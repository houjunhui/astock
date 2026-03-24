#!/usr/bin/env python3
"""
实盘成本模拟器
模拟真实交易成本：佣金、印花税、滑点、涨跌停无法成交、大单堵单
"""

# 收费标准
COMMISSION_RATE = 0.0003    # 佣金万三（买卖双向）
STAMP_TAX_RATE = 0.001      # 印花税千一（仅卖出）
STAMP_TAX_EXEMPT_REASONS = {"止损", "强制平仓"}  # 止损免印花税

SLIPPAGE_BUY = 0.001        # 买入滑点 +0.1%（模拟冲击成本）
SLIPPAGE_SELL = 0.001       # 卖出滑点 -0.1%

LIMIT_UP_REJECT_PROB = 0.30  # 涨停无法追入概率（30%）
LIMIT_DOWN_REJECT_PROB = 0.20  # 跌停无法卖出概率（20%）


def calc_buy_cost(price, qty):
    """计算买入成本（含佣金+滑点）"""
    slip_price = round(price * (1 + SLIPPAGE_BUY), 2)
    gross = slip_price * qty
    commission = gross * COMMISSION_RATE
    return {
        "slip_price": slip_price,
        "gross": gross,
        "commission": round(commission, 2),
        "total_cost": round(gross + commission, 2),
    }


def calc_sell_revenue(price, qty, reason=""):
    """计算卖出收入（含佣金+印花税+滑点）"""
    slip_price = round(price * (1 - SLIPPAGE_SELL), 2)
    gross = slip_price * qty
    commission = gross * COMMISSION_RATE
    exempt = reason in STAMP_TAX_EXEMPT_REASONS
    stamp = gross * STAMP_TAX_RATE if not exempt else 0
    return {
        "slip_price": slip_price,
        "gross": gross,
        "commission": round(commission, 2),
        "stamp_tax": round(stamp, 2),
        "total_revenue": round(gross - commission - stamp, 2),
    }


def calc_pnl(buy_cost_dict, sell_revenue_dict, qty):
    """计算实际盈亏"""
    total_cost = buy_cost_dict["total_cost"]
    total_revenue = sell_revenue_dict["total_revenue"]
    pnl = total_revenue - total_cost
    pnl_pct = pnl / total_cost * 100 if total_cost > 0 else 0
    return {
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "total_cost": total_cost,
        "total_revenue": total_revenue,
        "total_fees": round(buy_cost_dict["commission"] + sell_revenue_dict["commission"] + sell_revenue_dict["stamp_tax"], 2),
    }


def simulate_limit_up_reject(auction_chg_pct, level):
    """
    判断涨跌停是否无法成交
    涨停买入：价格≥涨停价时无法追入
    跌停卖出：价格≤跌停价时无法割肉
    返回 (rejected: bool, reason: str)
    """
    import random
    # 一字板（竞价涨停，无换手）大概率买不进
    if auction_chg_pct >= 9.8 and level >= 2:
        if random.random() < LIMIT_UP_REJECT_PROB * level * 0.5:
            return True, "一字板高开无法买入"
    # 普通涨停追入
    if auction_chg_pct >= 9.5:
        if random.random() < LIMIT_UP_REJECT_PROB:
            return True, "涨停价无法追入"
    return False, ""


def simulate_large_order_slippage(qty, avg_volume):
    """
    模拟大单滑点
    qty: 成交数量
    avg_volume: 昨日平均成交量
    如果成交数量 > 平均成交量的20%，加一个额外滑点
    """
    if avg_volume <= 0:
        return 0
    occupancy = qty / avg_volume
    if occupancy > 0.5:
        return 0.002  # 额外-0.2%滑点
    elif occupancy > 0.2:
        return 0.001  # 额外-0.1%滑点
    return 0


def full_cost_simulation(buy_price, sell_price, qty, reason="", avg_volume=0):
    """
    完整成本模拟
    返回实际盈亏（已扣所有成本）
    """
    buy = calc_buy_cost(buy_price, qty)
    extra_slippage = simulate_large_order_slippage(qty, avg_volume)
    sell_base = sell_price * (1 - extra_slippage)
    sell = calc_sell_revenue(sell_base, qty, reason)
    return calc_pnl(buy, sell, qty)


def format_cost_breakdown(buy_cost, sell_revenue, pnl_info):
    """格式化成本明细"""
    lines = [
        f"  买入价（含+0.1%滑点）: {buy_cost['slip_price']:.2f}",
        f"  买入金额: {buy_cost['gross']:,.0f}元",
        f"  买入佣金: {buy_cost['commission']:,.2f}元",
        f"  卖出价（含-0.1%滑点）: {sell_revenue['slip_price']:.2f}",
        f"  卖出金额: {sell_revenue['gross']:,.0f}元",
        f"  卖出佣金: {sell_revenue['commission']:,.2f}元",
        f"  印花税: {sell_revenue['stamp_tax']:,.2f}元",
        f"  总费用: {pnl_info['total_fees']:,.2f}元",
        f"  实际盈亏: {pnl_info['pnl']:+,.2f}元 ({pnl_info['pnl_pct']:+.2f}%)",
    ]
    return "\n".join(lines)


# 测试
if __name__ == "__main__":
    buy_price = 10.0
    qty = 100000
    sell_price = 10.7

    buy = calc_buy_cost(buy_price, qty)
    sell = calc_sell_revenue(sell_price, qty, "止盈")
    pnl = calc_pnl(buy, sell, qty)

    print("【成本明细测试】")
    print(format_cost_breakdown(buy, sell, pnl))

    # 止损情况（免印花税）
    sell_stop = calc_sell_revenue(sell_price * 0.97, qty, "止损")
    pnl_stop = calc_pnl(buy, sell_stop, qty)
    print("\n【止损情况】")
    print(format_cost_breakdown(buy, sell_stop, pnl_stop))
