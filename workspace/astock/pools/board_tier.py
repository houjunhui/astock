"""
连板梯队分档适配系统 v1

规则:
1-2板低位: 竞价0%-7% 全阶段(冰点除外) → A级上限
3-5板中位: 竞价3%-8% 主升/发酵/分歧初期 → S级
6板+高标: 竞价5%-10% 仅主升/强发酵期 → S级

S级顶级标的顶尖标准:
- 必须是板块龙头+市场辨识度前3
- 竞价3%-8%
- 历史封板率≥90%
- 换手≥3%, VR≥5
- 无任何风险剔除因子
- 单板块仅1只S级
- 分歧/退潮期关闭S级权限
"""


# ── 连板梯队定义 ──────────────────────────────────────────────────
TIER_DEFINITIONS = {
    "low": {      # 1-2板低位（首板龙头可达9%+高开）
        "lb_range": (1, 2),
        "auction_min": 0.0,
        "auction_max": 9.0,   # 加宽：首板龙头高开8%+是强抢筹信号
        "allowed_phases": ("主升", "发酵", "分歧", "退潮"),
        "max_tier": "A",
        "position_cap": 0.20,
    },
    "mid": {      # 3-5板中位
        "lb_range": (3, 5),
        "auction_min": 2.0,
        "auction_max": 10.0,
        "allowed_phases": ("主升", "发酵", "分歧"),
        "max_tier": "S",
        "position_cap": 0.30,
    },
    "high": {     # 6板+高标
        "lb_range": (6, 999),
        "auction_min": 5.0,
        "auction_max": 10.0,
        "allowed_phases": ("主升",),
        "max_tier": "S",
        "position_cap": 0.30,
    },
}


def get_board_tier(lb):
    """根据连板数返回梯队类型"""
    if lb <= 2:
        return "low"
    elif lb <= 5:
        return "mid"
    else:
        return "high"


def can_open_position(lb, auction_chg, phase, phase_confidence=0):
    """
    判断是否可以开仓
    
    参数:
        lb: 连板数
        auction_chg: 竞价涨幅（%）
        phase: 市场阶段
        phase_confidence: 阶段置信度（0-100）
    
    返回: (can_open, tier, reason)
    """
    board_tier = get_board_tier(lb)
    tier_def = TIER_DEFINITIONS[board_tier]
    
    # 阶段检查
    if phase == "冰点":
        return False, board_tier, "冰点期不开仓"
    if phase not in tier_def["allowed_phases"]:
        return False, board_tier, f"{board_tier}梯队不支持{phase}期开仓"
    
    # 竞价区间检查
    if auction_chg < tier_def["auction_min"]:
        return False, board_tier, f"竞价{auction_chg}%<最低{tier_def['auction_min']}%"
    if auction_chg > tier_def["auction_max"]:
        return False, board_tier, f"竞价{auction_chg}%>最高{tier_def['auction_max']}%"
    
    return True, board_tier, f"✓ {board_tier}梯队竞价区间合法"


def get_s_level_requirements():
    """
    S级顶尖标准检查清单
    
    返回: (requirements_dict, description)
    """
    reqs = {
        "竞价区间": "3%-8%",
        "历史封板率": "≥90%",
        "换手率": "≥3%",
        "量比VR": "≥5",
        "市场阶段": "主升/强发酵",
        "龙板块地位": "板块龙头或市场前3",
        "风险剔除": "无任何R类因子",
        "板块唯一性": "同板块仅1只S级",
    }
    return reqs, "S级顶尖标准（6项必须全部满足）"


def check_s_level_candidate(stock, all_s_candidates):
    """
    检查某标的是否满足S级顶尖标准
    all_s_candidates: 同日其他S级候选（用于检查板块唯一性）
    
    返回: (passes, fails_list)
    """
    fails = []
    
    # 1. 竞价区间 3%-8%
    chg = stock.get("auction_chg", 0)
    if not (3.0 <= chg <= 8.0):
        fails.append(f"竞价{chg}%不在3%-8%区间")
    
    # 2. 历史封板率 ≥90%
    seal_rate = stock.get("limit_up_suc_rate", 0)
    if seal_rate < 0.90:
        fails.append(f"历史封板率{seal_rate:.0%}<90%")
    
    # 3. 换手率 ≥3%
    turnover = stock.get("turnover", 0)
    if turnover < 3.0:
        fails.append(f"换手{turnover:.1f}%<3%")
    
    # 4. 量比VR ≥5
    vr = stock.get("vr", 0)
    if vr < 5.0:
        fails.append(f"VR{vr:.1f}<5.0")
    
    # 5. 无一字板
    if stock.get("limit_up_type") == "一字板":
        fails.append("一字板高开")
    
    # 6. 板块唯一性（同板块S级最多1只）
    sector = stock.get("sector", "")
    sector_s_count = sum(1 for c in all_s_candidates 
                          if c.get("sector") == sector and c["code"] != stock["code"])
    if sector_s_count > 0:
        fails.append(f"同板块{sector}已有{sector_s_count}只S级")
    
    return len(fails) == 0, fails


def format_board_tier_report(date_str):
    """生成连板梯队日报"""
    from datetime import datetime
    
    # 市场阶段（延迟导入避免循环依赖）
    phase, temp = "发酵", 50.0  # 默认值
    try:
        import sys; sys.path.insert(0, '/home/gem/workspace/agent/workspace')
        from scripts.auto_buy import get_market_phase
        phase, temp = get_market_phase(date_str)
    except Exception:
        pass
    
    lines = [
        f"【📊 连板梯队分档】{date_str}",
        f"市场阶段: {phase} | 温度: {temp:.1f}",
        "",
    ]
    
    for tier_name, defn in TIER_DEFINITIONS.items():
        emoji = {"low": "🔵", "mid": "🟡", "high": "🔴"}[tier_name]
        lb_min, lb_max = defn["lb_range"]
        allowed = "、".join(defn["allowed_phases"])
        tier_cap = f"{defn['position_cap']*100:.0f}%"
        
        status = "✅ 开放" if phase in defn["allowed_phases"] else "❌ 关闭"
        
        lines.append(
            f"{emoji} {tier_name.upper()}梯队({lb_min}-{lb_max}板) {status}"
        )
        lines.append(
            f"   竞价区间: {defn['auction_min']:.0f}%-{defn['auction_max']:.0f}%"
        )
        lines.append(
            f"   可开仓阶段: {allowed}"
        )
        lines.append(
            f"   仓位上限: {tier_cap} | 最高评级: {defn['max_tier']}"
        )
        lines.append("")
    
    # S级顶尖标准
    reqs, desc = get_s_level_requirements()
    lines.append(f"【S级顶尖标准】（{desc}）")
    for k, v in reqs.items():
        lines.append(f"  {k}: {v}")
    
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")
    print(format_board_tier_report(date_str))
