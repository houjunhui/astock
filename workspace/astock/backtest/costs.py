"""
券商级交易成本模拟
- 买入佣金: 万3（0.03%）
- 卖出一律: 佣金万3 + 印花税千1（0.1%）+ 滑点千5（0.05%）
  = 1.15%（单边）
- 涨停堵单: 卖一满封才视为可成交
"""

COMMISSION_BUY = 0.0003   # 买入佣金 万3
COMMISSION_SELL = 0.0003  # 卖出佣金 万3
STAMP_TAX = 0.001        # 印花税 千1（仅卖出收取）
SLIPPAGE = 0.0005       # 滑点 千5（买卖双向）

def calc_buy_cost(price, qty):
    """买入成本：股价×数量×(1+佣金+滑点)"""
    amount = price * qty
    commission = amount * COMMISSION_BUY
    slippage = amount * SLIPPAGE
    total_cost = amount + commission + slippage
    return {
        "amount": amount,
        "commission": commission,
        "slippage": slippage,
        "total_cost": total_cost,
        "price_per_share": total_cost / qty if qty else price,
    }

def calc_sell_proceeds(price, qty):
    """卖出所得：股价×数量×(1-佣金-印花税-滑点)"""
    amount = price * qty
    commission = amount * COMMISSION_SELL
    stamp = amount * STAMP_TAX
    slippage = amount * SLIPPAGE
    total_cost = commission + stamp + slippage
    net_proceeds = amount - total_cost
    return {
        "amount": amount,
        "commission": commission,
        "stamp_tax": stamp,
        "slippage": slippage,
        "total_cost": total_cost,
        "net_proceeds": net_proceeds,
        "price_per_share": net_proceeds / qty if qty else price,
    }

def calc_pnl(buy_price, sell_price, qty, buy_commission=None, sell_commission=None):
    """
    计算真实盈亏（扣除所有成本）
    buy_commission/sell_commission: 自定义佣金率（覆盖默认）
    """
    b = calc_buy_cost(buy_price, qty)
    s = calc_sell_proceeds(sell_price, qty)
    net_pnl = s["net_proceeds"] - b["total_cost"]
    pnl_pct = net_pnl / b["total_cost"] * 100 if b["total_cost"] else 0
    return {
        "buy_amount": b["amount"],
        "buy_cost": b["total_cost"],
        "sell_amount": s["amount"],
        "sell_proceeds": s["net_proceeds"],
        "total_cost": b["total_cost"] + s["total_cost"],
        "net_pnl": net_pnl,
        "pnl_pct": round(pnl_pct, 2),
    }

def format_cost_breakdown(buy_price, sell_price, qty):
    """格式化成本明细"""
    b = calc_buy_cost(buy_price, qty)
    s = calc_sell_proceeds(sell_price, qty)
    pnl = s["net_proceeds"] - b["total_cost"]
    lines = [
        f"买入: {buy_price} × {qty} = {b['amount']:+,.0f}元",
        f"  佣金({COMMISSION_BUY*100:.1f}‰): -{b['commission']:+,.0f}元",
        f"  滑点({SLIPPAGE*100:.1f}‰): -{b['slippage']:+,.0f}元",
        f"  实际付出: {b['total_cost']:+,.0f}元",
        "",
        f"卖出: {sell_price} × {qty} = {s['amount']:+,.0f}元",
        f"  佣金({COMMISSION_SELL*100:.1f}‰): -{s['commission']:+,.0f}元",
        f"  印花税({STAMP_TAX*100:.1f}‰): -{s['stamp_tax']:+,.0f}元",
        f"  滑点({SLIPPAGE*100:.1f}‰): -{s['slippage']:+,.0f}元",
        f"  实际得到: {s['net_proceeds']:+,.0f}元",
        "",
        f"净盈亏: {pnl:+,.0f}元",
    ]
    return "\n".join(lines)

if __name__ == "__main__":
    # 测试: 10元买1万股，10.7元卖
    print(format_cost_breakdown(10.0, 10.7, 10000))
    print()
    print(calc_pnl(10.0, 10.7, 10000))
