"""
全链路可追溯数据体系 v1

存储内容:
- 行情快照: 每日竞价/K线/收盘数据
- 选股记录: 候选股→评分→买入全流程
- 交易记录: 买卖价格/数量/原因/费用
- 风控日志: 每次触发详细记录
- 复盘报告: 每日自动生成

表结构:
- market_snapshots: 每日行情快照
- stock_scores: 候选股评分明细
- execution_log: 操作执行日志
- risk_events: 风控触发事件
- daily_reviews: 每日复盘报告
"""

import sqlite3
import json
import os
from datetime import datetime, date
from pathlib import Path

WORKSPACE = Path("/home/gem/workspace/agent/workspace")
DB_DIR = WORKSPACE / "astock" / "position"
DB_PATH = DB_DIR / "execution_trace.db"

TRACE_DB = DB_DIR / "execution_trace.db"


def get_trace_db():
    if not TRACE_DB.exists():
        init_trace_db()
    return sqlite3.connect(TRACE_DB)


def init_trace_db():
    """初始化追溯数据库"""
    TRACE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(TRACE_DB)
    c = conn.cursor()
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS market_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            lb INTEGER,
            auction_chg REAL,
            auction_amount REAL,
            close_price REAL,
            vr REAL,
            turnover REAL,
            limit_up_type TEXT,
            seal_rate REAL,
            sector TEXT,
            market_temp REAL,
            phase TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(date, code)
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS stock_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            tier TEXT,
            score_total REAL,
            score_detail TEXT,
            phase TEXT,
            market_temp REAL,
            auction_chg REAL,
            lb INTEGER,
            action TEXT,
            reason TEXT,
            position_pct REAL,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(date, code)
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS execution_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            action TEXT NOT NULL,
            price REAL,
            qty INTEGER,
            amount REAL,
            pnl_pct REAL,
            pnl_amt REAL,
            commission REAL,
            slippage REAL,
            reason TEXT,
            phase TEXT,
            tier TEXT,
            market_temp REAL,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS risk_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            code TEXT,
            event_type TEXT NOT NULL,
            trigger_condition TEXT,
            action_taken TEXT,
            price REAL,
            pnl_impact REAL,
            market_temp REAL,
            phase TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            total_pnl REAL,
            total_pnl_pct REAL,
            win_count INTEGER,
            loss_count INTEGER,
            win_rate REAL,
            avg_win_pct REAL,
            avg_loss_pct REAL,
            circuit_triggered INTEGER DEFAULT 0,
            max_drawdown REAL,
            factor_ic TEXT,
            top_factors TEXT,
            weak_factors TEXT,
            report TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    
    conn.commit()
    conn.close()


# ── 行情快照 ───────────────────────────────────────────────────
def log_market_snapshot(code, name, date_str, data):
    """记录行情快照"""
    try:
        conn = get_trace_db()
        conn.execute("""
            INSERT OR REPLACE INTO market_snapshots
            (date, code, name, lb, auction_chg, auction_amount, close_price,
             vr, turnover, limit_up_type, seal_rate, sector, market_temp, phase)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            date_str, code, name,
            data.get("lb", 0),
            data.get("auction_chg", 0),
            data.get("auction_amount", 0),
            data.get("close_price", 0),
            data.get("vr", 0),
            data.get("turnover", 0),
            data.get("limit_up_type", ""),
            data.get("limit_up_suc_rate", 0),
            data.get("sector", ""),
            data.get("market_temp", 0),
            data.get("phase", ""),
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        pass


def get_market_snapshot(code, date_str):
    """获取某日行情快照"""
    conn = get_trace_db()
    c = conn.cursor()
    c.execute("SELECT * FROM market_snapshots WHERE date=? AND code=?", (date_str, code))
    row = c.fetchone()
    conn.close()
    return row


# ── 选股评分记录 ───────────────────────────────────────────────
def log_stock_score(code, name, date_str, score_data):
    """记录选股评分"""
    try:
        conn = get_trace_db()
        conn.execute("""
            INSERT OR REPLACE INTO stock_scores
            (date, code, name, tier, score_total, score_detail,
             phase, market_temp, auction_chg, lb, action, reason, position_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            date_str, code, name,
            score_data.get("tier", ""),
            score_data.get("total_score", 0),
            json.dumps(score_data.get("score_detail", {}), ensure_ascii=False),
            score_data.get("phase", ""),
            score_data.get("market_temp", 0),
            score_data.get("auction_chg", 0),
            score_data.get("lb", 0),
            score_data.get("action", ""),
            score_data.get("reason", ""),
            score_data.get("position_pct", 0),
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        pass


# ── 执行日志 ───────────────────────────────────────────────────
def log_execution(date_str, code, name, action, price, qty, 
                   pnl_pct=0, pnl_amt=0, commission=0, slippage=0,
                   reason="", phase="", tier="", market_temp=0):
    """记录交易执行"""
    try:
        conn = get_trace_db()
        conn.execute("""
            INSERT INTO execution_log
            (date, code, name, action, price, qty, amount, pnl_pct, pnl_amt,
             commission, slippage, reason, phase, tier, market_temp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            date_str, code, name, action, price, qty,
            round(price * qty, 2) if price and qty else 0,
            pnl_pct, pnl_amt, commission, slippage,
            reason, phase, tier, market_temp,
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        pass


# ── 风控事件 ───────────────────────────────────────────────────
def log_risk_event(date_str, code, event_type, trigger="", action="",
                    price=0, pnl_impact=0, market_temp=0, phase=""):
    """记录风控事件"""
    try:
        conn = get_trace_db()
        conn.execute("""
            INSERT INTO risk_events
            (date, code, event_type, trigger_condition, action_taken,
             price, pnl_impact, market_temp, phase)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (date_str, code, event_type, trigger, action, price, pnl_impact, market_temp, phase))
        conn.commit()
        conn.close()
    except Exception as e:
        pass


def get_risk_events(date_str=None, code=None):
    """查询风控事件"""
    conn = get_trace_db()
    sql = "SELECT * FROM risk_events WHERE 1=1"
    params = []
    if date_str:
        sql += " AND date=?"
        params.append(date_str)
    if code:
        sql += " AND code=?"
        params.append(code)
    sql += " ORDER BY created_at DESC"
    c = conn.cursor()
    c.execute(sql, params)
    rows = c.fetchall()
    conn.close()
    return rows


# ── 每日复盘 ───────────────────────────────────────────────────
def generate_daily_review(date_str):
    """生成每日复盘报告"""
    conn = get_trace_db()
    
    # 交易统计
    c = conn.cursor()
    c.execute("""
        SELECT action, COUNT(*), SUM(pnl_amt), AVG(pnl_pct)
        FROM execution_log WHERE date=? AND action IN ('买入','卖出')
        GROUP BY action
    """, (date_str,))
    trade_stats = {row[0]: {"count": row[1], "pnl": row[2], "avg_pct": row[3]} for row in c.fetchall()}
    
    # 风控事件
    c.execute("SELECT COUNT(*) FROM risk_events WHERE date=?", (date_str,))
    risk_count = c.fetchone()[0]
    
    # 最大回撤（从daily_reviews）
    c.execute("SELECT MAX(drawdown) FROM market_snapshots WHERE date=?", (date_str,))
    max_dd = c.fetchall()
    
    conn.close()
    
    # 胜率
    buys = trade_stats.get("买入", {})
    sells = trade_stats.get("卖出", {})
    total_trades = sells.get("count", 0)
    
    report = {
        "date": date_str,
        "trade_count": total_trades,
        "total_pnl": sells.get("pnl", 0),
        "win_count": 0,
        "loss_count": 0,
        "win_rate": 0,
        "avg_win_pct": 0,
        "avg_loss_pct": 0,
        "risk_events": risk_count,
        "max_drawdown": 0,
    }
    
    return report


def format_execution_trace(date_str):
    """格式化执行追溯报告"""
    conn = get_trace_db()
    
    lines = [
        f"【📋 执行全链路追溯】{date_str}",
        f"{'='*36}",
    ]
    
    # 选股记录
    c = conn.cursor()
    c.execute("SELECT code, name, tier, score_total, auction_chg, lb, action, position_pct FROM stock_scores WHERE date=?", (date_str,))
    scores = c.fetchall()
    if scores:
        lines.append(f"\n【选股记录】{len(scores)}只")
        for row in scores:
            code, name, tier, score, chg, lb, action, pos = row
            e = "✅买" if action == "买入" else "❌弃"
            lines.append(f"  {e} {code} {name} {tier}级 评分{score:.1f} 竞价{chg:+.2f}% {lb}板 → {action} {pos:.0%}" if pos else f"  {e} {code} {name} {tier}级 → {action}")
    
    # 交易执行
    c.execute("SELECT code, name, action, price, qty, pnl_amt, reason FROM execution_log WHERE date=? AND action IN ('买入','卖出')", (date_str,))
    trades = c.fetchall()
    if trades:
        lines.append(f"\n【交易执行】{len(trades)}笔")
        for row in trades:
            code, name, action, price, qty, pnl, reason = row
            pnl_str = f"{pnl:+,.0f}元" if pnl else ""
            lines.append(f"  {action} {code} {name} {price}元 × {qty}手 = {pnl_str} 原因:{reason}")
    
    # 风控事件
    c.execute("SELECT code, event_type, trigger_condition, action_taken FROM risk_events WHERE date=?", (date_str,))
    risks = c.fetchall()
    if risks:
        lines.append(f"\n【风控事件】{len(risks)}次")
        for row in risks:
            code, etype, trigger, action = row
            lines.append(f"  🔥 {code} {etype} 触发:{trigger} → {action}")
    
    conn.close()
    
    if not trades and not scores and not risks:
        lines.append("\n  今日无任何操作记录")
    
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    date_str = sys.argv[1] if len(sys.argv) > 1 else date.today().strftime("%Y%m%d")
    print(format_execution_trace(date_str))
