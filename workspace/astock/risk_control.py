"""
全链路风控体系 v1

两套独立体系:
  隔夜仓(可卖): 7层风控 tick级事件驱动
  当日新仓(不可卖): 仅风险标记，次日竞价优先平

熔断机制:
  - 上证/创业板单日跌超3%
  - 跌停家数>30
  - 连板高度≤2板
  - 昨日涨停均收益<-3%
触发 → 全仓平仓 + 当日停止开仓
"""

import sys
sys.path.insert(0, '/home/gem/workspace/agent/workspace')

from astock.cache_manager import apply_cache
apply_cache()  # 缓存优先

from astock.quicktiny import (
    get_limit_stats, get_market_overview_fixed,
    get_ladder, get_broken_limit_up
)

CAPITAL = 1_000_000


# ── 熔断阈值 ──────────────────────────────────────────────────────
CIRCUIT_BREAKER = {
    "index_drop": 3.0,       # 指数单日跌超3%
    "limit_down_count": 30,  # 跌停超30家
    "max_lb_circuit": 2,     # 连板高度≤2板
    "yesterday_zt_return": -3.0,  # 昨日涨停均收益<-3%
}


def check_circuit_breaker(date_str):
    """
    检查是否触发熔断
    
    返回: (triggered: bool, reasons: list)
    """
    triggers = []
    
    try:
        overview = get_market_overview_fixed(date_str)
        if isinstance(overview, str): overview = {}
        
        # 1. 指数下跌
        index_chg = overview.get("sh_index_chg", 0) or 0
        if index_chg <= -CIRCUIT_BREAKER["index_drop"]:
            triggers.append(f"大盘下跌{index_chg:.2f}%>{CIRCUIT_BREAKER['index_drop']}%")
        
        # 2. 跌停家数
        limit_down = overview.get("limit_down_count", 0)
        if limit_down > CIRCUIT_BREAKER["limit_down_count"]:
            triggers.append(f"跌停{limit_down}家>{CIRCUIT_BREAKER['limit_down_count']}家")
        
        # 3. 连板高度
        ladder = get_ladder(date_str)
        if not isinstance(ladder, list): ladder = []
        if ladder:
            max_lb = max(
                s.get("lb", s.get("continue_num", 1))
                for s in ladder
            )
            if max_lb <= CIRCUIT_BREAKER["max_lb_circuit"]:
                triggers.append(f"连板高度{max_lb}板≤{CIRCUIT_BREAKER['max_lb_circuit']}板")
        
        # 4. 昨日涨停均收益
        yz_return = overview.get("yesterday_limit_up_avg_pcp", 0) or 0
        if yz_return <= CIRCUIT_BREAKER["yesterday_zt_return"]:
            triggers.append(f"昨日涨停均{yz_return:.2f}%<{CIRCUIT_BREAKER['yesterday_zt_return']}%")
        
    except Exception as e:
        triggers.append(f"熔断检查异常: {e}")
    
    return len(triggers) > 0, triggers


def format_circuit_report(date_str):
    """生成熔断检查报告"""
    triggered, reasons = check_circuit_breaker(date_str)
    
    lines = [f"【⚡ 熔断机制检查】{date_str}"]
    if triggered:
        lines.append("🔴 **熔断已触发**")
        for r in reasons:
            lines.append(f"  ❌ {r}")
    else:
        lines.append("🟢 熔断未触发，市场正常")
    
    return "\n".join(lines)


# ── 隔夜仓7层风控 ─────────────────────────────────────────────────
# 优先级: 熔断→竞价低开→炸板→保本止损→渐进浮亏→目标价→动态止盈
RISK_PRIORITY = [
    "熔断平仓",
    "竞价低开",
    "炸板回落",
    "保本止损",
    "渐进浮亏",
    "目标止盈",
    "动态止盈",
]


def risk_check_overnight(position, date_str):
    """
    隔夜仓(可卖) 7层风控检查
    
    position: 持仓dict，包含:
        code, name, buy_price, qty, stop_loss, target_price,
        peak_price, level, buy_date
    
    返回: (action: str, reason: str, close_price: float)
        action=None → 无需操作
    """
    from astock.position import get_current_price
    
    code = position["code"]
    buy_price = float(position["buy_price"])
    qty = int(position["qty"])
    stop_loss = float(position.get("stop_loss", 0))
    target_price = float(position.get("target_price", 0))
    peak_price = float(position.get("peak_price", buy_price))
    lb = int(position.get("level", position.get("lb", 1)))
    
    cur = get_current_price(code)
    if not cur or cur <= 0:
        cur = buy_price
    
    # ── R1: 熔断 ───────────────────────────────────────────
    triggered, _ = check_circuit_breaker(date_str)
    if triggered:
        close_price = round(cur * 0.995, 2)  # 滑点
        return "熔断平仓", f"熔断触发，强制平仓", close_price
    
    # ── R2: 竞价低开（有条件平仓，非绝对）────────────────
    # 低开>3%但竞价量能充足（>昨日50%）：视为弱转强，保留观察
    # 低开>3%且量能不足：竞价直接平
    auction_chg = position.get("auction_chg", 0)
    auction_amount = position.get("auction_amount", 0)
    yesterday_amount = position.get("yesterday_amount", 0)
    
    if auction_chg < -3.0:
        # 有量能承接：弱转强信号，暂不平仓
        if yesterday_amount > 0 and auction_amount >= yesterday_amount * 0.5:
            return None, "", 0.0  # 保留，继续观察
        # 无量能承接：平仓
        close_price = round(cur * 0.995, 2)
        return "竞价低开", f"竞价低开{auction_chg:.1f}%+量能不足，竞价平仓", close_price
    
    # ── R3: 炸板回落 ──────────────────────────────────────
    # 今日涨停后炸板
    today_limit_up = position.get("today_limit_up", False)
    if today_limit_up and cur < buy_price * 1.09:  # 曾涨停但现在回落
        broken_pct = (peak_price - cur) / peak_price * 100
        if lb >= 3:
            threshold = 6.0
        else:
            threshold = 4.0
        if broken_pct >= threshold:
            close_price = round(cur * 0.995, 2)
            return "炸板回落", f"炸板回落{broken_pct:.1f}%>{threshold}%阈值，强制平仓", close_price
    
    # ── R4a: 初始止损线（任何持仓全程生效，独立于保本止损）──
    # 持仓只要下跌超过止损线，立即触发，不依赖浮盈条件
    stop_loss_pct = position.get("stop_loss_pct", 0.04)  # 默认4%
    if stop_loss_pct > 0:
        loss_from_buy = (buy_price - cur) / buy_price * 100
        if loss_from_buy >= stop_loss_pct * 100:  # 亏损超过止损线%
            close_price = round(cur * 0.995, 2)
            return "初始止损", f"初始止损触发，亏损{loss_from_buy:.1f}%≥{stop_loss_pct*100:.0f}%阈值", close_price

    # ── R4: 保本止损 ──────────────────────────────────────
    profit_pct = (cur - buy_price) / buy_price * 100
    if profit_pct >= 20:
        new_stop = buy_price * 1.15  # 锁定15%
        if cur <= new_stop:
            close_price = round(new_stop * 0.995, 2)
            return "保本止损", f"浮盈≥20%→锁定15%利润({new_stop:.2f})", close_price
    elif profit_pct >= 10:
        new_stop = buy_price * 1.05  # 锁定5%
        if cur <= new_stop:
            close_price = round(new_stop * 0.995, 2)
            return "保本止损", f"浮盈≥10%→锁定5%利润({new_stop:.2f})", close_price
    elif profit_pct >= 5:
        if cur <= buy_price:
            close_price = round(buy_price * 0.995, 2)
            return "保本止损", f"浮盈≥5%→保本止损线({buy_price:.2f})", close_price
    
    # ── R5: 渐进浮亏 ──────────────────────────────────────
    if profit_pct <= -4.0:
        close_price = round(cur * 0.995, 2)
        return "渐进浮亏", f"浮亏{profit_pct:.1f}%≤-4%，全仓止损", close_price
    elif profit_pct <= -2.0:
        # 降半仓（需外部reduce_position配合）
        close_price = round(cur * 0.995, 2)
        return "渐进浮亏", f"浮亏{profit_pct:.1f}%≤-2%，建议降半仓", close_price
    
    # ── R6: 目标价止盈 ─────────────────────────────────────
    if target_price > 0 and cur >= target_price:
        close_price = round(target_price * 0.995, 2)
        return "目标止盈", f"触及目标价{target_price:.2f}，止盈平仓", close_price
    
    # ── R7: 动态止盈 ──────────────────────────────────────
    if peak_price > buy_price:
        retrace_pct = (peak_price - cur) / peak_price * 100
        remaining_profit = profit_pct
        if retrace_pct >= 4.0 and remaining_profit >= 6.0:
            close_price = round(cur * 0.995, 2)
            return "动态止盈", f"高点回落{retrace_pct:.1f}%+浮盈{remaining_profit:.1f}%，止盈", close_price
    
    return None, "", 0.0


def risk_tag_today_new(position, date_str):
    """
    当日新仓(不可卖) 风险标记
    
    返回: (risk_level: str, reasons: list)
        risk_level: "extreme"/"high"/"medium"/"low"
    """
    from astock.position import get_current_price
    from astock.quicktiny import get_market_overview_fixed
    
    code = position["code"]
    buy_price = float(position["buy_price"])
    cur = get_current_price(code)
    if not cur or cur <= 0:
        cur = buy_price
    
    profit_pct = (cur - buy_price) / buy_price * 100
    risk_reasons = []
    
    # 高风险: 炸板>8%
    today_limit_up = position.get("today_limit_up", False)
    if today_limit_up and profit_pct < -8.0:
        risk_reasons.append(f"当日炸板回落{abs(profit_pct):.1f}%>8%")
    
    # 高风险: 当日浮亏>5%
    if profit_pct < -5.0:
        risk_reasons.append(f"当日浮亏{profit_pct:.1f}%>5%")
    
    # 高风险: 板块跳水>3%
    try:
        overview = get_market_overview_fixed(date_str)
        if isinstance(overview, str): overview = {}
        sector_drop = position.get("sector_chg", 0)
        if sector_drop <= -3.0:
            risk_reasons.append(f"板块跳水{sector_drop:.1f}%>3%")
    except Exception:
        pass
    
    # 高风险: 熔断触发
    triggered, _ = check_circuit_breaker(date_str)
    if triggered:
        risk_reasons.append("大盘熔断触发")
    
    # 评级
    if len(risk_reasons) >= 2:
        return "extreme", risk_reasons
    elif len(risk_reasons) == 1:
        return "high", risk_reasons
    elif profit_pct < 0:
        return "medium", [f"当日浮亏{profit_pct:.1f}%"]
    else:
        return "low", [f"当日浮盈{profit_pct:.1f}%"]


def format_risk_report(overnight_checks, today_tags, date_str):
    """生成风控日报"""
    lines = [
        f"【🛡️ 全链路风控报告】{date_str}",
        f"{'='*36}",
    ]
    
    if overnight_checks:
        lines.append(f"\n【可卖持仓】{len(overnight_checks)}只")
        for code, (action, reason, price) in overnight_checks.items():
            emoji = "🔴" if action else "🟢"
            lines.append(f"  {emoji} {code}: {action or '无操作'}")
            if reason:
                lines.append(f"     {reason}")
    
    if today_tags:
        lines.append(f"\n【当日新仓】{len(today_tags)}只 (不可卖，仅标记)")
        level_emoji = {"extreme":"🔴","high":"🟡","medium":"🟠","low":"🟢"}
        for code, (level, reasons) in today_tags.items():
            e = level_emoji.get(level, "⚪")
            lines.append(f"  {e} {code} [{level.upper()}] {'|'.join(reasons)}")
    
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    date_str = sys.argv[1] if len(sys.argv) > 1 else "20260324"
    print(format_circuit_report(date_str))
