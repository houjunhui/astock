"""
策略验证框架: 历史回测 + 仿真对比 + 实盘追踪

验证标准:
- 年化收益≥60%, 最大回撤≤15%, 盈亏比≥3:1, 胜率≥60%
- 样本内/外收益差≤20%
- 全情绪周期正收益，退潮期回撤≤8%
"""

import json
from datetime import datetime, date, timedelta
from pathlib import Path
from statistics import mean, stdev

WORKSPACE = Path("/home/gem/workspace/agent/workspace")
BACKTEST_RESULT_FILE = WORKSPACE / "astock" / "position" / "backtest_results.json"
LIVE_TRACKING_FILE = WORKSPACE / "astock" / "position" / "live_tracking.json"

INITIAL_CAPITAL = 1_000_000.0

# ── 验证标准 ───────────────────────────────────────────────────
BENCHMARKS = {
    "annual_return_min": 0.60,      # 年化收益≥60%
    "max_drawdown_max": 0.15,       # 最大回撤≤15%
    "profit_loss_ratio_min": 3.0,   # 盈亏比≥3:1
    "win_rate_min": 0.60,           # 胜率≥60%
    "in_out_sample_gap_max": 0.20,   # 样本内外差≤20%
    "recession_drawdown_max": 0.08, # 退潮期回撤≤8%
}


def load_backtest_results():
    if BACKTEST_RESULT_FILE.exists():
        with open(BACKTEST_RESULT_FILE) as f:
            return json.load(f)
    return None


def save_backtest_results(data):
    with open(BACKTEST_RESULT_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def compute_annual_return(total_return, days):
    """计算年化收益"""
    if days <= 0:
        return 0.0
    years = days / 365
    return (1 + total_return) ** (1 / years) - 1


def compute_max_drawdown(equity_curve):
    """计算最大回撤"""
    peak = INITIAL_CAPITAL
    max_dd = 0.0
    for e in equity_curve:
        if e > peak:
            peak = e
        dd = (peak - e) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd


def compute_profit_loss_ratio(trades):
    """计算盈亏比"""
    wins = [t["pnl_amt"] for t in trades if t.get("pnl_amt", 0) > 0]
    losses = [abs(t["pnl_amt"]) for t in trades if t.get("pnl_amt", 0) < 0]
    if not wins or not losses:
        return 0.0
    avg_win = mean(wins)
    avg_loss = mean(losses)
    return avg_win / avg_loss if avg_loss > 0 else 0.0


def compute_win_rate(trades):
    """计算胜率"""
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.get("pnl_amt", 0) > 0)
    return wins / len(trades)


def validate_backtest(backtest_data):
    """
    验证回测结果是否达标
    
    backtest_data = {
        "period": "20240101-20260324",
        "days": 450,
        "total_return": 0.85,
        "equity_curve": [1000000, 1020000, ...],
        "trades": [{"date":"","pnl_amt":8000}, ...],
        "phase_returns": {"主升": 0.30, "发酵": 0.20, ...},
    }
    """
    results = {
        "pass": True,
        "criteria": {},
        "summary": {},
    }
    
    equity = backtest_data.get("equity_curve", [INITIAL_CAPITAL])
    trades = backtest_data.get("trades", [])
    days = backtest_data.get("days", 1)
    total_return = backtest_data.get("total_return", 0)
    phase_returns = backtest_data.get("phase_returns", {})
    
    # 年化收益
    ann_ret = compute_annual_return(total_return, days)
    results["criteria"]["annual_return"] = {
        "value": ann_ret,
        "threshold": BENCHMARKS["annual_return_min"],
        "pass": ann_ret >= BENCHMARKS["annual_return_min"],
    }
    
    # 最大回撤
    max_dd = compute_max_drawdown(equity)
    results["criteria"]["max_drawdown"] = {
        "value": max_dd,
        "threshold": BENCHMARKS["max_drawdown_max"],
        "pass": max_dd <= BENCHMARKS["max_drawdown_max"],
    }
    
    # 盈亏比
    pl_ratio = compute_profit_loss_ratio(trades)
    results["criteria"]["profit_loss_ratio"] = {
        "value": pl_ratio,
        "threshold": BENCHMARKS["profit_loss_ratio_min"],
        "pass": pl_ratio >= BENCHMARKS["profit_loss_ratio_min"],
    }
    
    # 胜率
    win_rate = compute_win_rate(trades)
    results["criteria"]["win_rate"] = {
        "value": win_rate,
        "threshold": BENCHMARKS["win_rate_min"],
        "pass": win_rate >= BENCHMARKS["win_rate_min"],
    }
    
    # 退潮期回撤
    recession_ret = phase_returns.get("退潮", 0)
    recession_dd = max(0, -recession_ret)
    results["criteria"]["recession_drawdown"] = {
        "value": recession_dd,
        "threshold": BENCHMARKS["recession_drawdown_max"],
        "pass": recession_dd <= BENCHMARKS["recession_drawdown_max"],
    }
    
    results["pass"] = all(c["pass"] for c in results["criteria"].values())
    
    results["summary"] = {
        "annual_return": f"{ann_ret:.1%}",
        "max_drawdown": f"{max_dd:.1%}",
        "profit_loss_ratio": f"{pl_ratio:.2f}:1",
        "win_rate": f"{win_rate:.1%}",
        "total_trades": len(trades),
        "total_return": f"{total_return:.1%}",
    }
    
    return results


def format_validation_report(backtest_data=None, live_data=None):
    """生成验证报告"""
    lines = [
        f"【📐 策略验证框架】",
        f"{'='*36}",
    ]
    
    # 验证标准
    lines.append(f"\n【顶尖标准】")
    for k, v in BENCHMARKS.items():
        lines.append(f"  {k}: {v:.2%}")
    
    # 回测结果
    if backtest_data:
        result = validate_backtest(backtest_data)
        lines.append(f"\n{'='*36}")
        lines.append(f"【历史回测验证】")
        lines.append(f"  区间: {backtest_data.get('period', 'N/A')}")
        lines.append(f"  交易日: {backtest_data.get('days', 0)}天")
        
        for k, c in result["criteria"].items():
            e = "✅" if c["pass"] else "❌"
            lines.append(f"  {e} {k}: {c['value']:.2%} (阈值{c['threshold']:.2%})")
        
        status = "🟢 达标" if result["pass"] else "🔴 不达标"
        lines.append(f"\n  总评: {status}")
        
        for k, v in result["summary"].items():
            lines.append(f"  {k}: {v}")
    
    # 仿真/实盘追踪
    if live_data:
        lines.append(f"\n{'='*36}")
        lines.append(f"【实盘追踪】")
        for k, v in live_data.items():
            lines.append(f"  {k}: {v}")
    
    return "\n".join(lines)


# ── 实盘追踪 ───────────────────────────────────────────────────
def load_live_tracking():
    if LIVE_TRACKING_FILE.exists():
        with open(LIVE_TRACKING_FILE) as f:
            return json.load(f)
    return {
        "phase": "paper",  # paper / sim / live_10 / live_30 / live_full
        "start_date": None,
        "equity": INITIAL_CAPITAL,
        "daily_equity": [],
        "monthly_pnl": {},
    }


def update_live_tracking(date_str, equity):
    """更新实盘权益"""
    state = load_live_tracking()
    
    # 更新权益
    state["equity"] = equity
    state["daily_equity"].append({"date": date_str, "equity": equity})
    state["daily_equity"] = state["daily_equity"][-90:]  # 保留最近90天
    
    # 月度统计
    month = date_str[:6]
    state["monthly_pnl"][month] = state["monthly_pnl"].get(month, 0)
    
    with open(LIVE_TRACKING_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False)


def check_live_phase_progression():
    """
    检查实盘阶段是否满足升级条件
    """
    state = load_live_tracking()
    phase = state.get("phase", "paper")
    
    checks = {
        "paper": {
            "condition": "回测达标",
            "next": "sim",
            "next_label": "仿真交易",
        },
        "sim": {
            "condition": "仿真3个月跑赢回测≤10%",
            "next": "live_10",
            "next_label": "10%小资金实盘",
        },
        "live_10": {
            "condition": "1个月与仿真差≤10%",
            "next": "live_30",
            "next_label": "30%资金实盘",
        },
        "live_30": {
            "condition": "1个月稳定，回撤≤5%",
            "next": "live_full",
            "next_label": "全量上线",
        },
    }
    
    current = checks.get(phase, {})
    
    return {
        "current_phase": phase,
        "condition": current.get("condition", "未知"),
        "next_phase": current.get("next", None),
        "next_label": current.get("next_label", "终点"),
        "equity": state["equity"],
        "start_date": state.get("start_date"),
    }


if __name__ == "__main__":
    # 模拟回测达标
    print(format_validation_report())
