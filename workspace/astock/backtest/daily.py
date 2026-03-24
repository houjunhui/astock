#!/usr/bin/env python3
"""
单日回测逻辑
模拟一天内的所有交易决策：竞价选股 → 盘中监控 → 收盘平仓

回测 vs 实盘的区别：
- 实盘：9:26竞价买，盘中监控，收盘14:57平
- 回测：用昨日ladder模拟选股，用今日收盘数据模拟各价格点
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
from collections import defaultdict
from astock.cache_manager import apply_cache  # 历史数据缓存
# quicktiny imports moved inside functions (after apply_cache) to ensure caching works
from concurrent.futures import ThreadPoolExecutor
import signal

def _get_kline_with_timeout(code, days=2, timeout_sec=3):
    """带超时的kline获取，防止挂起"""
    def _handler(signum, frame):
        raise TimeoutError(f"get_kline_hist timeout for {code}")
    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(timeout_sec)
    try:
        return get_kline_hist(code, days=days)
    except TimeoutError:
        return None  # 超时返回None，不阻塞
    except Exception:
        return None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
from astock.strategy_params import get_params
from astock.position.position_sqlite import init_db, add_position, close_position, get_db

CAPITAL = 1_000_000
COMMISSION = 0.0003   # 佣金0.03%
STAMP_TAX = 0.001     # 印花税0.1%（卖出）


def get_day_prices(code, date_str):
    """获取当日OHLC价格（线程超时保护，单日5秒上限）"""
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(get_kline_hist, code, 2)
            klines = future.result(timeout=5.0)
        if not klines:
            return None, None, None
        for k in klines:
            if str(k.get("date","")) == str(date_str):
                return float(k["high"]), float(k["low"]), float(k["close"])
        k = klines[-1]
        return float(k["high"]), float(k["low"]), float(k["close"])
    except Exception:
        return None, None, None


def get_close_price(code, date_str):
    h, l, c = get_day_prices(code, date_str)
    return c


def get_high_price(code, date_str):
    h, l, c = get_day_prices(code, date_str)
    return h


def get_low_price(code, date_str):
    h, l, c = get_day_prices(code, date_str)
    return l


def simulate_day(date_str, params=None, capital=CAPITAL, verbose=True):
    # 延迟导入quicktiny，确保apply_cache()的缓存包装先生效
    from astock.quicktiny import get_ladder, get_auction_for_codes, get_kline_hist

    # 确保新模块的缓存优先
    try:
        from astock.pools.emotion_adaptive import calc_emotion_adaptive
        emotion_score, phase, _ = calc_emotion_adaptive(yday)
        temp = emotion_score
    except Exception:
        pass  # 保持原有逻辑
    """
    模拟单日交易
    
    模拟逻辑（简化版）：
    - 用昨日ladder选股 → 竞价价格买入
    - 用今日最高/最低价判断是否触发止损/止盈/炸板
    - 用收盘价平仓（若未触发以上条件）
    
    返回: {
        "date": date_str,
        "buys": [...],
        "closes": [...],
        "day_pnl": float,
        "positions": [...],
    }
    """
    if params is None:
        params = get_params()

    # ── 1板开关（v2：默认关闭）──
    enable_1board = params.get("enable_1board", False)

    init_db()
    db = get_db()

    # ── 1. 获取昨日涨停池 ──
    yday = prev_trading_day(date_str)
    try:
        ladder_y = get_ladder(yday)
    except Exception:
        if verbose:
            print(f"[{date_str}] 获取ladder失败")
        return None

    if not ladder_y or not ladder_y.get("boards"):
        return None

    y_stocks = {}
    for b in ladder_y.get("boards", []):
        for s in b.get("stocks", []):
            y_stocks[s["code"]] = {
                "level": b.get("level", 1),
                "name": s.get("name", ""),
                "open_num": s.get("open_num"),
                "continue_num": s.get("continue_num", 1),
                "limit_up_type": s.get("limit_up_type", ""),
                "limit_up_suc_rate": s.get("limit_up_suc_rate"),
            }

    # ── 2. 获取今日竞价数据 ──
    try:
        ads = get_auction_for_codes(list(y_stocks.keys()), delay=0)
    except Exception:
        return None

    # ── 3. 情绪周期（board_tier判断前，先估算真实温度）──
    board_count = len(y_stocks)
    max_lb = max((s.get("level", 1) for s in y_stocks.values()), default=1)
    # 涨停池规模估算（回测专用，避免API超时）
    if board_count >= 15 and max_lb >= 5:
        temp, phase = 82, "主升"
    elif board_count >= 10 and max_lb >= 3:
        temp, phase = 68, "发酵"
    elif board_count >= 5:
        temp, phase = 48, "分歧"
    elif board_count >= 2:
        temp, phase = 30, "退潮"
    else:
        temp, phase = 15, "冰点"
    # board_tier过滤（退潮期拒绝3板+）
    try:
        from astock.pools.board_tier import can_open_position as bt_check
    except Exception:
        bt_check = None

    max_total_map = {
        "主升": params.get("max_total_main_sheng", 0.70),
        "发酵": 0.60,
        "分歧": 0.40,
        "退潮": 0.20,
        "冰点": 0.0,
    }
    max_total = max_total_map.get(phase, 0.40)

    # ── 4. 竞价选股（简化版回测）──
    candidates = []
    for code, ad in ads.items():
        yd = y_stocks.get(code, {})
        price = ad.get("price", 0)
        preClose = ad.get("preClose", 0)
        chg = (price / preClose - 1) * 100 if preClose and preClose > 0 else 0
        vr = ad.get("volumeRatio", 0)
        amount = ad.get("amount", 0)

        if chg <= 0 or not price or price <= 0:
            continue
        # amount=0或None时跳过金额过滤（历史竞价数据通常无amount字段）
        if amount and amount < params.get("auction_amount_min", 50_000_000):
            continue
        if vr < params.get("vol_ratio_min", 3.0):
            continue

        lb = yd.get("level", 1)
        if lb == 1 and not enable_1board:
            continue  # 1板暂停，跳过

        # ── 1板严格过滤（修复：亏钱主因）──
        if lb == 1:
            # 1板必须：情绪发酵期以上 + 竞价3-7% + VR≥5
            if phase not in ("主升", "发酵"):
                continue
            if not (3.0 <= chg <= 7.0):
                continue
            if vr < 5.0:
                continue
        elif lb == 2:
            # 2板必须：情绪分歧期以上 + 竞价2-9% + VR≥4
            if phase in ("冰点",):
                continue
            if not (2.0 <= chg <= 9.0):
                continue
            if vr < 4.0:
                continue

        # 仓位
        if lb >= 3:
            stock_cap = params.get("position_S", 0.30)
        elif lb == 2:
            stock_cap = params.get("position_A", 0.20)
        else:
            stock_cap = params.get("position_B", 0.15)

        suggest_pct = min(stock_cap, max_total)
        if suggest_pct < 0.05:
            continue

        suggest_amt = int(CAPITAL * suggest_pct / 100) * 100
        qty = suggest_amt // price // 100 * 100
        if qty < 100:
            continue

        # 目标价/止损价
        slip_price = round(price * 1.005, 2)  # 滑点+0.5%
        if lb >= 3:
            target = round(slip_price * params.get("target_3board_plus", 1.12), 2)
        elif lb == 2:
            target = round(slip_price * params.get("target_2board", 1.09), 2)
        else:
            target = round(slip_price * params.get("target_1board", 1.07), 2)

        stop_loss_pct = params.get("stop_loss_default", 0.04)
        stop_loss = round(slip_price * (1 - stop_loss_pct), 2)

        # ── 黑名单过滤（修复：防止顺灏式反复止损）──
        try:
            from pathlib import Path
            bl_file = Path("/home/gem/workspace/agent/workspace/astock/pools/blacklist.json")
            import json as _json
            if bl_file.exists():
                with open(bl_file) as _f:
                    _bl_data = _json.load(_f)
                if code in _bl_data:
                    continue  # 黑名单股跳过
        except Exception:
            pass

        candidates.append({
            "code": code, "name": yd.get("name", code),
            "lb": lb, "price": slip_price,
            "qty": qty, "target": target, "stop_loss": stop_loss,
            "auction_chg": chg,
        })

    candidates = sorted(candidates, key=lambda x: -x["lb"])

    # ── 5b. board_tier过滤（回测时跳过，避免新模块误杀历史）──
    # board_tier是v3.4新增，实盘启用，回测旧数据时应宽松
    BACKTEST_MODE = False  # 实盘模式
    try:
        from astock.pools.board_tier import can_open_position as bt_check
        if not BACKTEST_MODE:
            filtered = []
            for c in candidates:
                can, tn, rsn = bt_check(c.get("lb",1), c.get("auction_chg",0), phase)
                if can:
                    filtered.append(c)
            candidates = filtered[:3]
        else:
            candidates = candidates[:3]  # 直接取前3（排序后），不用board_tier过滤
    except Exception:
        candidates = candidates[:3]

    # ── 6. 模拟买入 ──
    day_buys = []
    for c in candidates:
        day_buys.append({
            "code": c["code"], "name": c["name"],
            "buy_price": c["price"], "qty": c["qty"],
            "target": c["target"], "stop_loss": c["stop_loss"],
            "level": c["lb"],
        })

    # ── 7. 模拟卖出 ──
    day_closes = []
    for buy in day_buys:
        code = buy["code"]
        buy_price = buy["buy_price"]
        qty = buy["qty"]
        stop_loss = buy["stop_loss"]
        target = buy["target"]
        level = buy["level"]

        close_price = get_close_price(code, date_str)
        high_price = get_high_price(code, date_str)
        low_price = get_low_price(code, date_str)

        if close_price is None:
            continue

        # 模拟卖出原因判断（按优先级）
        reason = "收盘平仓"
        actual_sell_price = close_price

        if low_price is not None and low_price <= stop_loss:
            reason = "止损"
            actual_sell_price = stop_loss
        elif high_price is not None and high_price >= target:
            reason = "目标价止盈"
            actual_sell_price = target
        elif level >= 3 and high_price is not None and close_price is not None:
            # 炸板检测：3板+ 最高价回落≥6%
            broken_pct = (high_price - close_price) / high_price if high_price > 0 else 0
            if broken_pct >= params.get("broken_limit_dragon", 0.06):
                reason = "炸板出局"
                actual_sell_price = close_price

        # 实际结算（含滑点-0.5%）
        sell_price_net = round(actual_sell_price * 0.995, 2)
        pnl = (sell_price_net - buy_price) * qty
        pnl -= buy_price * qty * COMMISSION  # 买入佣金
        pnl -= sell_price_net * qty * COMMISSION  # 卖出佣金
        if reason != "止损":
            pnl -= sell_price_net * qty * STAMP_TAX  # 印花税
        pnl_pct = pnl / (buy_price * qty) * 100

        day_closes.append({
            "code": code, "name": buy["name"],
            "buy_price": buy_price, "close_price": sell_price_net,
            "qty": qty, "level": level,
            "pnl": round(pnl, 0), "pnl_pct": round(pnl_pct, 2),
            "reason": reason,
            "buy_date": date_str,
        })

    day_pnl = sum(c["pnl"] for c in day_closes)

    if verbose:
        print(f"\n【{date_str}】{phase} | 买{len(day_buys)}只 | 平{len(day_closes)}只 | 日盈亏{day_pnl:+,.0f}元")
        for c in day_closes:
            icon = "✅" if c["pnl"] > 0 else "❌"
            print(f"  {icon} {c['name']}({c['code']}) {c['level']}板 {c['reason']} {c['pnl_pct']:+.1f}% {c['pnl']:+,.0f}元")

    return {
        "date": date_str,
        "phase": phase,
        "temperature": temp,
        "buys": day_buys,
        "closes": day_closes,
        "day_pnl": round(day_pnl, 0),
    }


def prev_trading_day(date_str):
    from datetime import datetime, timedelta
    d = datetime.strptime(str(date_str), "%Y%m%d")
    return (d - timedelta(days=1)).strftime("%Y%m%d")


def run_backtest_range(start_date, end_date, params=None, verbose=False):
    """运行区间回测"""
    from astock.backtest.runner import get_trade_dates, format_backtest_stats, format_backtest_report, is_valid_for_live
    from astock.strategy_params import get_params

    if params is None:
        params = get_params()

    # 启用历史数据缓存（首次运行自动填充，后续直接读缓存）
    apply_cache()

    dates = get_trade_dates(start_date, end_date)
    all_closes = []

    for date_str in dates:
        result = simulate_day(date_str, params=params, verbose=verbose)
        if result:
            all_closes.extend(result["closes"])

    stats = format_backtest_stats(all_closes)

    # 保存回测结果
    result_file = f"/tmp/backtest_{start_date}_{end_date}.json"
    with open(result_file, "w") as f:
        json.dump({"stats": stats, "trades": all_closes, "params": params}, f, indent=2, default=str)
    print(f"\n回测结果已保存: {result_file}")

    print(format_backtest_report(stats))
    print(f"\n通过第一阶段验证: {'✅ 是' if is_valid_for_live(stats) else '❌ 否'}")
    return stats


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20260301")
    parser.add_argument("--end", default="20260324")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    from astock.strategy_params import get_params
    params = get_params()
    run_backtest_range(args.start, args.end, params, verbose=args.verbose)
