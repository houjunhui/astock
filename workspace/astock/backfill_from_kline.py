"""
astock/backfill_from_kline
从本地K线数据计算每日涨停/炸板，写入historical_zt表
"""
import sys, os, glob
sys.path.insert(0, os.path.dirname(__file__) or '.')

import pandas as pd
from db import get_db, save_historical_zt


def find_limitups_from_klines(kline_df, date):
    """
    从K线数据中找某日涨停股票。
    kline_df需包含: date, code, close, high, open
    """
    day_df = kline_df[kline_df['date'] == date].copy()
    if day_df.empty:
        return []

    dates = sorted(kline_df['date'].unique())
    day_idx = dates.index(date)
    if day_idx == 0:
        return []

    prev_date = dates[day_idx - 1]
    prev = kline_df[kline_df['date'] == prev_date].set_index('code')

    results = []
    for _, row in day_df.iterrows():
        code = row['code']
        close = row['close']
        high = row['high']
        open_p = row['open']

        if code not in prev.index:
            continue
        prev_close = prev.loc[code, 'close']
        if not (prev_close > 0 and close > 0):
            continue

        pct_chg = (close - prev_close) / prev_close * 100

        # 涨停判定：科创板/创业板20%，主板10%
        is_cyb = code.startswith(('300', '301', '688', '8'))
        zt_pct = 20.0 if is_cyb else 10.0
        zt_price = round(prev_close * (1 + zt_pct / 100), 2)

        # 涨停：最高价触及涨停价（99.9%精度）且涨幅>=9.5%
        is_zt = (high >= zt_price * 0.999 and pct_chg >= 9.5)
        # 炸板：涨停了但又打开，收盘价<涨停价
        is_zb = is_zt and close < zt_price * 0.999

        results.append({
            'code': code,
            'close': close,
            'high': high,
            'zt_price': zt_price,
            'pct_chg': round(pct_chg, 2),
            'is_zt': is_zt,
            'is_zb': is_zb,
        })

    return results


def compute_historical_zt(ym):
    """
    计算某月每日涨停/炸板，写入数据库。
    """
    from fetch_kline import load_kline_month
    df = load_kline_month(ym)
    if df is None:
        print(f"无{ym}数据")
        return

    dates = sorted(df['date'].unique())
    print(f"[{ym}] 共{len(dates)}个交易日")

    total_zt = 0
    total_zb = 0

    for date in dates:
        results = find_limitups_from_klines(df, date)
        zt_stocks = [r for r in results if r['is_zt']]
        zb_stocks = [r for r in results if r['is_zb']]

        # 保存涨停股
        for r in zt_stocks:
            conn = get_db()
            conn.execute("""
                INSERT OR REPLACE INTO historical_zt
                (date, code, name, close, high, zt_price, pct_chg, reason, industry, lb)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (date, r['code'], '', r['close'], r['high'],
                  r['zt_price'], r['pct_chg'], 'ZT', '', 0))
            conn.commit()
            conn.close()

        total_zt += len(zt_stocks)
        total_zb += len(zb_stocks)

        if len(zt_stocks) > 0:
            print(f"  {date}: 涨停{len(zt_stocks)}只 炸板{len(zb_stocks)}只")

    print(f"[{ym}] 完成: 涨停{total_zt}只 炸板{total_zb}只")
    return total_zt, total_zb


def compute_all_months():
    """对所有已下载的月份数据执行计算"""
    from fetch_kline import DATA_DIR
    files = sorted(glob.glob(os.path.join(DATA_DIR, 'kline_*.parquet')))
    if not files:
        print("无K线文件，请先运行 fetch_kline.py")
        return

    print(f"找到{len(files)}个月份文件")
    grand_zt = 0
    grand_zb = 0
    for f in files:
        ym = os.path.basename(f).replace('kline_', '').replace('.parquet', '')
        zt, zb = compute_historical_zt(ym)
        grand_zt += zt
        grand_zb += zb

    print(f"\n总计: 涨停{grand_zt}只 炸板{grand_zb}只")
