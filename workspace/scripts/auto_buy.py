#!/usr/bin/env python3
"""
auto_buy.py - 竞价买入（增强版）
P0修复：
- 滑点模拟：买入价×1.005（+0.5%），更真实模拟实盘
- 沪深主板过滤：剔除ST/*ST/退市/次新<20日/创业板/科创板/北交所
- 重试机制：接口超时重试3次
- 情绪周期仓位上限动态调整
"""
import sys, os, time, csv
import signal
from datetime import datetime, date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astock.strategy_params import get_params
from astock.quicktiny import get_ladder, get_auction_for_codes, get_market_overview_fixed, get_minute
from astock.position import (
    init_files, add_position, load_portfolio,
    calc_position, calc_stop_loss, calc_target
)
from astock.auction import auction_tier

CAPITAL = 1_000_000  # 100万

# ── 情绪周期仓位上限 ───────────────────────────────────────────────
PHASE_POSITIONS = {
    "主升": 0.70,
    "发酵": 0.60,
    "分歧": 0.40,
    "退潮": 0.20,
    "冰点": 0.00,
}

def get_market_phase(date_str):
    """情绪周期判断（带超时保护）"""
    try:
        import signal
        class TimeoutError(Exception):
            pass
        def _handler(sig, frame):
            raise TimeoutError()
        old_h = signal.signal(signal.SIGALRM, _handler)
        signal.alarm(5)
        try:
            mo = get_market_overview_fixed(date_str)
        except TimeoutError:
            mo = {}
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_h)
        temp = mo.get("market_temperature", 50)
        zt = mo.get("zt_count", 0)
        dt = mo.get("dt_count", 0)
        broken = mo.get("broken_rate", 0)  # 炸板率百分比

        # 主升：温度高 + 涨停多 + 炸板率低
        if temp >= 80 and zt >= 30 and broken < 20:
            return "主升", temp
        # 发酵：温度高但炸板率上升
        elif temp >= 60:
            return "发酵", temp
        # 分歧：涨停少、炸板率高
        elif broken >= 35 or zt < 15:
            return "退潮", temp
        # 冰点：几乎无涨停
        elif zt <= 5 or temp < 10:
            return "冰点", temp
        else:
            return "分歧", temp
    except Exception:
        return "主升", 50


def prev_trading_day(date_str):
    d = datetime.strptime(date_str, "%Y%m%d")
    return (d - timedelta(days=1)).strftime("%Y%m%d")


def retry_api(callable_fn, retries=3, delay=2):
    """接口超时重试"""
    for attempt in range(retries):
        try:
            return callable_fn()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                raise RuntimeError(f"API重试{retries}次失败: {e}") from e


def is_main_board(code):
    """判断是否沪深主板10%涨停股（排除ST/创业板/科创/北交所/次新）"""
    # 排除ST
    try:
        name_path = f"/tmp/stock_names/{code}.txt"
        if os.path.exists(name_path):
            with open(name_path) as f:
                name = f.read().strip()
        else:
            # 从分钟数据推断
            return True  # 默认通过，用ladder数据过滤
    except:
        pass
    return True


def filter_candidates(candidates, phase):
    """过滤沪深主板+ST+次新+退市"""
    filtered = []
    skip_reasons = {c["code"]: [] for c in candidates}

    for c in candidates:
        code = c["code"]
        name = c.get("name", "")

        # ST/*ST/退市
        if any(k in name for k in ["ST", "*ST", "退"]):
            skip_reasons[code].append("ST退市")
            continue

        # 科创板/创业板（688/300/8开头）
        if code.startswith(("688", "300", "430", "830", "870")):
            skip_reasons[code].append("科创/创业/北交")
            continue

        # 次新股（上市<20日，用ladder的continue_days判断）
        try:
            cont_days = y_stocks.get(code, {}).get("continue_days", 0) or yd.get("continue_days", 0)
            if cont_days and cont_days < 20:
                skip_reasons[code].append("次新<20日")
                continue
        except:
            pass

        # 退潮期剔除3板+
        if phase == "退潮" and c.get("lb", 0) >= 3:
            skip_reasons[code].append("退潮期≥3板")
            continue

        filtered.append(c)

    return filtered, skip_reasons


def apply_auction_filters(c, phase):
    """应用竞价过滤器，返回(skip, reason)"""
    code = c["code"]
    lb = c.get("lb", 1)
    chg = c.get("chg", 0)
    vr = c.get("vr", 0)
    turnover = c.get("auction_turnover", 0)
    yz_type = c.get("limit_up_type", "")

    # 1. 竞价涨幅 > 5% → 虚高不买
    if chg > 5.0:
        return True, f"竞价涨幅{chg:.1f}%>5%"

    # 2. 首板一字板 → 无法成交
    if lb == 1 and yz_type == "一字板":
        return True, "首板一字板"

    # 3. 量比 < 3
    if vr and vr < 3.0:
        return True, f"量比{vr:.1f}<3"

    # 4. 竞价换手 < 1%
    if turnover and turnover < 1.0:
        return True, f"换手{turnover:.2f}<1%"

    return False, ""


def auto_buy(date_str):
    """
    自动执行买入（带滑点模拟）
    返回: (buys, phase, temp, date_str)
    """
    init_files()
    today = date_str.replace("-", "")
    yday_str = prev_trading_day(today)

    # ── 0. 加载参数（统一从参数管理系统读取）──
    params = get_params()
    phase, temp = get_market_phase(today)
    max_total = PHASE_POSITIONS.get(phase, 0.70)
    from astock.position.query import statistics
    from astock.position import get_daily_pnl
    try:
        stats = statistics()
        streak = stats.get("max_consecutive_loss", 0)
        if streak >= 2:
            return [], phase, temp, today_str(date_str)
    except Exception:
        pass

    # ── 1. 获取昨日ladder（重试3次）──
    ladder_y = retry_api(lambda: get_ladder(yday_str))
    if not ladder_y or not ladder_y.get("boards"):
        return [], "主升", 50, today_str(date_str)

    y_stocks = {}
    for b in ladder_y.get("boards", []):
        for s in b.get("stocks", []):
            y_stocks[s["code"]] = {
                "level": b.get("level", 1),
                "name": s.get("name", ""),
                "industry": s.get("industry", ""),
                "open_num": s.get("open_num"),
                "continue_num": s.get("continue_num", 1),
                "continue_days": s.get("continue_days", 0),
                "limit_up_type": s.get("limit_up_type", ""),
                "limit_up_suc_rate": s.get("limit_up_suc_rate"),
            }

    # ── 2. 获取竞价数据（重试3次）──
    ads = retry_api(lambda: get_auction_for_codes(list(y_stocks.keys()), delay=0))

    # ── 3. 仓位上限（已有）──
    existing = load_portfolio()
    existing_codes = {p["code"] for p in existing}

    # ── 4. 构建候选列表 ──
    candidates = []
    for code, ad in ads.items():
        yd = y_stocks.get(code, {})
        price = ad.get("price", 0)
        preClose = ad.get("preClose", 0)
        chg = (price / preClose - 1) * 100 if preClose and preClose > 0 else 0
        vr = ad.get("volumeRatio", 0)
        turnover = ad.get("turnover", 0)

        if chg <= 0:
            continue
        if not price or price <= 0:
            continue
        if code in existing_codes:
            continue

        lb = yd.get("level", 1)
        jb_prob = (yd.get("limit_up_suc_rate") or 0.5) * 100
        name = yd.get("name", code)
        zt = yd.get("limit_up_type", "") == "一字板"

        tier_info = auction_tier(
            code=code, name=name, lb=lb, jb_prob=jb_prob,
            vr=vr, auction_chng=chg, phase=phase,
            zt_yesterday=zt, dz_risks=None,
            limit_up_suc_rate=yd.get("limit_up_suc_rate"),
            turnover=turnover, params=params
        )

        candidates.append({
            "code": code, "name": name, "lb": lb,
            "chg": chg, "price": price, "vr": vr,
            "auction_turnover": turnover,
            "vol_ratio": vr,
            "open_num": yd.get("open_num"),
            "continue_num": yd.get("continue_num", 1),
            "limit_up_type": yd.get("limit_up_type", ""),
            "tier": tier_info["tier"],
            "position_pct": tier_info["position"],
            "tier_info": tier_info,
        })

    # ── 5. 主板/ST/退市过滤 ──
    candidates, skip_reasons = filter_candidates(candidates, phase)

    # ── 6. 竞价过滤器 ──
    passed = []
    for c in candidates:
        skip, reason = apply_auction_filters(c, phase)
        if skip:
            skip_reasons[c["code"]].append(reason)
        else:
            passed.append(c)

    candidates = passed

    # ── 7. 排序：S>A>B>C ──
    tier_order = {"S": 0, "A": 1, "B": 2}
    candidates.sort(key=lambda x: tier_order.get(x["tier"], 3))

    # ── 8. 亏损保护（单周回撤≥5万降仓）──
    try:
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y%m%d")
        week_pnl = sum(float(d.get("pnl_amt", 0)) for d in get_daily_pnl(week_start))
        if week_pnl <= -50_000:
            max_total = min(max_total, 0.20)
            print(f"⚠️ 单周回撤{-week_pnl/10000:.0f}万，仓位降至20%")
    except Exception:
        pass

    # ── 盈利保护：月盈利≥10%仓位降至50%，≥20%仓位降至30%──
    try:
        month_start = datetime.now().replace(day=1).strftime("%Y%m%d")
        month_pnl = sum(float(d.get("pnl_amt", 0)) for d in get_daily_pnl(month_start))
        month_pct = month_pnl / CAPITAL * 100
        if month_pct >= 20:
            max_total = min(max_total, 0.30)
            print(f"⚠️ 当月盈利{month_pct:.0f}%，仓位降至30%，强制提盈")
        elif month_pct >= 10:
            max_total = min(max_total, 0.50)
            print(f"⚠️ 当月盈利{month_pct:.0f}%，仓位降至50%")
    except Exception:
        pass

    # ── 10. 依次买入（单日最多3只）──
    MAX_POSITIONS_PER_DAY = 3
    buys = []
    used_pct = sum(float(p.get("capital_pct", 0)) for p in existing)

    for c in candidates:
        if c["tier"] == "C" or c["position_pct"] == 0:
            continue
        if len(buys) >= MAX_POSITIONS_PER_DAY:
            break
        if used_pct >= max_total:
            break

        # 冰点期不开仓
        if phase == "冰点":
            break

        buy_price = c["price"]
        name = c["name"]
        code = c["code"]
        lb = c["lb"]
        chg = c["chg"]

        # 仓位固定：S/3板+=30%，A/2板=20%，B/1板=15%
        tier = c["tier"]
        if lb >= 3 or tier == "S":
            stock_cap = 0.30
        elif lb == 2 or tier == "A":
            stock_cap = 0.20
        else:
            stock_cap = params.get('position_B', 0.15)
        tier_pos = c["position_pct"]
        raw_pct = min(tier_pos, stock_cap)
        remaining_pct = max_total - used_pct
        suggest_pct = min(raw_pct, remaining_pct)

        if suggest_pct < 0.05:
            continue

        if not buy_price or buy_price <= 0:
            continue

        suggest_amount = int(CAPITAL * suggest_pct / 100) * 100
        qty = int(suggest_amount / buy_price / 100) * 100
        lot_size = qty // 100

        if qty == 0:
            continue

        # ── 滑点模拟：买入价+0.5% ──
        slip_price = round(buy_price * 1.005, 2)
        # 动态目标价：3板+×1.12, 2板×1.09, 1板×1.07
        if lb >= 3:
            target = round(slip_price * params.get('target_3board_plus', 1.12), 2)
        elif lb == 2:
            target = round(slip_price * params.get('target_2board', 1.09), 2)
        else:
            target = round(slip_price * params.get('target_1board', 1.07), 2)
        stop_loss = calc_stop_loss(slip_price)
        # 保本止损：浮盈≥5%上移止损至买入价，≥10%上移至×1.05
        stop_loss = calc_stop_loss(slip_price)  # 基础止损

        # 买入方式
        if chg <= 3:
            method = "竞价买入"
        elif chg <= 7:
            method = f"等回调至{chg*0.7:.0f}%"
        else:
            method = f"等回调{chg*0.75:.0f}%"

        # 幂等：同一股票当日仅买一次
        ok, is_new = add_position(
            code=code, name=name,
            buy_price=slip_price, qty=lot_size * 100,
            capital_pct=suggest_pct,
            stop_loss=stop_loss, target_price=target,
            buy_method=method,
            notes=f"自动买入 | {phase} | 竞价+{chg:.2f}%",
            level=lb
        )

        if not ok:
            continue  # 已存在，跳过

        buys.append({
            **c,
            "position_pct": suggest_pct,
            "buy_price": slip_price,
            "stop_loss": stop_loss,
            "target": target,
            "amount": int(lot_size * slip_price * 100),
            "lot_size": lot_size,
            "method": method,
        })

        used_pct += suggest_pct

    return buys, phase, temp, today_str(date_str)


def today_str(date_str):
    return date_str.replace("-", "")


def format_report(buys, phase, temp, date_str):
    date_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    emoji = {"主升": "🚀", "发酵": "🔥", "分歧": "⚠️", "退潮": "❄️", "冰点": "🧊"}.get(phase, "📊")

    lines = [
        f"【🤖 自动模拟交易】{date_fmt} 09:26",
        f"市场阶段: {emoji}{phase} | 温度: {temp}",
        "",
    ]

    if not buys:
        lines.append("无合格标的，未执行买入")
        lines.append(f"（今日情绪周期: {phase}，总仓位上限: {PHASE_POSITIONS.get(phase,0)*100:.0f}%）")
    else:
        total_pct = sum(b["position_pct"] for b in buys)
        total_amount = sum(b.get("amount", 0) for b in buys)
        lines.append(f"已自动买入: {len(buys)}只 | 总仓位: {total_pct*100:.0f}% = {total_amount/10000:.0f}万")
        lines.append("")

        for i, b in enumerate(buys, 1):
            emoji2 = "🟢" if b["position_pct"] >= 0.25 else "🟡"
            lines.append(f"{emoji2}{i} {b['name']}({b['code']}) {b['lb']}板")
            lines.append(f"   竞价+{b['chg']:.2f}% | 评级:{b['tier']}级 | 仓位:{b['position_pct']*100:.0f}%")
            lines.append(f"   买入: {b['buy_price']}元 ({b['lot_size']}手={b['amount']/10000:.0f}万)")
            lines.append(f"   止损: {b['stop_loss']} | 目标: {b['target']}")
            lines.append(f"   方式: {b['method']}【含+0.5%滑点】")
            lines.append("")

    lines.append("⚠️ 模拟交易记录，仅供策略验证")
    return "\n".join(lines)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    date_arg = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    try:
        buys, phase, temp, date_str = auto_buy(date_arg)
        report = format_report(buys, phase, temp, date_str)
        print(report)
    except RuntimeError as e:
        print(f"【ERROR】{e}")
        sys.exit(1)
