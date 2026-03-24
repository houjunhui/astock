"""
动态仓位管理器 - T+1 持仓周期内动态调整
规则：
- 浮盈 ≥ 5% → 可加仓10%
- 浮亏 ≤ -3% → 强制减仓50%
- 持仓过夜 → 次日开盘检视
- 最长持仓3天，到期强制平
"""
import sys; sys.path.insert(0, '/home/gem/workspace/agent/workspace')
from astock.position import load_portfolio, get_daily_pnl
from datetime import datetime

def check_dynamic_position(position, current_profit_pct):
    """
    检查是否需要动态调整某只持仓的仓位
    
    position: 持仓记录 dict
    current_profit_pct: 当前浮盈比例（0.05 = 5%）
    
    返回: (action, new_capital_pct, reason)
        action: "hold" | "add" | "reduce" | "close"
    """
    from astock.strategy_params import get_params
    params = get_params()
    dp = params.get("dynamic_position", {})
    if not dp.get("enabled", False):
        return "hold", position.get("capital_pct", 0.10), "disabled"
    
    cap = position.get("capital_pct", 0.10)
    buy_date_str = position.get("buy_date", "")
    holding_days = 0
    if buy_date_str:
        try:
            buy_dt = datetime.strptime(buy_date_str, "%Y%m%d")
            holding_days = (datetime.now() - buy_dt).days
        except:
            pass
    
    max_days = dp.get("max_holding_days", 3)
    if holding_days >= max_days:
        return "close", 0, f"持仓{holding_days}天到期"
    
    # 浮盈处理
    if current_profit_pct >= 0.10:  # 浮盈≥10%
        bonus = dp.get("holding_night_bonus", 0.05)
        new_cap = min(cap + bonus, 0.35)  # 最多加到35%
        return "add", new_cap, f"浮盈{current_profit_pct*100:.1f}%加仓"
    elif current_profit_pct >= 0.05:
        return "hold", cap, f"浮盈{current_profit_pct*100:.1f}%，保持"
    
    # 浮亏处理
    if current_profit_pct <= -0.03:  # 浮亏≥3%
        scale = dp.get("profit_scale_factor", 0.5)
        new_cap = cap * scale
        return "reduce", new_cap, f"浮亏{abs(current_profit_pct)*100:.1f}%减仓"
    
    return "hold", cap, f"浮盈{current_profit_pct*100:.1f}%，保持"

def rebalance_portfolio():
    """检视所有持仓，返回需要调整的列表"""
    from astock.strategy_params import get_params
    params = get_params()
    dp = params.get("dynamic_position", {})
    if not dp.get("enabled", False):
        return []
    
    positions = load_portfolio()
    adjustments = []
    for pos in positions:
        code = pos.get("code")
        buy_price = float(pos.get("buy_price", 0))
        current_price = pos.get("current_price", buy_price)  # 暂无实时价时用买入价
        
        if buy_price <= 0:
            continue
        
        profit_pct = (current_price - buy_price) / buy_price
        action, new_cap, reason = check_dynamic_position(pos, profit_pct)
        
        if action != "hold":
            adjustments.append({
                "code": code,
                "name": pos.get("name"),
                "action": action,
                "old_capital_pct": pos.get("capital_pct", 0.10),
                "new_capital_pct": new_cap,
                "reason": reason,
                "profit_pct": profit_pct,
            })
    
    return adjustments

if __name__ == "__main__":
    adj = rebalance_portfolio()
    if adj:
        print("=== 动态仓位调整 ===")
        for a in adj:
            print(f"  {a['action']} {a['name']}({a['code']}): {a['old_capital_pct']*100:.0f}%→{a['new_capital_pct']*100:.0f}% | {a['reason']}")
    else:
        print("无调整")
