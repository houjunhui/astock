"""
astock.db
数据库操作
"""
import sqlite3, json
from config import DB_PATH


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库表结构"""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL, code TEXT NOT NULL, name TEXT,
            lb INTEGER, industry TEXT,
            trend TEXT, rsi REAL, vr REAL,
            macd_state TEXT, vol_status TEXT, price_vs_ma20 TEXT,
            jb_prob REAL, dz_prob REAL,
            dist_N INTEGER, dist_N1 INTEGER, dist_N2 INTEGER,
            signal TEXT,
            base_prob REAL, adj_factor REAL,
            outcome TEXT, detail TEXT, actual_boards INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, code)
        );

        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            total_zt INTEGER, avg_jb_prob REAL,
            zt_rate REAL, dz_rate REAL,
            phase TEXT, phase_name TEXT,
            market_avg_zt REAL, market_avg_dz REAL,
            market_trend TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS calibration (
            bucket INTEGER PRIMARY KEY,
            total_sample_count INTEGER DEFAULT 0,
            total_predicted_prob_sum REAL DEFAULT 0,
            total_actual_jb_sum INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS coef_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, coef_name TEXT, old_value REAL, new_value REAL,
            reason TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, coef_name)
        );

        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL, name TEXT,
            buy_date TEXT, buy_price REAL,
            cur_price REAL, profit_pct REAL,
            hold_days INTEGER DEFAULT 0, status TEXT DEFAULT '持仓',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS historical_zt (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            code TEXT NOT NULL, name TEXT,
            close REAL, high REAL, zt_price REAL,
            pct_chg REAL, turn_rate REAL,
            reason TEXT,
            industry TEXT,
            lb INTEGER DEFAULT 1,
            UNIQUE(date, code)
        );

        CREATE INDEX IF NOT EXISTS idx_zt_date ON historical_zt(date);
        CREATE INDEX IF NOT EXISTS idx_zt_code ON historical_zt(code);
    """)
    conn.commit()
    conn.close()
    return DB_PATH


def save_predictions(date, results, phase='未知', phase_name=''):
    """批量保存预测结果"""
    conn = get_db()
    for r in results:
        p = r['prediction']
        conn.execute("""
            INSERT OR REPLACE INTO predictions
            (date, code, name, lb, industry, trend, rsi, vr, macd_state, vol_status,
             price_vs_ma20, jb_prob, dz_prob, dist_N, dist_N1, dist_N2, signal,
             base_prob, adj_factor, outcome, detail, actual_boards)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL)
        """, (date, r['code'], r['name'], r['lb'], r.get('industry', ''),
              r.get('trend', ''), r.get('rsi'), r.get('vr'),
              r.get('macd_state', ''), r.get('vol_status', ''),
              r.get('price_vs_ma20', ''),
              p['jb_prob'], p['dz_prob'],
              p.get('distribution', {}).get('N+1', 0),
              p.get('distribution', {}).get('继续', 0),
              p.get('distribution', {}).get('断板', 0),
              str(p['signal']), p.get('base_prob', 0), p.get('adj_factor', 1),
              ))
    conn.commit()
    conn.close()


def save_daily_stats(date, total_zt, avg_jb, phase, phase_name,
                      mkt_avg_zt=0, mkt_avg_dz=0, mkt_trend=''):
    conn = get_db()
    conn.execute("""
        INSERT OR REPLACE INTO daily_stats
        (date, total_zt, avg_jb_prob, phase, phase_name,
         market_avg_zt, market_avg_dz, market_trend)
        VALUES (?,?,?,?,?,?,?,?)
    """, (date, total_zt, avg_jb, phase, phase_name, mkt_avg_zt, mkt_avg_dz, mkt_trend))
    conn.commit()
    conn.close()


def save_historical_zt(date, stocks):
    """
    保存某日涨停股票列表。
    stocks: list of dict {code, name, close, high, zt_price, pct_chg, turn_rate, reason, industry}
    """
    if not stocks:
        return
    conn = get_db()
    for s in stocks:
        conn.execute("""
            INSERT OR REPLACE INTO historical_zt
            (date, code, name, close, high, zt_price, pct_chg, turn_rate, reason, industry, lb)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (date, s['code'], s['name'], s.get('close'), s.get('high'),
              s.get('zt_price'), s.get('pct_chg'), s.get('turn_rate'),
              s.get('reason', ''), s.get('industry', ''), s.get('lb', 1)))
    conn.commit()
    conn.close()


def get_historical_zt(date):
    """获取某日涨停股票列表"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM historical_zt WHERE date=? ORDER BY pct_chg DESC", (date,)
    ).fetchall()
    conn.close()
    return rows
