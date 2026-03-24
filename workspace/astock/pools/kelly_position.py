"""
动态仓位管理：凯利公式 + 风险预算双约束 v1

凯利公式: f* = (b×p - q) / b
  b = 盈亏比（盈利额/亏损额）
  p = 晋级胜率（次日续涨概率）
  q = 1-p
  f* = 最优仓位比例

风险预算约束:
  单票最大亏损 ≤ 总资金×1%
  max_position = 风险预算 / 止损线

总仓位上限（情绪阶段）:
  主升≤70%, 发酵≤60%, 分歧≤40%, 退潮≤20%, 冰点=0%

分散化约束:
  单日最多: 主升3只, 发酵2只, 分歧/退潮1只, 冰点0只
  单板块仓位 ≤ 总仓位×30%
"""

from astock.pools.emotion_thermometer import score_to_phase

# ── 常量 ───────────────────────────────────────────────────────────
CAPITAL = 1_000_000  # 100万
RISK_PER_STOCK = 0.01  # 单票最大风险1%
MAX_POSITIONS = {"主升": 3, "发酵": 2, "分歧": 1, "退潮": 1, "冰点": 0}
PHASE_CAP = {"主升": 0.70, "发酵": 0.60, "分歧": 0.40, "退潮": 0.20, "冰点": 0.00}
SECTOR_CONCENTRATION = 0.30  # 单板块上限30%


def kelly_fraction(win_rate, avg_win_pct, avg_loss_pct):
    """
    凯利公式计算最优仓位
    
    参数:
        win_rate: 胜率（0-1）
        avg_win_pct: 平均盈利百分比（%，如8.0代表8%）
        avg_loss_pct: 平均亏损百分比（%，如4.0代表4%）
    
    返回:
        f_star: 最优仓位比例（0-1），负数表示不应开仓
    """
    if win_rate <= 0 or avg_win_pct <= 0 or avg_loss_pct <= 0:
        return 0.0
    
    p = win_rate
    q = 1 - p
    b = avg_win_pct / avg_loss_pct  # 盈亏比
    
    f_star = (b * p - q) / b
    
    # 凯利公式：负数→不应开仓，取0；正数→不超过50%上限
    if f_star < 0:
        return 0.0
    return min(f_star, 0.50)


def risk_budget_limit(capital, risk_pct, stop_loss_pct):
    """
    风险预算约束：单票最大仓位
    
    单票最大亏损 = capital × risk_pct
    max_position = 最大亏损 / 止损线
    """
    max_loss = capital * risk_pct
    max_pos = max_loss / (stop_loss_pct / 100)
    return max_pos / capital  # 返回比例


def calc_single_position(code, tier, win_rate, avg_win_pct, avg_loss_pct, 
                         stop_loss_pct, phase, sector_cap_pct=0.30):
    """
    计算单只标的的建议仓位
    
    参数:
        tier: S/A/B/C 评级
        win_rate: 晋级胜率（0-1）
        avg_win_pct: 平均盈利%
        avg_loss_pct: 平均亏损%
        stop_loss_pct: 止损线%
        phase: 市场阶段
        sector_cap_pct: 板块集中度上限（0-1）
    
    返回: (仓位比例, 原因)
    """
    phase_cap = PHASE_CAP.get(phase, 0)
    
    # 冰点期不开仓
    if phase == "冰点":
        return 0.0, "冰点期不开仓"
    
    # C级放弃
    if tier == "C":
        return 0.0, "C级放弃"
    
    # 凯利公式
    kelly = kelly_fraction(win_rate, avg_win_pct, avg_loss_pct)
    
    # 风险预算（1%单票亏损上限）
    risk_limit = risk_budget_limit(CAPITAL, RISK_PER_STOCK, stop_loss_pct)
    
    # 单票仓位上限（取凯利和风险预算的较小值）
    tier_max = {"S": 0.30, "A": 0.20, "B": 0.15}.get(tier, 0.10)
    single_cap = min(kelly, risk_limit, tier_max, phase_cap)
    
    # 板块集中度检查（sector_cap_pct是板块已用仓位比例）
    if sector_cap_pct + single_cap > SECTOR_CONCENTRATION:
        single_cap = max(0, SECTOR_CONCENTRATION - sector_cap_pct)
        reason = f"板块集中度限制→压缩至{single_cap:.0%}"
    else:
        reason = f"凯利{ kelly:.0%} | 风险预算{ risk_limit:.0%} | 阶段上限{ phase_cap:.0%} | {tier}级上限{tier_max:.0%}"
    
    return round(single_cap, 3), reason


def allocate_positions(candidates, phase, sector_map):
    """
    多只标的仓位分配（优先级S>A>B，超限按评级等比例压缩）
    
    分配规则：
    1. 按优先级排序（S最优先）
    2. 按凯利公式计算每只仓位
    3. 若总仓位超过阶段上限，按评级优先级从低到高等比例压缩
       B级先压缩 → 不够再压缩A级 → 最后压缩S级
    4. 板块集中度：单板块仓位≤30%，超出按持仓比例压缩
    
    candidates: [{code, tier, win_rate, avg_win_pct, avg_loss_pct, stop_loss_pct}]
    sector_map: {sector: used_cap_pct} 板块已用仓位
    phase: 市场阶段
    
    返回: [{code, tier, position_pct, reason}]
    """
    if not candidates or phase == "冰点":
        return []
    
    max_total = PHASE_CAP.get(phase, 0)
    max_count = MAX_POSITIONS.get(phase, 0)
    
    # 按优先级排序
    tier_order = {"S": 0, "A": 1, "B": 2}
    sorted_cands = sorted(candidates, key=lambda x: tier_order.get(x["tier"], 3))
    
    allocated = []
    total_cap = 0.0
    count = 0
    
    for cand in sorted_cands:
        if count >= max_count:
            cand["position_pct"] = 0.0
            cand["reason"] = f"超过单日{max_count}只上限"
            continue
        if total_cap >= max_total:
            cand["position_pct"] = 0.0
            cand["reason"] = f"总仓位已达{max_total:.0%}上限"
            continue
        
        sector = cand.get("sector", "default")
        sector_used = sector_map.get(sector, 0)
        
        pos, reason = calc_single_position(
            cand["code"], cand["tier"], cand["win_rate"],
            cand["avg_win_pct"], cand["avg_loss_pct"],
            cand["stop_loss_pct"], phase, sector_used
        )
        
        # 超出阶段上限：从B级开始等比例压缩
        if total_cap + pos > max_total:
            # 优先压缩低评级
            if tier == "B":
                pos = min(pos, max(0, max_total - total_cap))
                reason = f"B级等比例压缩至{tier_cap:.0%}"
            elif tier == "A":
                # 检查B级是否还有空间
                b_alloc = next((a["position_pct"] for a in allocated if a["tier"]=="B"), 0)
                if b_alloc > 0.05:
                    reason = "B级尚有空间，A级跳过"
                    continue  # 跳过，等下次或压缩后重分配
                pos = min(pos, max(0, max_total - total_cap))
                reason = f"A级压缩至{tier_cap:.0%}"
            else:
                pos = min(pos, max(0, max_total - total_cap))
                reason = f"S级压缩至{max_total:.0%}" if pos > 0 else "总仓位已满"
        
        cand["position_pct"] = pos
        cand["reason"] = reason
        allocated.append(cand)
        
        total_cap += pos
        sector_map[sector] = sector_used + pos
        count += 1
    
    return allocated


def format_kelly_report(phase, candidates, allocated):
    """生成凯利仓位分配报告"""
    emoji = {"主升":"🔥","发酵":"☀️","分歧":"⛅","退潮":"🌧️","冰点":"❄️"}.get(phase,"?")
    phase_cap = PHASE_CAP.get(phase, 0)
    
    lines = [
        f"【📊 凯利仓位分配】{emoji}{phase}期",
        f"{'='*36}",
        f"总仓位上限: {phase_cap:.0%} | 候选{candidates}只",
        f"",
    ]
    
    total = sum(a["position_pct"] for a in allocated)
    for a in allocated:
        tier_emoji = {"S":"🅛","A":"🅐","B":"🅑","C":"🅒"}.get(a["tier"],"?")
        status = "✅" if a["position_pct"] > 0 else "❌"
        lines.append(
            f"  {status} {tier_emoji}{a['code']} {a['tier']}级 "
            f"→ {a['position_pct']:.0%}"
        )
        lines.append(f"     {a['reason']}")
    
    lines.append(f"{'─'*36}")
    lines.append(f"  合计仓位: {total:.0%} / {phase_cap:.0%}")
    
    return "\n".join(lines)


if __name__ == "__main__":
    # 快速测试
    candidates = [
        {"code":"600396","tier":"S","win_rate":0.40,"avg_win_pct":9.0,"avg_loss_pct":4.0,"stop_loss_pct":4.0},
        {"code":"600376","tier":"A","win_rate":0.30,"avg_win_pct":7.0,"avg_loss_pct":4.0,"stop_loss_pct":4.0},
        {"code":"600149","tier":"B","win_rate":0.25,"avg_win_pct":7.0,"avg_loss_pct":4.0,"stop_loss_pct":4.0},
    ]
    for phase in ["主升","发酵","分歧","退潮","冰点"]:
        sector_map = {}
        allocated = allocate_positions(candidates, phase, sector_map)
        print(format_kelly_report(phase, len(candidates), allocated))
        print()
