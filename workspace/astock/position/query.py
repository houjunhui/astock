"""
query.py - 历史操作台账与胜率统计
"""

import csv
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))
from astock.position.position_tracker import PORTFOLIO_FILE


def load_all_trades():
    """从SQLite加载所有已平仓交易（字段统一转为字符串兼容处理）"""
    try:
        from astock.position.position_sqlite import load_all_trades as _sqlite_load
        rows = _sqlite_load()
        # SQLite返回float，转换为字符串统一处理
        str_rows = []
        for r in rows:
            r = dict(r)
            for k in ["pnl_pct", "pnl_amt", "buy_price", "close_price", "qty"]:
                if k in r and isinstance(r[k], (int, float)):
                    r[k] = str(r[k])
            str_rows.append(r)
        return str_rows
    except Exception:
        return []


def query_trades(code=None, start_date=None, end_date=None,
                 tier=None, status=None):
    """
    查询交易记录
    code: 股票代码
    start_date/end_date: YYYY-MM-DD
    tier: S/A/B/C
    status: 持仓/已平仓
    """
    trades = load_all_trades()
    result = []
    for t in trades:
        if code and t.get("code") != code:
            continue
        if start_date and t.get("buy_date", "") < start_date:
            continue
        if end_date and t.get("buy_date", "") > end_date:
            continue
        if status:
            s = t.get("status", "")
            if status == "持仓" and "已平仓" in s:
                continue
            if status == "已平仓" and "已平仓" not in s:
                continue
        result.append(t)
    return result


def statistics(start_date=None, end_date=None):
    """
    胜率统计
    返回: {total_trades, win_count, lose_count, win_rate,
          total_pnl_amt, avg_pnl_pct, by_tier, by_level, by_phase}
    """
    trades = load_all_trades()

    closed = [t for t in trades if "已平仓" in t.get("status", "")
              and t.get("buy_date", "") >= (start_date or "")]
    if end_date:
        closed = [t for t in closed if t.get("buy_date", "") <= end_date]

    total = len(closed)
    if total == 0:
        return {"total": 0, "win": 0, "lose": 0, "win_rate": 0,
                "total_pnl_amt": 0, "avg_pnl_pct": 0,
                "by_tier": {}, "by_level": {}, "by_phase": {}}

    wins = [t for t in closed if float(t.get("pnl_pct", 0)) > 0]
    losses = [t for t in closed if float(t.get("pnl_pct", 0)) <= 0]

    total_pnl = sum(float(t.get("pnl_amt", 0)) for t in closed
                    if t.get("pnl_amt", "").replace(".", "").replace("-", "").isdigit())
    avg_pnl = total_pnl / total if total else 0

    # 按评级统计
    by_tier = defaultdict(lambda: {"total": 0, "win": 0, "pnl": 0})
    for t in closed:
        tier = t.get("notes", "")
        # 从notes里提取评级
        tier_key = "B"
        if "A级" in tier:
            tier_key = "A"
        elif "S级" in tier:
            tier_key = "S"
        by_tier[tier_key]["total"] += 1
        if float(t.get("pnl_pct", 0)) > 0:
            by_tier[tier_key]["win"] += 1
        pnl_val = float(t.get("pnl_amt", 0)) if t.get("pnl_amt", "").replace(".", "").replace("-", "").isdigit() else 0
        by_tier[tier_key]["pnl"] += pnl_val

    # 按板位统计（使用CSV的level字段）
    by_level = defaultdict(lambda: {"total": 0, "win": 0, "pnl": 0})
    for t in closed:
        lv_str = t.get("level", "")
        if lv_str in ("6", "7", "8", "9"):
            level = "6板+"
        elif lv_str == "5":
            level = "5板"
        elif lv_str == "4":
            level = "4板"
        elif lv_str == "3":
            level = "3板"
        elif lv_str == "2":
            level = "2板"
        else:
            level = "1板"
        by_level[level]["total"] += 1
        if float(t.get("pnl_pct", 0)) > 0:
            by_level[level]["win"] += 1
        pnl_val = float(t.get("pnl_amt", 0)) if t.get("pnl_amt", "").replace(".", "").replace("-", "").isdigit() else 0
        by_level[level]["pnl"] += pnl_val

    # 按市场阶段统计（从notes提取）
    by_phase = defaultdict(lambda: {"total": 0, "win": 0, "pnl": 0})
    for t in closed:
        notes = t.get("notes", "")
        phase = "主升"
        if "退潮" in notes:
            phase = "退潮"
        elif "冰点" in notes:
            phase = "冰点"
        by_phase[phase]["total"] += 1
        if float(t.get("pnl_pct", 0)) > 0:
            by_phase[phase]["win"] += 1
        pnl_val = float(t.get("pnl_amt", 0)) if t.get("pnl_amt", "").replace(".", "").replace("-", "").isdigit() else 0
        by_phase[phase]["pnl"] += pnl_val

    # ── 最大回撤 ──
    # 按日期排序，计算累计盈亏曲线，找最大回落
    sorted_trades = sorted(closed, key=lambda t: t.get("buy_date", ""))
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for t in sorted_trades:
        pnl_val = float(t.get("pnl_amt", 0)) if t.get("pnl_amt", "").replace(".", "").replace("-", "").isdigit() else 0
        cumulative += pnl_val
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_drawdown:
            max_drawdown = dd

    # ── 连续亏损次数 ──
    consecutive = 0
    max_consecutive = 0
    for t in sorted_trades:
        if float(t.get("pnl_pct", 0)) < 0:
            consecutive += 1
            if consecutive > max_consecutive:
                max_consecutive = consecutive
        else:
            consecutive = 0

    # ── 可成交率 ──
    # 从 daily_pnl 读取实际成交笔数 / 买入信号数（后者暂无，以总持仓记录估算）
    try:
        from astock.position.position_sqlite import get_today_stats, get_db
        total_filled = len(list(get_db().execute("SELECT id FROM positions")).fetchall())
        # 简化：可成交率 = 有平仓记录的交易日数 / 交易天数
        filled_days = len(set(t["buy_date"] for t in closed)) if closed else 0
        signal_days = len(set(t.get("buy_date", "") for t in closed)) if closed else 0
        fill_rate = round(filled_days / signal_days * 100, 1) if signal_days else 0
    except Exception:
        fill_rate = 0.0

    # ── 炸板亏损占比 ──
    broken_limit_loss = sum(
        float(t.get("pnl_amt", 0)) for t in closed
        if "炸板" in t.get("status", "") or "炸板" in t.get("notes", "")
        and float(t.get("pnl_pct", 0)) < 0
    )
    total_loss = sum(float(t.get("pnl_amt", 0)) for t in losses
                     if t.get("pnl_amt", "").replace(".", "").replace("-", "").isdigit())
    broken_loss_ratio = round(abs(broken_limit_loss) / abs(total_loss) * 100, 1) if total_loss < 0 and broken_limit_loss < 0 else 0.0

    return {
        "total": total, "win": len(wins), "lose": len(losses),
        "win_rate": round(len(wins) / total * 100, 1) if total else 0,
        "total_pnl_amt": round(total_pnl, 0),
        "avg_pnl_pct": round(avg_pnl, 2),
        "max_drawdown": round(max_drawdown, 0),
        "max_consecutive_loss": max_consecutive,
        "fill_rate": fill_rate,
        "broken_limit_loss_ratio": broken_loss_ratio,
        "by_tier": dict(by_tier),
        "by_level": dict(by_level),
        "by_phase": dict(by_phase),
    }


def format_statistics(stats, date_range=""):
    """格式化统计报告"""
    lines = [f"【📊 胜率统计】{date_range}".strip()]

    if stats["total"] == 0:
        lines.append("暂无交易记录")
        return "\n".join(lines)

    lines.append(f"总交易: {stats['total']}笔 | 胜: {stats['win']} 负: {stats['lose']} | 胜率: {stats['win_rate']}%")
    lines.append(f"总盈亏: {stats['total_pnl_amt']:+,.0f}元 | 均盈亏: {stats['avg_pnl_pct']:+.2f}%")
    lines.append("")
    lines.append("【核心指标】")
    lines.append(f"  最大回撤: {stats.get('max_drawdown', 0):+,.0f}元")
    lines.append(f"  最大连亏: {stats.get('max_consecutive_loss', 0)}次")
    lines.append(f"  可成交率: {stats.get('fill_rate', 0):.1f}%")
    lines.append(f"  炸板亏损占比: {stats.get('broken_limit_loss_ratio', 0):.1f}%")
    lines.append("")

    # 按评级
    if stats["by_tier"]:
        lines.append("【按评级】")
        for tier in ["S", "A", "B", "C"]:
            if tier in stats["by_tier"]:
                d = stats["by_tier"][tier]
                wr = d["win"] / d["total"] * 100 if d["total"] else 0
                lines.append(f"  {tier}级: {d['total']}笔 胜率{wr:.0f}% 盈亏{d['pnl']:+,.0f}元")
        lines.append("")

    # 按板位
    if stats["by_level"]:
        lines.append("【按板位】")
        for lv in ["6板+", "3板+", "2板", "1板"]:
            if lv in stats["by_level"]:
                d = stats["by_level"][lv]
                wr = d["win"] / d["total"] * 100 if d["total"] else 0
                lines.append(f"  {lv}: {d['total']}笔 胜率{wr:.0f}% 盈亏{d['pnl']:+,.0f}元")
        lines.append("")

    # 按阶段
    if stats["by_phase"]:
        lines.append("【按阶段】")
        for ph in ["主升", "发酵", "启动", "退潮", "冰点"]:
            if ph in stats["by_phase"]:
                d = stats["by_phase"][ph]
                wr = d["win"] / d["total"] * 100 if d["total"] else 0
                lines.append(f"  {ph}: {d['total']}笔 胜率{wr:.0f}% 盈亏{d['pnl']:+,.0f}元")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", help="股票代码")
    parser.add_argument("--start", help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--status", choices=["持仓", "已平仓"])
    parser.add_argument("--stat", action="store_true")
    args = parser.parse_args()

    if args.stat:
        stats = statistics(args.start, args.end)
        print(format_statistics(stats, f"{args.start or '开始'} ~ {args.end or '今日'}"))
    else:
        trades = query_trades(code=args.code, start_date=args.start,
                              end_date=args.end, status=args.status)
        if not trades:
            print("无记录")
        for t in trades:
            print(f"{t['buy_date']} {t['code']} {t['name']} {t['status']} {t.get('pnl_pct','?')}%")


# 导出给 Feishu 命令调用
def cmd_query(args_str=""):
    """解析并执行查询命令"""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--code")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--status", choices=["持仓", "已平仓"])
    parser.add_argument("--stat", action="store_true")
    args = parser.parse_args(args_str.split() if args_str else [])

    if args.stat:
        stats = statistics(args.start, args.end)
        return format_statistics(stats, f"{args.start or '全量'} ~ {args.end or '今日'}")
    else:
        trades = query_trades(code=args.code, start_date=args.start,
                              end_date=args.end, status=args.status)
        if not trades:
            return "无记录"
        lines = []
        for t in trades:
            lines.append(
                f"{t['buy_date']} {t['name']}({t['code']}) "
                f"买{t['buy_price']} {t.get('status','?')} {t.get('pnl_pct','?')}%"
            )
        return "\n".join(lines)
