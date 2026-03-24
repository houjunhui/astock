"""
仓位计算器 v1.0
根据评级(tier)、置信度、熔断状态计算最优仓位
"""

from datetime import datetime, date

# 默认总资金（实际使用时从配置文件读取或用户指定）
DEFAULT_CAPITAL = 1000000  # 100万模拟资金


def calc_position(tier, capital=None, confidence=None, phase="主升",
                  daily_pnl=0, daily_loss_limit=-30000,
                  existing_positions=None):
    """
    计算仓位
    
    参数:
        tier: S/A/B/C 评级
        capital: 总资金，默认100万
        confidence: 额外置信度系数(0-1)，越高越重仓
        phase: 市场阶段
        daily_pnl: 当日盈亏
        daily_loss_limit: 当日最大亏损限额（触发熔断）
        existing_positions: 已有持仓列表 [{code, capital_pct}]
    
    返回: {
        capital: 可用资金,
        suggest_capital_pct: 建议仓位比例,
        suggest_amount: 建议金额,
        lot_size: 手数（A股100股=1手）,
        halt: 是否触发熔断
    }
    """
    capital = capital or DEFAULT_CAPITAL
    existing = existing_positions or []
    
    # 已有仓位
    used_pct = sum(float(p.get("capital_pct", 0)) for p in existing)
    available = capital * (1 - used_pct)
    
    # 熔断检查
    if daily_pnl <= daily_loss_limit:
        return {
            "available_capital": available,
            "suggest_capital_pct": 0,
            "suggest_amount": 0,
            "lot_size": 0,
            "halt": True,
            "reason": f"触发熔断！当日亏损{daily_pnl:.0f}元，超过限额{daily_loss_limit:.0f}元，停止开新仓"
        }
    
    # 基础仓位
    tier_position = {
        "S": 1.00,
        "A": 0.50,
        "B": 0.30,
        "C": 0.00,
    }.get(tier, 0)
    
    # 置信度调整
    conf_factor = confidence if confidence else 1.0
    
    # 市场阶段调整
    phase_factor = {
        "主升": 1.2,
        "发酵": 1.0,
        "启动": 0.85,
        "退潮": 0.50,
        "冰点": 0.30,
        "恐慌": 0.00,
    }.get(phase, 0.70)
    
    # 汇总
    final_pct = tier_position * conf_factor * phase_factor
    final_pct = min(max(final_pct, 0), 1.0)
    
    # 最高单只仓位上限30%（防止重仓单一标的）
    final_pct = min(final_pct, 0.30)
    
    # 最高总仓位（主升不超过90%，其他不超过70%）
    max_total = 0.90 if phase in ("主升", "发酵") else 0.70
    if used_pct + final_pct > max_total:
        final_pct = max(0, max_total - used_pct)
    
    amount = int(available * final_pct / 100) * 100  # 向下取整百
    lot_size = amount // 100  # A股1手=100股
    
    return {
        "available_capital": available,
        "used_capital_pct": used_pct,
        "suggest_capital_pct": round(final_pct, 2),
        "suggest_amount": amount,
        "lot_size": lot_size,
        "halt": False,
        "phase": phase,
        "tier_position": tier_position,
        "conf_factor": conf_factor,
        "phase_factor": phase_factor,
    }


def calc_stop_loss(buy_price, method="fixed", param=None):
    """
    计算止损价
    
    方法:
    - fixed: 固定比例（默认-3%）
    - atr: ATR倍数
    - recent_low: 近N日最低点
    """
    if not buy_price or buy_price <= 0:
        return 0.0
    if method == "fixed":
        pct = param if param is not None else 0.04
        return round(buy_price * (1 - pct), 2)
    elif method == "recent_low":
        # 近N日最低点（需要kline数据，这里简化）
        return param or round(buy_price * 0.97, 2)
    return round(buy_price * 0.96, 2)


def calc_target(buy_price, method="fixed", param=None, phase=None):
    """
    计算目标价（支持分阶段调整）
    phase: 主升/发酵/分歧/退潮/冰点
      主升→目标上浮5%（避免卖飞）
      退潮→目标下调3%（及时止盈）
      冰点→目标下调10%
    """
    if not buy_price or buy_price <= 0:
        return 0.0
    if method == "fixed":
        pct = param if param is not None else 0.09
        if phase == "主升":
            pct = pct * 1.05
        elif phase == "退潮":
            pct = pct * 0.97
        elif phase == "冰点":
            pct = pct * 0.90
        return round(buy_price * (1 + pct), 2)
    return round(buy_price * 1.10, 2)


def format_position_calc(result, code, name, buy_price):
    """格式化仓位计算报告"""
    if result["halt"]:
        return f"🚨 熔断触发：{result['reason']}"
    
    pct = result["suggest_capital_pct"]
    amount = result["suggest_amount"]
    lots = result["lot_size"]
    phase = result["phase"]
    
    stop = calc_stop_loss(buy_price)
    target = calc_target(buy_price)
    
    lines = [
        f"📈 {name}({code}) 仓位计算",
        f"  阶段: {phase} | 建议仓位: {pct*100:.0f}%",
        f"  可用资金: {result['available_capital']:.0f}元",
        f"  建议买入: {amount}元 ({lots}手)",
        f"  买入价格: {buy_price}元",
        f"  止损价: {stop}元 (-{((buy_price-stop)/buy_price)*100:.1f}%)",
        f"  目标价: {target}元 (+{((target-buy_price)/buy_price)*100:.1f}%)",
    ]
    
    return "\n".join(lines)
