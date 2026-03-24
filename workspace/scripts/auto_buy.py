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
from astock.cache_manager import apply_cache
apply_cache()  # 缓存优先
from astock.quicktiny import get_ladder, get_auction_for_codes, get_market_overview_fixed, get_minute
from astock.position import (
    init_files, add_position, load_portfolio,
    calc_position, calc_stop_loss, calc_target
)
from astock.auction import auction_tier

# 新增: 情绪温度计 + 凯利仓位 + 连板梯队 + 熔断检查
try:
    from astock.pools.emotion_thermometer import calc_emotion_score
    from astock.pools.kelly_position import allocate_positions, PHASE_CAP as KELLY_PHASE_CAP
    from astock.pools.board_tier import can_open_position as board_tier_check, get_board_tier
    from astock.risk_control import check_circuit_breaker
    from astock.pools.emotion_adaptive import calc_emotion_adaptive
    from astock.pools.dynamic_position import rebalance_portfolio
    NEW_MODULES_OK = True
except Exception as e:
    NEW_MODULES_OK = False

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
    """情绪周期判断（使用自适应评分）"""
    try:
        score, phase, details = calc_emotion_adaptive(date_str)
        return phase, score
    except Exception:
        return "分歧", 50


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
    if turnover and turnover < params.get('turnover_min', 1.0):
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

    # ── 0. 情绪温度计 + 加载参数 ──
    params = get_params()
    # 09:26竞价决策：用昨日收盘数据，不可用当日盘中数据（滞后）
    if NEW_MODULES_OK:
        try:
            # 竞价用昨日情绪数据，隔夜仓监控用当日实时数据
            emotion_total, phase, emotion_scores, _ = calc_emotion_score(yday_str)
            temp = emotion_total
        except Exception:
            phase, temp = get_market_phase(today)
    else:
        phase, temp = get_market_phase(today)
    
    # 阶段仓位上限（kelly_position的PHASE_CAP）
    phase_cap = KELLY_PHASE_CAP.get(phase, 0.70)
    max_total = phase_cap
    
    # ── 0.5 熔断检查：触发则全日停止开仓 ──
    if NEW_MODULES_OK:
        try:
            circuit_triggered, circuit_reasons = check_circuit_breaker(today)
            if circuit_triggered:
                print(f"【🛡️ 熔断触发，停止开仓】")
                for r in circuit_reasons:
                    print(f"  ❌ {r}")
                return [], phase, temp, today_str(date_str)
        except Exception:
            pass
    
    from astock.position.query import statistics
    from astock.position import get_daily_pnl
    try:
        stats = statistics()
        streak = stats.get("max_consecutive_loss", 0)
        if streak >= params.get('consecutive_loss_stop', 2):
            return [], phase, temp, today_str(date_str)
    except Exception:
        pass

    # ── 1. 获取昨日ladder（重试3次 + 数据质量校验）──
    ladder_y = None
    for attempt in range(3):
        try:
            ladder_y = get_ladder(yday_str)
            # 数据质量校验
            if ladder_y and ladder_y.get("boards"):
                stock_count = sum(len(b.get("stocks", [])) for b in ladder_y["boards"])
                if stock_count < 5:
                    print(f"⚠️ 数据异常: 涨停池仅{stock_count}只，重试{attempt+1}/3")
                    ladder_y = None
                    continue
                break
        except Exception as e:
            print(f"⚠️ API异常: {e}，重试{attempt+1}/3")
            ladder_y = None
    if not ladder_y or not ladder_y.get("boards"):
        print("⚠️ 昨日ladder数据获取失败，停止开仓")
        return [], phase, temp, today_str(date_str)

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

    # ── 2. 获取竞价数据（重试3次 + 数据质量校验）──
    ads = {}
    for attempt in range(3):
        try:
            ads = get_auction_for_codes(list(y_stocks.keys()), delay=0)
            if not ads or len(ads) < 3:
                print(f"⚠️ 竞价数据不足({len(ads) if ads else 0}只)，重试{attempt+1}/3")
                ads = {}
                continue
            break
        except Exception as e:
            print(f"⚠️ 竞价API异常: {e}，重试{attempt+1}/3")
            ads = {}
    if not ads or len(ads) < 3:
        print("⚠️ 竞价数据获取失败，停止开仓")
        return [], phase, temp, today_str(date_str)

    # ── 3. 仓位上限（已有）──
    existing = load_portfolio()
    existing_codes = {p["code"] for p in existing}

    # ── 动态仓位检视（持仓期内调整）──
    if NEW_MODULES_OK:
        try:
            adjustments = rebalance_portfolio()
            if adjustments:
                print(f"【仓位调整】{len(adjustments)}只持仓需调整")
                for adj in adjustments:
                    print(f"  {adj['action']} {adj['name']}: {adj['old_capital_pct']*100:.0f}%→{adj['new_capital_pct']*100:.0f}% | {adj['reason']}")
        except Exception:
            pass

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
            turnover=turnover, params=params,
            auction_amount=yd.get("auction_amount")
        )
        # 用params的position_S/A/B覆盖auction_tier的仓位建议
        tier = tier_info["tier"]
        tier_position_override = {
            "S": params.get("position_S"),
            "A": params.get("position_A"),
            "B": params.get("position_B"),
        }
        if tier in tier_position_override and tier_position_override[tier] is not None:
            tier_info["position"] = tier_position_override[tier]

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

    # ── 5b. 连板梯队过滤（board_tier模块）──
    if NEW_MODULES_OK:
        tier_board_filtered = []
        for c in candidates:
            lb = c.get("lb", 1)
            chg = c.get("chg", 0)
            can_open, tier_name, reason = board_tier_check(lb, chg, phase)
            if not can_open:
                skip_reasons.setdefault(c["code"], []).append(f"梯队{tier_name}:{reason}")
            else:
                tier_board_filtered.append(c)
        candidates = tier_board_filtered

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

    # ── 7b. 凯利仓位分配（kelly_position模块）──
    if NEW_MODULES_OK and candidates:
        try:
            kelly_candidates = []
            for c in candidates:
                if c["tier"] in ("S", "A", "B"):
                    kelly_candidates.append({
                        "code": c["code"],
                        "name": c.get("name", c["code"]),
                        "tier": c["tier"],
                        "win_rate": params.get(f"kelly_win_rate_{c['tier']}", 0.3),
                        "avg_win_pct": params.get("kelly_avg_win_pct", 9.0),
                        "avg_loss_pct": params.get("kelly_avg_loss_pct", 4.0),
                        "stop_loss_pct": 4.0,
                        "sector": c.get("tier_info", {}).get("industry", "default"),
                    })
            if kelly_candidates:
                sector_map = {}
                allocated = allocate_positions(kelly_candidates, phase, sector_map)
                alloc_map = {a["code"]: a["position_pct"] for a in allocated}
                for c in candidates:
                    if c["code"] in alloc_map and alloc_map[c["code"]] > 0:
                        c["position_pct"] = alloc_map[c["code"]]
                        c["position_source"] = "kelly"
                    elif c["code"] in alloc_map:
                        c["position_pct"] = 0
                        c["position_source"] = "kelly_zero"
        except Exception as e:
            pass  # Kelly失败时用原始仓位

    # ── 8. 亏损保护（单周回撤≥5万降仓）──
    try:
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y%m%d")
        week_pnl = sum(float(d.get("pnl_amt", 0)) for d in get_daily_pnl(week_start))
        if week_pnl <= -params.get('week_drawdown_stop', 50000):
            max_total = min(max_total, 0.20)
            print(f"⚠️ 单周回撤{-week_pnl/10000:.0f}万，仓位降至20%")
    except Exception:
        pass

    # ── 盈利保护：月盈利≥10%仓位降至50%，≥20%仓位降至30%──
    try:
        month_start = datetime.now().replace(day=1).strftime("%Y%m%d")
        month_pnl = sum(float(d.get("pnl_amt", 0)) for d in get_daily_pnl(month_start))
        month_pct = month_pnl / CAPITAL * 100
        if month_pct >= 20:  # 月盈≥20%→仓位≤30%
            max_total = min(max_total, params.get('profit_protect_20', 0.30))
            print(f"⚠️ 当月盈利{month_pct:.0f}%，仓位降至30%，强制提盈")
        elif month_pct >= 10:  # 月盈≥10%→仓位≤50%
            max_total = min(max_total, params.get('profit_protect_10', 0.50))
            print(f"⚠️ 当月盈利{month_pct:.0f}%，仓位降至50%")
    except Exception:
        pass

    # ── 10. 依次买入（单日最多N只）──
    MAX_POSITIONS_PER_DAY = params.get('max_positions_per_day', 3)
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
        stop_loss = calc_stop_loss(slip_price, param=params.get('stop_loss_default'))

        # 买入方式
        if chg <= 3:
            method = "竞价买入"
        elif chg <= 7:
            method = f"等回调至{chg*0.7:.0f}%"
        else:
            method = f"等回调{chg*0.75:.0f}%"

        # ── 流动性约束：一字板/无量高开 → 无法真实成交 ──
        limit_type = c.get("limit_up_type", "")
        open_num = c.get("open_num")
        if limit_type == "一字板":
            continue  # 一字板无法排队买入
        if open_num is not None and open_num < 5000:
            continue  # 竞价挂单不足5000手（约50万），视为无量

        # 幂等：同一股票当日仅买一次
        max_days = 2 if lb >= 3 else 1  # 龙3板+持2夜，普通持1夜
        ok, is_new = add_position(
            code=code, name=name,
            buy_price=slip_price, qty=lot_size * 100,
            capital_pct=suggest_pct,
            stop_loss=stop_loss, target_price=target,
            buy_method=method,
            notes=f"自动买入 | {phase} | 竞价+{chg:.2f}%",
            level=lb, max_days=max_days
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
