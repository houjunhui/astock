#!/usr/bin/env python3
"""
A股百日涨停数据采集器
逐日抓取过去100个交易日的涨停股数据并缓存
"""

import akshare as ak
import pandas as pd
import warnings
import os
import json
import datetime
from pathlib import Path

warnings.filterwarnings('ignore')

DATA_DIR = "/home/gem/workspace/agent/workspace/data/astock/100day"
os.makedirs(DATA_DIR, exist_ok=True)

def get_recent_trading_days(n=100):
    """获取过去n个交易日列表"""
    trading_days = []
    date = datetime.date.today()
    # 周六日跳过，往回找
    while len(trading_days) < n:
        if date.weekday() < 5:  # Monday=0, ..., Friday=4
            trading_days.append(date.strftime("%Y%m%d"))
        date -= datetime.timedelta(days=1)
        # 安全兜底：最多跑400天
    return trading_days

def fetch_zt_pool(date):
    """获取指定日期涨停股数据"""
    try:
        df = ak.stock_zt_pool_em(date=date)
        records = []
        for _, row in df.iterrows():
            records.append({
                'date': date,
                'code': str(row['代码']),
                'name': row['名称'],
                'pct': float(row['涨跌幅']),
                'price': float(row['最新价']),
                'amount': float(row['成交额']) if pd.notna(row['成交额']) else 0,
                'float_mkt': float(row['流通市值']) if pd.notna(row['流通市值']) else 0,
                'total_mkt': float(row['总市值']) if pd.notna(row['总市值']) else 0,
                'turnover': float(row['换手率']) if pd.notna(row['换手率']) else 0,
                'sealed_capital': float(row['封板资金']) if pd.notna(row['封板资金']) else 0,
                'first_seal_time': str(row['首次封板时间']) if pd.notna(row['首次封板时间']) else '',
                'last_seal_time': str(row['最后封板时间']) if pd.notna(row['最后封板时间']) else '',
                'explode_count': int(row['炸板次数']) if pd.notna(row['炸板次数']) else 0,
                'continuous': str(row['涨停统计']) if pd.notna(row['涨停统计']) else '',
                'lb_count': int(row['连板数']) if pd.notna(row['连板数']) else 0,
                'sector': str(row['所属行业']) if pd.notna(row['所属行业']) else '',
            })
        return records
    except Exception as e:
        return {'error': str(e), 'date': date}

def main():
    print("="*60)
    print("A股百日涨停数据采集器")
    print("="*60)
    
    # 获取过去100个交易日
    days = get_recent_trading_days(100)
    print(f"过去100个交易日: {days[0]} ~ {days[-1]}")
    print(f"总天数: {len(days)}")
    print()
    
    # 逐日抓取
    all_data = {}
    error_log = []
    
    for i, day in enumerate(days):
        cache_file = f"{DATA_DIR}/{day}.json"
        
        # 已缓存则跳过
        if os.path.exists(cache_file):
            with open(cache_file) as f:
                all_data[day] = json.load(f)
            print(f"[{i+1}/{len(days)}] {day} ✓ (已缓存)")
            continue
        
        # 抓取
        records = fetch_zt_pool(day)
        if isinstance(records, dict) and 'error' in records:
            error_log.append(records)
            print(f"[{i+1}/{len(days)}] {day} ✗ {records['error'][:30]}")
            continue
        
        # 缓存
        with open(cache_file, 'w') as f:
            json.dump(records, f, ensure_ascii=False)
        all_data[day] = records
        print(f"[{i+1}/{len(days)}] {day} ✓ ({len(records)}只涨停)")
    
    # 汇总
    total_zt = sum(len(v) if isinstance(v, list) else 0 for v in all_data.values())
    print()
    print("="*60)
    print(f"采集完成！")
    print(f"成功天数: {len(all_data)}/{len(days)}")
    print(f"总涨停记录: {total_zt}条")
    print(f"失败天数: {len(error_log)}")
    if error_log:
        print("失败记录:", error_log[:5])
    
    # 生成汇总文件
    summary_file = f"{DATA_DIR}/summary.json"
    with open(summary_file, 'w') as f:
        json.dump({
            'days': len(all_data),
            'total_zt': total_zt,
            'date_range': [days[-1], days[0]],
            'days_data': {d: len(v) if isinstance(v, list) else 0 for d, v in all_data.items()}
        }, f, ensure_ascii=False, indent=2)
    print(f"\n汇总已保存: {summary_file}")

if __name__ == '__main__':
    main()
