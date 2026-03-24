"""
astock.backfill
历史涨停数据回填 - 用AKShare获取股票列表 + BaoStock K线计算每日涨停
"""
import time, random, sys, os, json
sys.path.insert(0, os.path.dirname(__file__) or '.')

import baostock as bs
import akshare as ak
from datetime import datetime, timedelta
from db import get_db, save_historical_zt

_bs_conn = None


def _bs_login():
    global _bs_conn
    if _bs_conn is None:
        _bs_conn = bs.login()
    return _bs_conn


def _bs_logout():
    global _bs_conn
    if _bs_conn:
        bs.logout()
        _bs_conn = None


def code_to_bs(code):
    code = str(code).zfill(6)
    if code.startswith(('6', '9')):
        return f"sh.{code}"
    return f"sz.{code}"


def get_stock_list():
    """从缓存文件获取股票列表（AKShare失效时的备用）"""
    cache_path = os.path.join(os.path.dirname(__file__) or '.', '..', 'data', 'astock', 'stock_list.json')
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)
    # 备用：硬编码主要股票
    stocks = []
    for i in range(600000, 605000): stocks.append({'code': f'{i:06d}', 'name': ''})
    for i in range(688000, 689100): stocks.append({'code': f'{i:06d}', 'name': ''})
    for i in range(1, 2500): stocks.append({'code': f'{i:06d}', 'name': ''})
    for i in range(300000, 304000): stocks.append({'code': f'{i:06d}', 'name': ''})
    return stocks


def find_limitups_for_stock(code, start_date, end_date):
    """
    返回 {(date, code, name, close, high, zt_price, pct_chg)}
    """
    bs_code = code_to_bs(code)
    _bs_login()
    rs = bs.query_history_k_data_plus(
        bs_code, "date,open,high,low,close,volume",
        start_date=start_date, end_date=end_date, frequency="d"
    )
    if rs.error_msg != 'success':
        return []

    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return []

    # 建立date→row映射
    date_map = {}
    for r in rows:
        try:
            date = r[0]
            date_map[date] = {
                'open': float(r[1]), 'high': float(r[2]),
                'low': float(r[3]), 'close': float(r[4]),
            }
        except (ValueError, IndexError):
            continue

    if not date_map:
        return []

    # 排序
    dates_sorted = sorted(date_map.keys())
    results = []

    for i, date in enumerate(dates_sorted):
        d = date_map[date]
        close = d['close']
        high = d['high']

        # 前一日收盘
        prev_close = None
        if i > 0:
            prev_date = dates_sorted[i - 1]
            prev_close = date_map[prev_date]['close']

        if not prev_close or prev_close <= 0:
            continue

        pct_chg = (close - prev_close) / prev_close * 100

        # 判断涨停
        is_cyb = code.startswith(('300', '301', '688', '8'))
        zt_pct = 20.0 if is_cyb else 10.0
        zt_price = round(prev_close * (1 + zt_pct / 100), 2)

        # 涨停条件：最高价触及涨停价（99.9%精度）+ 涨幅 >= 9.5%
        if high >= zt_price * 0.999 and pct_chg >= 9.5:
            results.append({
                'date': date,
                'code': code,
                'close': close,
                'high': high,
                'zt_price': zt_price,
                'pct_chg': round(pct_chg, 2),
            })

    return results


def backfill_one_date(date):
    """
    回填某一天的涨停股票（所有股票逐一检查）。
    date: 'YYYY-MM-DD' 格式
    """
    print(f"[回填] {date}")
    stocks = get_stock_list()
    print(f"[回填] 共{len(stocks)}只股票")

    all_zt = []

    for i, stock in enumerate(stocks):
        code = stock['code']
        name = stock['name']

        # 查该股在date附近的K线（往前查20天，确保有前一日数据）
        target = datetime.strptime(date, '%Y-%m-%d')
        start = (target - timedelta(days=25)).strftime('%Y-%m-%d')
        end = date

        results = find_limitups_for_stock(code, start, end)

        for r in results:
            if r['date'] == date:  # 只取当天
                r['name'] = name
                r['industry'] = ''
                all_zt.append(r)

        # 每20%进度报告一次
        pct = (i + 1) * 100 // len(stocks)
        if pct % 20 == 0 and (i == 0 or ((i + 1) * 100 // len(stocks)) != (i * 100 // len(stocks))):
            print(f"[回填] {date} {pct}% ({i+1}/{len(stocks)}) 发现{len(all_zt)}只涨停")

        # 无延迟：让BaoStock全速运行

    # 保存
    if all_zt:
        save_historical_zt(date, all_zt)
        print(f"[回填] {date} 完成: {len(all_zt)}只涨停")
    else:
        print(f"[回填] {date} 完成: 0只涨停")

    return all_zt


def backfill_recent(days=5):
    """
    回填最近N个交易日。
    """
    today = datetime.now()
    dates = []
    d = today - timedelta(days=1)
    while len(dates) < days and d <= today:
        if d.weekday() < 5:
            dates.append(d.strftime('%Y-%m-%d'))
        d += timedelta(days=1)

    print(f"[回填] 准备回填{len(dates)}个交易日: {dates[0]}~{dates[-1]}")
    total = 0
    for date in reversed(dates):
        result = backfill_one_date(date)
        total += len(result)
    print(f"[回填] 总计: {total}只涨停记录")
    return total


if __name__ == '__main__':
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    try:
        backfill_recent(days)
    finally:
        _bs_logout()
