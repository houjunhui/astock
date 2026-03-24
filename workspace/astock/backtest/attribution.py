#!/usr/bin/env python3
"""
归因复盘模块 - 增强版统计
在原有指标基础上，新增：
- 夏普比率、卡玛比率
- 分维度胜率/盈亏比统计（评级/板位/阶段/原因）
- 盈利来源与亏损原因精准拆解
- 炸板亏损占比、止损亏损占比
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import defaultdict

CAPITAL = 1_000_000


def enhanced_statistics(trades, daily_pnl=None):
    """
    增强版统计（支持回测 trades 和实盘 trades 两种格式）

    trades 格式: [{
        "code", "name", "buy_date", "buy_price", "close_price",
        "qty", "pnl", "pnl_pct", "reason", "level", "tier",
        "phase", "buy_method", ...
    }]
    """
    if not trades:
        return empty_stats()

    pnls = [t["pnl"] for t in trades]
    pnl_pcts = [t["pnl_pct"] for t in trades if isinstance(t["pnl_pct"], (int, float))]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_pnl = sum(pnls)
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 1

    # ── 基础指标 ──
    total = len(trades)
    win_count = len(wins)
    lose_count = len(losses)
    win_rate = win_count / total * 100 if total else 0
    profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0
    avg_pnl_pct = sum(pnl_pcts) / len(pnl_pcts) if pnl_pcts else 0

    # ── 最大回撤 ──
    peak = CAPITAL
    max_dd = 0
    equity = CAPITAL
    # 按时间顺序排序
    sorted_trades = sorted(trades, key=lambda t: t.get("buy_date", ""))
    for t in sorted_trades:
        equity += t["pnl"]
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    max_dd_pct = max_dd / CAPITAL * 100

    # ── 夏普比率 ──
    if len(pnl_pcts) > 1:
        mean_r = sum(pnl_pcts) / len(pnl_pcts)
        std_r = (sum((r - mean_r) ** 2 for r in pnl_pcts) / len(pnl_pcts)) ** 0.5
        sharpe = (mean_r / std_r * (252 ** 0.5)) if std_r > 0 else 0
    else:
        sharpe = 0

    # ── 卡玛比率 ──
    # 年化收益（假设252交易日）
    annual_ret = total_pnl / CAPITAL * 252 / max(len(trades), 1)
    calmar = annual_ret / max_dd_pct if max_dd_pct > 0 else 0

    # ── 按板位 ──
    by_level = defaultdict(lambda: {"count": 0, "win": 0, "lose": 0, "pnl": 0, "wins": 0.0, "losses": 0.0})
    for t in trades:
        lb = t.get("level", "?")
        by_level[lb]["count"] += 1
        if t["pnl"] > 0:
            by_level[lb]["win"] += 1
            by_level[lb]["wins"] += t["pnl"]
        else:
            by_level[lb]["lose"] += 1
            by_level[lb]["losses"] += abs(t["pnl"])
        by_level[lb]["pnl"] += t["pnl"]

    # ── 按评级 ──
    by_tier = defaultdict(lambda: {"count": 0, "win": 0, "lose": 0, "pnl": 0, "wins": 0.0, "losses": 0.0})
    for t in trades:
        tier = t.get("tier", "?") or "?"
        by_tier[tier]["count"] += 1
        if t["pnl"] > 0:
            by_tier[tier]["win"] += 1
            by_tier[tier]["wins"] += t["pnl"]
        else:
            by_tier[tier]["lose"] += 1
            by_tier[tier]["losses"] += abs(t["pnl"])
        by_tier[tier]["pnl"] += t["pnl"]

    # ── 按阶段 ──
    by_phase = defaultdict(lambda: {"count": 0, "win": 0, "pnl": 0})
    for t in trades:
        phase = t.get("phase", "unknown") or "unknown"
        by_phase[phase]["count"] += 1
        if t["pnl"] > 0:
            by_phase[phase]["win"] += 1
        by_phase[phase]["pnl"] += t["pnl"]

    # ── 按平仓原因 ──
    by_reason = defaultdict(lambda: {"count": 0, "win": 0, "pnl": 0})
    for t in trades:
        reason = t.get("reason", "其他") or "其他"
        by_reason[reason]["count"] += 1
        if t["pnl"] > 0:
            by_reason[reason]["win"] += 1
        by_reason[reason]["pnl"] += t["pnl"]

    # ── 亏损拆解 ──
    broken_limit_loss = 0
    stop_loss_total = 0
    trailing_profit_loss = 0
    other_loss = 0

    for t in trades:
        if t["pnl"] <= 0:
            reason = t.get("reason", "") or ""
            if "炸板" in reason:
                broken_limit_loss += abs(t["pnl"])
            elif "止损" in reason:
                stop_loss_total += abs(t["pnl"])
            elif "动态止盈" in reason or "回落" in reason:
                trailing_profit_loss += abs(t["pnl"])
            else:
                other_loss += abs(t["pnl"])

    total_loss = sum(abs(p) for p in losses)
    broken_limit_ratio = broken_limit_loss / total_loss * 100 if total_loss else 0
    stop_loss_ratio = stop_loss_total / total_loss * 100 if total_loss else 0
    trailing_ratio = trailing_profit_loss / total_loss * 100 if total_loss else 0

    # ── 连续亏损/盈利 ──
    streak_win = 0
    streak_lose = 0
    max_streak_win = 0
    max_streak_lose = 0
    current_streak = 0
    for t in sorted_trades:
        if t["pnl"] > 0:
            if current_streak > 0:
                current_streak += 1
            else:
                current_streak = 1
            max_streak_win = max(max_streak_win, current_streak)
        else:
            if current_streak < 0:
                current_streak -= 1
            else:
                current_streak = -1
            max_streak_lose = max(max_streak_lose, abs(current_streak))

    # ── 月度统计 ──
    by_month = defaultdict(lambda: {"count": 0, "pnl": 0})
    for t in trades:
        month = t.get("buy_date", "")[:6] if t.get("buy_date") else "unknown"
        by_month[month]["count"] += 1
        by_month[month]["pnl"] += t["pnl"]

    return {
        # 核心指标
        "total_trades": total,
        "win_count": win_count,
        "lose_count": lose_count,
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 0),
        "avg_pnl": round(total_pnl / total, 0) if total else 0,
        "avg_pnl_pct": round(avg_pnl_pct, 2),
        "profit_loss_ratio": round(profit_loss_ratio, 2),
        # 风险指标
        "max_drawdown": round(max_dd, 0),
        "max_drawdown_pct": round(max_dd_pct, 1),
        "sharpe_ratio": round(sharpe, 2),
        "calmar_ratio": round(calmar, 2),
        # 亏损拆解
        "broken_limit_loss": round(broken_limit_loss, 0),
        "stop_loss_total": round(stop_loss_total, 0),
        "trailing_profit_loss": round(trailing_profit_loss, 0),
        "other_loss": round(other_loss, 0),
        "broken_limit_loss_ratio": round(broken_limit_ratio, 1),
        "stop_loss_ratio": round(stop_loss_ratio, 1),
        "trailing_ratio": round(trailing_ratio, 1),
        # 连续性
        "max_consecutive_win": max_streak_win,
        "max_consecutive_loss": max_streak_lose,
        # 分维度
        "by_level": dict(by_level),
        "by_tier": dict(by_tier),
        "by_phase": dict(by_phase),
        "by_reason": dict(by_reason),
        "by_month": dict(by_month),
    }


def empty_stats():
    return {
        "total_trades": 0, "win_count": 0, "lose_count": 0, "win_rate": 0,
        "total_pnl": 0, "avg_pnl": 0, "avg_pnl_pct": 0, "profit_loss_ratio": 0,
        "max_drawdown": 0, "max_drawdown_pct": 0, "sharpe_ratio": 0, "calmar_ratio": 0,
        "broken_limit_loss": 0, "stop_loss_total": 0, "trailing_profit_loss": 0, "other_loss": 0,
        "broken_limit_loss_ratio": 0, "stop_loss_ratio": 0, "trailing_ratio": 0,
        "max_consecutive_win": 0, "max_consecutive_loss": 0,
        "by_level": {}, "by_tier": {}, "by_phase": {}, "by_reason": {}, "by_month": {},
    }


def format_enhanced_report(stats, date_range=""):
    """格式化增强版报告"""
    lines = [f"【📊 增强归因报告】{date_range}".strip()]

    if stats["total_trades"] == 0:
        lines.append("暂无交易记录")
        return "\n".join(lines)

    s = stats
    lines.extend([
        f"总交易: {s['total_trades']}笔 | 胜: {s['win_count']} 负: {s['lose_count']} | 胜率: {s['win_rate']}%",
        f"总盈亏: {s['total_pnl']:+,.0f}元 | 均盈亏: {s['avg_pnl']:+,.0f}元",
        f"盈亏比: {s['profit_loss_ratio']} | 均盈亏率: {s['avg_pnl_pct']:+.2f}%",
    ])

    lines.append(f"最大回撤: {s['max_drawdown']:+,.0f}元 ({s['max_drawdown_pct']}%)")
    lines.append(f"夏普比率: {s['sharpe_ratio']} | 卡玛比率: {s['calmar_ratio']}")

    # 亏损拆解
    total_loss = s['broken_limit_loss'] + s['stop_loss_total'] + s['trailing_profit_loss'] + s['other_loss']
    if total_loss > 0:
        lines.append(f"\n【亏损拆解】（总亏损{total_loss:+,.0f}元）")
        lines.append(f"  止损亏损: {s['stop_loss_total']:+,.0f}元 ({s['stop_loss_ratio']}%)")
        lines.append(f"  炸板亏损: {s['broken_limit_loss']:+,.0f}元 ({s['broken_limit_loss_ratio']}%)")
        lines.append(f"  动态止盈: {s['trailing_profit_loss']:+,.0f}元 ({s['trailing_ratio']}%)")
        lines.append(f"  其他亏损: {s['other_loss']:+,.0f}元")

    # 连续性
    lines.append(f"\n【连续性】最大连赢: {s['max_consecutive_win']}次 | 最大连亏: {s['max_consecutive_loss']}次")

    # 按板位
    if s.get("by_level"):
        lines.append("\n【按板位】")
        for lb in sorted(s["by_level"].keys(), key=lambda x: (x == "?", x)):
            d = s["by_level"][lb]
            wr = round(d["win"] / d["count"] * 100, 1) if d["count"] else 0
            pl_ratio = round(d["wins"] / d["losses"], 2) if d["losses"] > 0 else (999 if d["wins"] > 0 else 0)
            lines.append(f"  {lb}板: {d['count']}笔 胜率{wr}% 盈亏{d['pnl']:+,.0f}元 盈亏比{pl_ratio}")

    # 按评级
    if s.get("by_tier"):
        lines.append("\n【按评级】")
        for tier in ["S", "A", "B", "C"]:
            if tier in s["by_tier"]:
                d = s["by_tier"][tier]
                wr = round(d["win"] / d["count"] * 100, 1) if d["count"] else 0
                lines.append(f"  {tier}级: {d['count']}笔 胜率{wr}% 盈亏{d['pnl']:+,.0f}元")

    # 按阶段
    if s.get("by_phase"):
        lines.append("\n【按市场阶段】")
        phase_order = ["主升", "发酵", "分歧", "退潮", "冰点", "unknown"]
        for phase in phase_order:
            if phase in s["by_phase"]:
                d = s["by_phase"][phase]
                wr = round(d["win"] / d["count"] * 100, 1) if d["count"] else 0
                lines.append(f"  {phase}: {d['count']}笔 胜率{wr}% 盈亏{d['pnl']:+,.0f}元")

    # 按平仓原因
    if s.get("by_reason"):
        lines.append("\n【按平仓原因】")
        for reason, d in sorted(s["by_reason"].items(), key=lambda x: -x[1]["count"]):
            wr = round(d["win"] / d["count"] * 100, 1) if d["count"] else 0
            lines.append(f"  {reason}: {d['count']}笔 胜率{wr}% 盈亏{d['pnl']:+,.0f}元")

    # 月度
    if s.get("by_month"):
        lines.append("\n【月度统计】")
        for month, d in sorted(s["by_month"].items()):
            lines.append(f"  {month}: {d['count']}笔 盈亏{d['pnl']:+,.0f}元")

    # 验证标准
    checks = {
        "胜率≥55%": s.get("win_rate", 0) >= 55,
        "盈亏比≥1.5": s.get("profit_loss_ratio", 0) >= 1.5,
        "卡玛≥2": s.get("calmar_ratio", 0) >= 2,
        "最大回撤≤15%": s.get("max_drawdown_pct", 999) <= 15,
    }
    lines.append("\n【验证标准】")
    for name, passed in checks.items():
        lines.append(f"  {'✅' if passed else '❌'} {name}")

    return "\n".join(lines)


if __name__ == "__main__":
    # 测试
    sample_trades = [
        {"code": "600396", "name": "华电辽能", "buy_date": "20260324", "buy_price": 6.93,
         "close_price": 7.53, "qty": 43400, "pnl": 25172, "pnl_pct": 8.37,
         "reason": "目标价止盈", "level": 6, "tier": "A", "phase": "主升"},
        {"code": "600376", "name": "首开股份", "buy_date": "20260324", "buy_price": 5.93,
         "close_price": 5.66, "qty": 33700, "pnl": -9086, "pnl_pct": -4.55,
         "reason": "止损", "level": 1, "tier": "A", "phase": "主升"},
        {"code": "603016", "name": "新宏泰", "buy_date": "20260324", "buy_price": 39.35,
         "close_price": 39.49, "qty": 2500, "pnl": 356, "pnl_pct": 0.36,
         "reason": "炸板出局", "level": 1, "tier": "A", "phase": "主升"},
    ]
    stats = enhanced_statistics(sample_trades)
    print(format_enhanced_report(stats, "20260324"))
