#!/usr/bin/env python3
"""
auto_close.py - 收盘后自动平仓 + 生成盈亏报告
用法: python3 auto_close.py [date]

逻辑:
1. 读取所有未平持仓
2. 以收盘价自动平仓
3. 统计当日盈亏
4. 生成报告发送飞书
"""

import sys
import os
from datetime import datetime, date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astock.position import (
    init_files, load_portfolio, close_position,
    get_current_price, get_portfolio_status
)
from pathlib import Path


CAPITAL = 1_000_000  # 100万模拟资金


def daily_pnl_file():
    return Path(__file__).parent.parent / "astock" / "position" / "daily_pnl.csv"


def auto_close(date_str):
    """
    收盘自动平仓
    返回: (平仓记录列表, 统计)
    """
    init_files()
    today = date_str.replace("-", "")

    # 读取所有持仓（含已平仓的今日记录）
    all_rows = []
    pf_file = Path(__file__).parent.parent / "astock" / "position" / "portfolio.csv"
    if pf_file.exists():
        import csv
        with open(pf_file, "r") as f:
            all_rows = list(csv.DictReader(f))

    # 只平仓今日开仓且未平的
    closed = []
    still_open = []

    for row in all_rows:
        if row.get("buy_date", "").replace("-", "") == today:
            code = row["code"]
            name = row["name"]
            buy_price = float(row["buy_price"])
            qty = int(row["qty"])
            method = row.get("buy_method", "")
            notes = row.get("notes", "")

            cur = get_current_price(code)
            if cur is None:
                cur = buy_price  # 无法取到收盘价，用买入价

            pnl_pct = (cur - buy_price) / buy_price * 100
            pnl_amt = (cur - buy_price) * qty

            close_position(code, round(cur, 2), f"收盘自动平仓({today})")

            closed.append({
                "code": code, "name": name,
                "buy_price": buy_price, "close_price": round(cur, 2),
                "qty": qty,
                "pnl_pct": round(pnl_pct, 2),
                "pnl_amt": round(pnl_amt, 2),
                "buy_method": method,
                "notes": notes,
            })
        elif row.get("status", "") in ("持仓", "持仓中"):
            still_open.append(row)

    return closed, still_open


def append_daily_pnl(date_str, closed):
    """追加到每日盈亏表"""
    f = daily_pnl_file()
    f.parent.mkdir(parents=True, exist_ok=True)

    import csv
    write_header = not f.exists()

    with open(f, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "code", "name", "buy_price", "close_price",
                                            "qty", "pnl_pct", "pnl_amt", "buy_method", "reason"])
        if write_header:
            w.writeheader()

        for c in closed:
            w.writerow({
                "date": date_str,
                "code": c["code"],
                "name": c["name"],
                "buy_price": c["buy_price"],
                "close_price": c["close_price"],
                "qty": c["qty"],
                "pnl_pct": c["pnl_pct"],
                "pnl_amt": c["pnl_amt"],
                "buy_method": c.get("buy_method", ""),
                "reason": "收盘平仓",
            })


def format_report(closed, date_str, capital=CAPITAL):
    """格式化每日盈亏报告"""
    date_fmt = date_str.replace("-", "")
    date_disp = f"{date_fmt[:4]}-{date_fmt[4:6]}-{date_fmt[6:]}"

    lines = [
        f"【📈 每日盈亏报告】{date_disp} 收盘",
        f"{'='*36}",
        "",
    ]

    if not closed:
        lines.append("今日无交易（无持仓或无新开仓）")
        total_pnl = 0
        total_pct = 0
    else:
        total_pnl = sum(c["pnl_amt"] for c in closed)
        total_pct = total_pnl / capital * 100

        lines.append(f"交易笔数: {len(closed)}笔")
        lines.append(f"总盈亏: {total_pnl:+,.0f}元 ({total_pct:+.2f}%)")
        lines.append(f"资金: {capital/10000:.0f}万")
        lines.append("")
        lines.append("【逐笔明细】")
        for i, c in enumerate(closed, 1):
            emoji = "✅" if c["pnl_amt"] >= 0 else "❌"
            lines.append(
                f"{emoji} {i} {c['name']}({c['code']}) "
                f"{c['buy_price']}→{c['close_price']} "
                f"{c['pnl_pct']:+.2f}% ({c['pnl_amt']:+,.0f}元)"
            )

    lines.append("")
    lines.append(f"{'='*36}")
    lines.append("⚠️ 模拟交易记录，仅供策略验证")

    return "\n".join(lines)


if __name__ == "__main__":
    import os as _os
    _os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    date_arg = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    date_str = date_arg

    closed, still_open = auto_close(date_str)
    append_daily_pnl(date_str, closed)
    report = format_report(closed, date_str)
    print(report)
