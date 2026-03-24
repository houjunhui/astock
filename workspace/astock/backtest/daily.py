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

from collections import defaultdict
from astock.quicktiny import get_ladder, get_auction_for_codes, get_market_overview_fixed, get_minute
from astock.strategy_params import get_params
from astock.position.position_sqlite import init_db, add_position, close_position, get_db

CAPITAL = 1_000_000
COMMISSION = 0.0003   # 佣金0.03%
STAMP_TAX = 0.001     # 印花税0.1%（卖出）


def get_close_price(code, date_str):
    """获取收盘价（今日K线最后一根）"""
    try:
        mins = get_minute(code, ndays=2)
        if not mins:
            return None
        # 分钟数据最后一条是收盘价
        return float(mins[-1][4])
    except Exception:
        return None


def get_high_price(code, ndays=2):
    """获取期间最高价"""
    try:
        mins = get_minute(code, ndays=ndays)
        if not mins:
            return None
        return max(float(m[3]) for m in mins)
    except Exception:
        return None


def get_low_price(code, ndays=2):
    """获取期间最低价"""
    try:
        mins = get_minute(code, ndays=ndays)
        if not mins:
            return None
        return min(float(m[2]) for m in mins)
    except Exception:
        return None


def simulate_day(date_str, params=None, capital=CAPITAL, verbose=True):
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

    # ── 3. 情绪周期 ──
    try:
        mo = get_market_overview_fixed(date_str)
        temp = mo.get("market_temperature", 50)
    except Exception:
        temp = 50

    if temp >= 80:
        phase = "主升"
    elif temp >= 60:
        phase = "发酵"
    elif temp >= 40:
        phase = "分歧"
    elif temp >= 20:
        phase = "退潮"
    else:
        phase = "冰点"

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
        if amount < params.get("auction_amount_min", 50_000_000):
            continue
        if vr < params.get("vol_ratio_min", 3.0):
            continue

        lb = yd.get("level", 1)
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

        candidates.append({
            "code": code, "name": yd.get("name", code),
            "lb": lb, "price": slip_price,
            "qty": qty, "target": target, "stop_loss": stop_loss,
            "auction_chg": chg,
        })

    # 最多3只
    candidates = sorted(candidates, key=lambda x: -x["lb"])[:3]

    # ── 5. 模拟买入 ──
    day_buys = []
    for c in candidates:
        # 记录（不真正写入，避免干扰）
        day_buys.append({
            "code": c["code"], "name": c["name"],
            "buy_price": c["price"], "qty": c["qty"],
            "target": c["target"], "stop_loss": c["stop_loss"],
            "level": c["lb"],
        })

    # ── 6. 模拟卖出 ──
    day_closes = []
    for buy in day_buys:
        code = buy["code"]
        buy_price = buy["buy_price"]
        qty = buy["qty"]
        stop_loss = buy["stop_loss"]
        target = buy["target"]
        level = buy["level"]

        close_price = get_close_price(code, date_str)
        high_price = get_high_price(code, ndays=2)
        low_price = get_low_price(code, ndays=2)

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
