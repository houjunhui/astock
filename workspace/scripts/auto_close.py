#!/usr/bin/env python3
"""
auto_close.py - 收盘自动平仓 + 盈亏报告（增强版）
- 滑点模拟：卖出价×0.995（-0.5%）
- 使用SQLite持久化
- 生成每日盈亏报告
"""
import sys, os
from datetime import datetime, date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astock.position import (
    init_files, load_portfolio, close_position,
    get_current_price, get_daily_pnl
)

CAPITAL = 1_000_000

def auto_close(date_str):
    """收盘自动平仓"""
    init_files()
    today = date_str.replace("-", "")

    positions = load_portfolio()  # 仅返回status='持仓'的
    closed = []

    for pos in positions:
        # ── T+1 规则：今日新仓不允许在今日平仓 ──
        buy_date = pos.get("buy_date", "")
        if buy_date and buy_date == today:
            # 今日新开仓，不得在今日平仓 → 纳入隔夜仓
            continue
        if not buy_date:
            # 无buy_date字段的旧数据，按旧逻辑处理（有风险）
            buy_date_display = "未知"
        else:
            buy_date_display = buy_date

        code = pos["code"]
        name = pos["name"]
        buy_price = float(pos["buy_price"])
        qty = int(pos["qty"])

        cur = get_current_price(code)
        if not cur or cur <= 0:
            cur = buy_price

        # 滑点模拟：平仓价×0.995
        close_price = round(cur * 0.995, 2)
        reason = f"收盘自动平仓({today})"
        ok = close_position(code, close_price, reason)

        if ok:
            pnl_pct = round((close_price - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0
            pnl_amt = round((close_price - buy_price) * qty, 2)
            closed.append({
                "code": code, "name": name,
                "buy_price": buy_price, "close_price": close_price,
                "qty": qty, "pnl_pct": pnl_pct, "pnl_amt": pnl_amt,
                "buy_method": pos.get("buy_method", ""),
                "reason": reason,
            })

    return closed, positions


def format_report(closed, positions, date_str):
    date_fmt = date_str.replace("-", "")
    date_disp = f"{date_fmt[:4]}-{date_fmt[4:6]}-{date_fmt[6:]}"

    lines = [
        f"【📈 每日盈亏报告】{date_disp} 收盘",
        f"{'='*36}",
        "",
    ]

    if not closed:
        lines.append("今日无交易（无持仓或无新开仓）")
        total_pnl = 0.0
    else:
        total_pnl = sum(float(c["pnl_amt"]) for c in closed)
        total_pct = total_pnl / CAPITAL * 100

        lines.append(f"交易笔数: {len(closed)}笔")
        lines.append(f"总盈亏: {total_pnl:+,.0f}元 ({total_pct:+.2f}%)")
        lines.append(f"资金: {CAPITAL/10000:.0f}万")
        lines.append("")
        lines.append("【逐笔明细】")

        for i, c in enumerate(closed, 1):
            emoji = "✅" if float(c["pnl_amt"]) >= 0 else "❌"
            lines.append(
                f"{emoji} {i} {c['name']}({c['code']}) "
                f"{float(c['buy_price']):.2f}→{float(c['close_price']):.2f} "
                f"{float(c['pnl_pct']):+.2f}% ({float(c['pnl_amt']):+,.0f}元)"
            )

    lines.append("")
    lines.append("⚠️ 模拟交易记录，仅供策略验证（含-0.5%滑点）")
    return "\n".join(lines)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    date_arg = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    closed, positions = auto_close(date_arg)
    report = format_report(closed, positions, date_arg)
    print(report)

    # 生成每日复盘报告
    try:
        from astock.trade_logger import generate_daily_review, format_execution_trace
        date_str = date_arg.replace("-", "")
        review = generate_daily_review(date_str)
        print()
        print(format_execution_trace(date_str))
    except Exception as e:
        pass
