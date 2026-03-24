"""
position_sqlite.py - SQLite持仓持久化
- 事务+行锁解决并发写入
- 同一股票同一日期去重（幂等性）
- 表结构与原CSV字段完全一致
"""
import sqlite3
import os
from pathlib import Path
from datetime import date, datetime
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "portfolio.db")

@contextmanager
def get_db(write=False):
    """线程安全的数据库连接（支持读写事务）"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if write:
            conn.execute("BEGIN IMMEDIATE")  # 行锁
        yield conn
        if write:
            conn.commit()
    except Exception:
        if write:
            conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    """初始化数据库（幂等）"""
    with get_db(write=True) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                buy_date TEXT NOT NULL,
                buy_price REAL NOT NULL,
                qty INTEGER NOT NULL,
                capital_pct REAL NOT NULL,
                stop_loss REAL NOT NULL,
                target_price REAL NOT NULL,
                buy_method TEXT DEFAULT '',
                current_price REAL NOT NULL,
                pnl_pct REAL NOT NULL DEFAULT 0,
                pnl_amt REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                level INTEGER DEFAULT 0,
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(code, buy_date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_pnl (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                buy_price REAL NOT NULL,
                close_price REAL NOT NULL,
                qty INTEGER NOT NULL,
                pnl_pct REAL NOT NULL,
                pnl_amt REAL NOT NULL,
                buy_method TEXT DEFAULT '',
                reason TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_code ON positions(code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_status ON positions(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pnl_date ON daily_pnl(date)")

def add_position(code, name, buy_price, qty, capital_pct, stop_loss, target_price,
                buy_method="", notes="", level=None):
    """
    新增持仓（去重幂等）
    返回: (success, is_new)
    - success=True, is_new=True  → 新增成功
    - success=False, is_new=False → 同一股票当日已存在，跳过
    """
    today = date.today().strftime("%Y-%m-%d")
    try:
        with get_db(write=True) as conn:
            conn.execute("""
                INSERT INTO positions
                (date, code, name, buy_date, buy_price, qty, capital_pct, stop_loss,
                 target_price, buy_method, current_price, pnl_pct, pnl_amt, status, level, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                today, code, name, today, buy_price, qty, capital_pct, stop_loss,
                target_price, buy_method, buy_price, 0.0, 0.0, '持仓',
                level or 0, notes
            ))
            return True, True
    except sqlite3.IntegrityError:
        return False, False  # 同一股票当日已存在

def reduce_position(code, reduce_qty, close_price, reason=""):
    """
    降仓（部分平仓，保留剩余持仓）
    reduce_qty: 本次卖出的数量
    返回: True=成功降仓, False=无持仓
    """
    today_s = date.today().strftime("%Y-%m-%d")
    with get_db(write=True) as conn:
        row = conn.execute(
            "SELECT * FROM positions WHERE code=? AND status='持仓' ORDER BY id DESC LIMIT 1",
            (code,)
        ).fetchone()
        if not row:
            return False

        buy_price = float(row['buy_price'])
        old_qty = int(row['qty'])
        if reduce_qty >= old_qty:
            # 数量不足降仓，当作全平
            reduce_qty = old_qty

        remaining_qty = old_qty - reduce_qty
        pnl_pct = round((close_price - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0.0
        pnl_amt = round((close_price - buy_price) * reduce_qty, 2)

        if remaining_qty >= 100:
            # 更新持仓记录（数量减少）
            conn.execute(
                "UPDATE positions SET qty=?, current_price=? WHERE id=?",
                (remaining_qty, close_price, row['id'])
            )
        else:
            # 剩余不足1手，直接清仓
            conn.execute(
                "UPDATE positions SET status=?, current_price=?, pnl_pct=?, pnl_amt=? WHERE id=?",
                (f"已平仓:{reason}", close_price, pnl_pct, pnl_amt, row['id'])
            )
            remaining_qty = 0

        # 记录本次降仓盈亏
        conn.execute("""
            INSERT INTO daily_pnl
            (date, code, name, buy_price, close_price, qty, pnl_pct, pnl_amt, buy_method, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (today_s, code, row['name'], buy_price, close_price,
              reduce_qty, pnl_pct, pnl_amt, row['buy_method'], f"降仓:{reason}"))
        return True


def close_position(code, close_price, reason=""):
    """
    平仓（仅对持仓中标的执行，幂等）
    返回: True=成功平仓, False=无持仓或已平
    """
    today = date.today().strftime("%Y-%m-%d")
    with get_db(write=True) as conn:
        # 原子操作：先查后改，用事务保证一致性
        row = conn.execute(
            "SELECT * FROM positions WHERE code=? AND status='持仓' ORDER BY id DESC LIMIT 1",
            (code,)
        ).fetchone()
        if not row:
            return False

        buy_price = float(row['buy_price'])
        qty = int(row['qty'])
        pnl_pct = round((close_price - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0.0
        pnl_amt = round((close_price - buy_price) * qty, 2)

        conn.execute(
            "UPDATE positions SET status=?, current_price=?, pnl_pct=?, pnl_amt=? WHERE id=?",
            (f"已平仓:{reason}", close_price, pnl_pct, pnl_amt, row['id'])
        )
        conn.execute("""
            INSERT INTO daily_pnl
            (date, code, name, buy_price, close_price, qty, pnl_pct, pnl_amt, buy_method, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (today, code, row['name'], buy_price, close_price, qty,
              pnl_pct, pnl_amt, row['buy_method'], reason))
        return True

def load_portfolio(status_filter=('持仓',)):
    """加载持仓，默认仅返回持仓中标的"""
    with get_db() as conn:
        if status_filter is None:
            rows = conn.execute("SELECT * FROM positions ORDER BY id DESC").fetchall()
        else:
            placeholders = ','.join('?' * len(status_filter))
            rows = conn.execute(
                f"SELECT * FROM positions WHERE status IN ({placeholders}) ORDER BY id DESC",
                status_filter
            ).fetchall()
    return [dict(r) for r in rows]

def get_today_trades():
    """获取今日所有交易（含已平仓）"""
    today = date.today().strftime("%Y-%m-%d")
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM positions WHERE buy_date=? ORDER BY id DESC",
            (today,)
        ).fetchall()
    return [dict(r) for r in rows]

def load_all_trades():
    """加载所有历史交易（for query.py）"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM positions WHERE status LIKE '已平仓%' ORDER BY buy_date DESC"
        ).fetchall()
    return [dict(r) for r in rows]

def get_daily_pnl(start_date=None, end_date=None):
    """加载每日盈亏"""
    with get_db() as conn:
        if start_date and end_date:
            rows = conn.execute(
                "SELECT * FROM daily_pnl WHERE date>=? AND date<=? ORDER BY date DESC",
                (start_date, end_date)
            ).fetchall()
        elif start_date:
            rows = conn.execute(
                "SELECT * FROM daily_pnl WHERE date>=? ORDER BY date DESC",
                (start_date,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM daily_pnl ORDER BY date DESC").fetchall()
    return [dict(r) for r in rows]

def init_files():
    """兼容旧API"""
    init_db()

# ── 旧CSV兼容层（自动迁移） ──────────────────────────────────────────
def migrate_csv_to_sqlite(csv_path):
    """一次性迁移历史CSV到SQLite"""
    if not os.path.exists(csv_path):
        return
    import csv
    with get_db(write=True) as conn:
        with open(csv_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO positions
                        (date, code, name, buy_date, buy_price, qty, capital_pct, stop_loss,
                         target_price, buy_method, current_price, pnl_pct, pnl_amt, status, level, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        row.get("date",""), row.get("code",""), row.get("name",""),
                        row.get("buy_date",""), float(row.get("buy_price",0) or 0),
                        int(row.get("qty",0) or 0), float(row.get("capital_pct",0) or 0),
                        float(row.get("stop_loss",0) or 0), float(row.get("target_price",0) or 0),
                        row.get("buy_method",""), float(row.get("current_price",0) or 0),
                        float(row.get("pnl_pct",0) or 0), float(row.get("pnl_amt",0) or 0),
                        row.get("status","持仓"), int(row.get("level",0) or 0),
                        row.get("notes","")
                    ))
                except Exception:
                    pass

if __name__ == "__main__":
    init_db()
    print("✅ SQLite数据库初始化完成:", DB_PATH)
