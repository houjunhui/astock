#!/usr/bin/env python3
"""
历史回测执行器
用真实历史行情数据（quicktiny）逐日模拟交易决策

回测流程：
1. 逐日加载：昨日ladder + 今日竞价 + 今日收盘K线
2. 模拟竞价买入、盘中监控、收盘平仓
3. 记录每笔交易，汇总统计
"""

import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime, timedelta
from collections import defaultdict

CAPITAL = 1_000_000


def get_trade_dates(start_date, end_date):
    """生成交易日列表（排除周末）"""
    dates = []
    d = datetime.strptime(str(start_date), "%Y%m%d")
    end = datetime.strptime(str(end_date), "%Y%m%d")
    while d <= end:
        if d.weekday() < 5:
            dates.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return dates


def prev_trade_day(date_str):
    d = datetime.strptime(str(date_str), "%Y%m%d")
    return (d - timedelta(days=1)).strftime("%Y%m%d")


def next_trade_day(date_str):
    d = datetime.strptime(str(date_str), "%Y%m%d")
    return (d + timedelta(days=1)).strftime("%Y%m%d")


def format_backtest_stats(closed, initial_capital=1_000_000):
    """从closed交易列表计算统计指标"""
    if not closed:
        return {
            "total_trades": 0, "win_trades": 0, "lose_trades": 0,
            "win_rate": 0, "total_pnl": 0, "avg_pnl": 0,
            "profit_loss_ratio": 0, "max_drawdown": 0,
            "max_drawdown_pct": 0, "sharpe_ratio": 0, "calmar_ratio": 0,
            "annual_return_pct": 0, "by_level": {}, "by_reason": {},
        }

    pnls = [t["pnl"] for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_pnl = sum(pnls)
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 1

    # 最大回撤（按时间顺序）
    peak = initial_capital
    max_dd = 0
    equity = initial_capital
    for t in closed:
        equity += t["pnl"]
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    # 夏普比率
    daily_rets = [t["pnl_pct"] / 100 for t in closed]
    if len(daily_rets) > 1:
        mean_r = sum(daily_rets) / len(daily_rets)
        std_r = (sum((r - mean_r) ** 2 for r in daily_rets) / len(daily_rets)) ** 0.5
        sharpe = (mean_r / std_r * (252 ** 0.5)) if std_r > 0 else 0
    else:
        sharpe = 0

    # 卡玛
    max_dd_pct = max_dd / initial_capital * 100
    annual_ret = total_pnl / initial_capital * 100
    calmar = annual_ret / max_dd_pct if max_dd_pct > 0 else 0

    # 按板位
    by_level = defaultdict(lambda: {"count": 0, "win": 0, "pnl": 0})
    for t in closed:
        lb = t.get("level", 1)
        by_level[lb]["count"] += 1
        if t["pnl"] > 0:
            by_level[lb]["win"] += 1
        by_level[lb]["pnl"] += t["pnl"]

    # 按原因
    by_reason = defaultdict(lambda: {"count": 0, "pnl": 0})
    for t in closed:
        reason = t.get("close_reason", "其他") or "其他"
        by_reason[reason]["count"] += 1
        by_reason[reason]["pnl"] += t["pnl"]

    return {
        "total_trades": len(closed),
        "win_trades": len(wins),
        "lose_trades": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0,
        "total_pnl": round(total_pnl, 0),
        "avg_pnl": round(total_pnl / len(closed), 0),
        "profit_loss_ratio": round(avg_win / avg_loss, 2) if avg_loss > 0 else 0,
        "max_drawdown": round(max_dd, 0),
        "max_drawdown_pct": round(max_dd_pct, 1),
        "sharpe_ratio": round(sharpe, 2),
        "calmar_ratio": round(calmar, 2),
        "annual_return_pct": round(annual_ret, 1),
        "by_level": dict(by_level),
        "by_reason": dict(by_reason),
    }


def format_backtest_report(stats):
    """格式化回测报告"""
    lines = ["【📈 历史回测结果】"]
    if stats["total_trades"] == 0:
        lines.append("  无交易记录")
        return "\n".join(lines)

    s = stats
    lines.extend([
        f"  总交易: {s['total_trades']}笔 | 胜: {s['win_trades']} 负: {s['lose_trades']} | 胜率: {s['win_rate']}%",
        f"  总盈亏: {s['total_pnl']:+,.0f}元 | 均盈亏: {s['avg_pnl']:+,.0f}元",
        f"  盈亏比: {s['profit_loss_ratio']}",
        f"  最大回撤: {s['max_drawdown']:+,.0f}元 ({s['max_drawdown_pct']}%)",
        f"  夏普比率: {s['sharpe_ratio']} | 卡玛比率: {s['calmar_ratio']}",
        f"  年化收益: {s['annual_return_pct']}%",
    ])

    if s.get("by_level"):
        lines.append("  【按板位】")
        for lb in sorted(s["by_level"].keys()):
            d = s["by_level"][lb]
            wr = round(d["win"] / d["count"] * 100, 1) if d["count"] else 0
            lines.append(f"    {lb}板: {d['count']}笔 胜率{wr}% 盈亏{d['pnl']:+,.0f}元")

    if s.get("by_reason"):
        lines.append("  【按平仓原因】")
        for reason, d in sorted(s["by_reason"].items(), key=lambda x: -x[1]["count"]):
            lines.append(f"    {reason}: {d['count']}笔 盈亏{d['pnl']:+,.0f}元")

    # 验证标准
    checks = {
        "年化收益率≥30%": s.get("annual_return_pct", 0) >= 30,
        "最大回撤≤15%": s.get("max_drawdown_pct", 999) <= 15,
        "胜率≥55%": s.get("win_rate", 0) >= 55,
        "盈亏比≥1.5": s.get("profit_loss_ratio", 0) >= 1.5,
        "卡玛比率≥2": s.get("calmar_ratio", 0) >= 2,
    }
    lines.append("\n  【第一阶段验证标准】")
    for name, passed in checks.items():
        icon = "✅" if passed else "❌"
        lines.append(f"    {icon} {name}")

    all_pass = all(checks.values())
    lines.append(f"\n  {'✅ 全部通过' if all_pass else '❌ 未全部通过'}")
    return "\n".join(lines)


def is_valid_for_live(stats):
    """判断是否通过第一阶段验证"""
    return (
        stats.get("annual_return_pct", 0) >= 30 and
        stats.get("max_drawdown_pct", 999) <= 15 and
        stats.get("win_rate", 0) >= 55 and
        stats.get("profit_loss_ratio", 0) >= 1.5 and
        stats.get("calmar_ratio", 0) >= 2
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="历史回测")
    parser.add_argument("--start", default="20260101", help="开始日期")
    parser.add_argument("--end", default="20260324", help="结束日期")
    args = parser.parse_args()

    from astock.backtest.daily import run_backtest_day
    from astock.strategy_params import get_params

    params = get_params()
    print(f"回测: {args.start} → {args.end}")
    print(f"参数版本: {list(params.items())[:3]}...")

    dates = get_trade_dates(args.start, args.end)
    print(f"交易日: {len(dates)}天")

    # 这里只是展示框架，实际回测需要完整数据
    print(f"\n提示: 实际回测请调用 run_backtest_range(start_date, end_date, params)")
