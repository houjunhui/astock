#!/usr/bin/env python3
"""
回测引擎 - 基于历史K线模拟交易
用法:
  from astock.backtest.engine import BacktestEngine
  engine = BacktestEngine(start_date="20260101", end_date="20260324")
  result = engine.run(get_params())
  print(result)
"""

import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import defaultdict
from datetime import datetime, timedelta

CAPITAL = 1_000_000


class BacktestEngine:
    def __init__(self, start_date="20260101", end_date="20260324",
                 initial_capital=1_000_000, commission=0.0003, stamp_tax=0.001):
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.commission = commission  # 佣金0.03%
        self.stamp_tax = stamp_tax     # 印花税0.1%（卖出时）
        self.params = {}

        # 状态
        self.cash = initial_capital
        self.positions = {}  # {code: {qty, buy_price, buy_date, level, ...}}
        self.closed = []     # 已平仓交易
        self.daily_pnl = []  # 每日盈亏
        self.equity_curve = []  # 每日权益

        # 统计
        self.stats = {
            "total_trades": 0,
            "win_trades": 0,
            "lose_trades": 0,
            "total_pnl": 0,
            "max_drawdown": 0,
            "sharpe_ratio": 0,
            "calmar_ratio": 0,
            "win_rate": 0,
            "profit_loss_ratio": 0,
        }

    def run(self, params, verbose=False):
        """运行回测，返回结果"""
        self.params = params
        self.cash = self.initial_capital
        self.positions = {}
        self.closed = []
        self.equity_curve = []
        self.daily_pnl = []

        # 加载历史数据
        trade_dates = self._get_trade_dates()
        for date_str in trade_dates:
            self._run_day(date_str, verbose)

        self._compute_stats()
        return self._make_result()

    def _get_trade_dates(self):
        """生成回测期间的交易日列表"""
        dates = []
        d = datetime.strptime(self.start_date, "%Y%m%d")
        end = datetime.strptime(self.end_date, "%Y%m%d")
        while d <= end:
            if d.weekday() < 5:  # 排除周末
                dates.append(d.strftime("%Y%m%d"))
            d += timedelta(days=1)
        return dates

    def _run_day(self, date_str, verbose):
        """每日运行逻辑"""
        raise NotImplementedError("子类需实现 _run_day")

    def _buy(self, code, price, qty, date_str, level=1):
        """模拟买入（含手续费）"""
        cost = price * qty
        commission = cost * self.commission
        if self.cash < cost + commission:
            return False
        self.cash -= (cost + commission)
        self.positions[code] = {
            "code": code, "buy_price": price, "qty": qty,
            "buy_date": date_str, "level": level,
            "stop_loss": price * (1 - self.params.get("stop_loss_default", 0.04)),
            "target": price * (1 + self._get_target_pct(level)),
        }
        return True

    def _sell(self, code, price, reason="", date_str=""):
        """模拟卖出（含佣金+印花税）"""
        if code not in self.positions:
            return
        pos = self.positions[code]
        qty = pos["qty"]
        revenue = price * qty
        commission = revenue * self.commission
        stamp = revenue * self.stamp_tax if reason != "止损" else 0  # 止损免印花税
        net_revenue = revenue - commission - stamp

        buy_cost = pos["buy_price"] * qty
        pnl = net_revenue - buy_cost
        pnl_pct = pnl / buy_cost * 100

        self.closed.append({
            "code": code,
            "buy_date": pos["buy_date"],
            "buy_price": pos["buy_price"],
            "close_date": date_str,
            "close_price": price,
            "qty": qty,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "reason": reason,
            "level": pos["level"],
        })

        self.cash += net_revenue
        del self.positions[code]
        return pnl

    def _get_target_pct(self, level):
        if level >= 3:
            return self.params.get("target_3board_plus", 0.12)
        elif level == 2:
            return self.params.get("target_2board", 0.09)
        else:
            return self.params.get("target_1board", 0.07)

    def _get_stop_loss_pct(self):
        return self.params.get("stop_loss_default", 0.04)

    def _compute_stats(self):
        """计算统计指标"""
        if not self.closed:
            return

        pnls = [t["pnl"] for t in self.closed]
        pnl_pcts = [t["pnl_pct"] for t in self.closed]

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_pnl = sum(pnls)
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 1

        # 最大回撤
        peak = 0
        max_dd = 0
        equity = self.initial_capital
        for pnl in pnls:
            equity += pnl
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        # 夏普比率（日收益/日波动率，年化）
        daily_returns = [t["pnl_pct"] / 100 for t in self.closed]
        if len(daily_returns) > 1:
            mean_ret = sum(daily_returns) / len(daily_returns)
            std_ret = (sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)) ** 0.5
            sharpe = (mean_ret / std_ret * (252 ** 0.5)) if std_ret > 0 else 0
        else:
            sharpe = 0

        # 卡玛比率
        annual_return = total_pnl / self.initial_capital * 100
        max_dd_pct = max_dd / self.initial_capital * 100 if self.initial_capital > 0 else 0
        calmar = annual_return / max_dd_pct if max_dd_pct > 0 else 0

        self.stats = {
            "total_trades": len(self.closed),
            "win_trades": len(wins),
            "lose_trades": len(losses),
            "win_rate": len(wins) / len(self.closed) * 100 if self.closed else 0,
            "total_pnl": total_pnl,
            "avg_pnl": total_pnl / len(self.closed),
            "profit_loss_ratio": avg_win / avg_loss if avg_loss > 0 else 0,
            "max_drawdown": max_dd,
            "max_drawdown_pct": max_dd_pct,
            "sharpe_ratio": round(sharpe, 2),
            "calmar_ratio": round(calmar, 2),
            "annual_return_pct": round(annual_return, 2),
            "by_level": self._by_level(),
            "by_reason": self._by_reason(),
        }

    def _by_level(self):
        result = defaultdict(lambda: {"count": 0, "win": 0, "pnl": 0})
        for t in self.closed:
            lb = t["level"]
            result[lb]["count"] += 1
            if t["pnl"] > 0:
                result[lb]["win"] += 1
            result[lb]["pnl"] += t["pnl"]
        return dict(result)

    def _by_reason(self):
        result = defaultdict(lambda: {"count": 0, "pnl": 0})
        for t in self.closed:
            reason = t.get("reason", "其他") or "其他"
            result[reason]["count"] += 1
            result[reason]["pnl"] += t["pnl"]
        return dict(result)

    def _make_result(self):
        return {
            "params": self.params,
            "stats": self.stats,
            "closed": self.closed,
            "initial_capital": self.initial_capital,
            "final_capital": self.cash + sum(
                p["buy_price"] * p["qty"] for p in self.positions.values()
            ),
        }


def format_backtest_result(result):
    """格式化回测结果"""
    s = result["stats"]
    lines = [
        "【📈 回测结果】",
        f"  交易次数: {s['total_trades']}笔 | 胜: {s['win_trades']} 负: {s['lose_trades']} | 胜率: {s['win_rate']:.1f}%",
        f"  总盈亏: {s['total_pnl']:+,.0f}元 | 均盈: {s['avg_pnl']:+,.0f}元",
        f"  盈亏比: {s['profit_loss_ratio']:.2f}",
        f"  最大回撤: {s['max_drawdown']:+,.0f}元 ({s['max_drawdown_pct']:.1f}%)",
        f"  夏普比率: {s['sharpe_ratio']:.2f}",
        f"  卡玛比率: {s['calmar_ratio']:.2f}",
        f"  年化收益: {s['annual_return_pct']:.1f}%",
        f"  最终资金: {result['final_capital']:,.0f}元",
    ]
    if s.get("by_level"):
        lines.append("  【按板位】")
        for lb in sorted(s["by_level"].keys()):
            d = s["by_level"][lb]
            wr = d["win"] / d["count"] * 100 if d["count"] else 0
            lines.append(f"    {lb}板: {d['count']}笔 胜率{wr:.0f}% 盈亏{d['pnl']:+,.0f}元")
    if s.get("by_reason"):
        lines.append("  【按平仓原因】")
        for reason, d in sorted(s["by_reason"].items(), key=lambda x: x[1]["count"], reverse=True):
            lines.append(f"    {reason}: {d['count']}笔 盈亏{d['pnl']:+,.0f}元")
    return "\n".join(lines)


def is_valid_params(stats):
    """
    判断参数是否通过验证标准
    年化≥30%, 最大回撤≤15%, 胜率≥55%, 盈亏比≥1.5, 卡玛≥2
    """
    checks = {
        "年化收益率≥30%": stats.get("annual_return_pct", 0) >= 30,
        "最大回撤≤15%": stats.get("max_drawdown_pct", 999) <= 15,
        "胜率≥55%": stats.get("win_rate", 0) >= 55,
        "盈亏比≥1.5": stats.get("profit_loss_ratio", 0) >= 1.5,
        "卡玛比率≥2": stats.get("calmar_ratio", 0) >= 2,
    }
    failed = [k for k, v in checks.items() if not v]
    passed = all(checks.values())
    return passed, checks, failed


if __name__ == "__main__":
    # 简单测试
    engine = BacktestEngine("20260101", "20260324")
    from astock.strategy_params import get_params
    result = engine.run(get_params())
    print(format_backtest_result(result))
    passed, checks, failed = is_valid_params(result["stats"])
    print(f"\n通过验证: {passed}")
    if failed:
        print(f"未通过: {failed}")
