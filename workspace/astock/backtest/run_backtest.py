"""
历史回测执行器 v1

用法:
  python3 -m astock.backtest.run_backtest [start_date] [end_date]

示例:
  python3 -m astock.backtest.run_backtest 20240101 20260324
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime, date, timedelta
import json
from pathlib import Path

INITIAL_CAPITAL = 1_000_000.0
WORKSPACE = Path("/home/gem/workspace/agent/workspace")


def run_backtest(start_date_str="20250101", end_date_str="20260324"):
    """
    执行历史回测
    """
    from astock.backtest.engine import BacktestEngine
    from astock.backtest.validator import validate_backtest, save_backtest_results
    
    print(f"【📊 历史回测】{start_date_str} → {end_date_str}")
    print("=" * 50)
    
    # 转换日期
    try:
        start = datetime.strptime(start_date_str, "%Y%m%d").date()
        end = datetime.strptime(end_date_str, "%Y%m%d").date()
    except ValueError:
        start = date.today() - timedelta(days=60)
        end = date.today()
        start_date_str = start.strftime("%Y%m%d")
        end_date_str = end.strftime("%Y%m%d")
    
    days = (end - start).days
    if days < 30:
        print("回测区间太短，至少30天")
        return None
    
    # 初始化回测引擎
    engine = BacktestEngine(initial_capital=INITIAL_CAPITAL)
    
    # 逐步回测（每日）
    current = start
    while current <= end:
        date_str = current.strftime("%Y%m%d")
        # 跳过周末
        if current.weekday() < 5:
            try:
                engine.run_day(date_str)
            except Exception as e:
                print(f"  ⚠️ {date_str} 回测异常: {e}")
        current += timedelta(days=1)
    
    # 统计结果
    equity = engine.equity_curve
    trades = engine.get_trades()
    
    total_return = (equity[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL if len(equity) > 1 else 0
    
    result = {
        "period": f"{start_date_str}-{end_date_str}",
        "days": len([start + timedelta(days=i) for i in range((end-start).days+1) if (start+timedelta(days=i)).weekday()<5]),
        "start_date": start_date_str,
        "end_date": end_date_str,
        "total_return": total_return,
        "final_equity": equity[-1] if equity else INITIAL_CAPITAL,
        "equity_curve": equity,
        "trades": trades,
    }
    
    # 验证
    from astock.backtest.validator import validate_backtest, format_validation_report, BENCHMARKS
    validation = validate_backtest(result)
    
    print(f"\n【回测结果】")
    for k, v in validation["summary"].items():
        print(f"  {k}: {v}")
    
    print(f"\n【验证】")
    for k, c in validation["criteria"].items():
        e = "✅" if c["pass"] else "❌"
        print(f"  {e} {k}: {c['value']:.2%} (阈值{c['threshold']:.2%})")
    
    status = "🟢 达标" if validation["pass"] else "🔴 不达标"
    print(f"\n总评: {status}")
    
    # 保存
    save_backtest_results(result)
    print(f"\n✅ 结果已保存")
    
    return result


if __name__ == "__main__":
    s = sys.argv[1] if len(sys.argv) > 1 else "20250101"
    e = sys.argv[2] if len(sys.argv) > 2 else date.today().strftime("%Y%m%d")
    run_backtest(s, e)
