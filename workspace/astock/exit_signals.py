"""
astock.exit_signals
卖出信号评估
"""
from config import EXIT_RULES


def eval_exit_signals(conn, code=None):
    """
    评估持仓是否触发卖出信号。
    code=None时评估所有持仓。
    返回 [(code, name, buy_price, cur_price, profit_pct, hold_days, signal, action)]
    """
    if code:
        rows = conn.execute(
            "SELECT * FROM positions WHERE code=? AND status='持仓'", (code,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM positions WHERE status='持仓'"
        ).fetchall()

    results = []
    for r in rows:
        profit = r['profit_pct']
        hold = r['hold_days']
        action = None
        signal = ''

        if profit <= EXIT_RULES['stop_loss_pct']:
            signal = f"🚨 止损！浮亏{profit:.1f}%"
            action = "止损"
        elif profit >= EXIT_RULES['take_profit_pct']:
            signal = f"🎯 止盈！浮盈{profit:.1f}%"
            action = "止盈"
        elif hold >= EXIT_RULES['max_hold_days']:
            signal = f"⏰ 持有{hold}天，到期"
            action = "到期"

        results.append({
            'code': r['code'], 'name': r['name'],
            'buy_price': r['buy_price'], 'cur_price': r['cur_price'],
            'profit_pct': profit, 'hold_days': hold,
            'signal': signal, 'action': action,
        })
    return results
