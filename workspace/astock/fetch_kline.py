"""
astock/fetch_kline
分批获取A股日K线，断了能续传
- 每获取N只股票立即写入磁盘（追加模式）
- 重启后自动跳过已有数据
- 最终合并成按月存储的Parquet
"""
import sys, os, time, random, json
sys.path.insert(0, os.path.dirname(__file__) or '.')

import baostock as bs
import pandas as pd
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__) or '.', '..', 'data', 'astock', 'kline')
STOCK_LIST_PATH = os.path.join(os.path.dirname(__file__) or '.', '..', 'data', 'astock', 'stock_list.json')
CHECKPOINT = os.path.join(DATA_DIR, 'fetch_checkpoint.json')
BATCH_DIR = os.path.join(DATA_DIR, 'batches')
BATCH_SIZE = 500  # 每批500只写一次磁盘

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BATCH_DIR, exist_ok=True)


def code_to_bs(code):
    code = str(code).zfill(6)
    return f"sh.{code}" if code.startswith(('6', '9')) else f"sz.{code}"


def get_stock_list():
    with open(STOCK_LIST_PATH) as f:
        return json.load(f)


def fetch_kline(code, start, end):
    rs = bs.query_history_k_data_plus(
        code_to_bs(code),
        "date,open,high,low,close,volume,amount",
        start_date=start, end_date=end,
        frequency="d", adjustflag="2"
    )
    if rs.error_msg != 'success':
        return None
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=['date','open','high','low','close','volume','amount'])
    df['code'] = code
    for col in ['open','high','low','close','volume','amount']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df


def save_checkpoint(idx, fetched, failed):
    with open(CHECKPOINT, 'w') as f:
        json.dump({'idx': idx, 'fetched': fetched, 'failed': failed, 'time': time.time()}, f)


def load_checkpoint():
    if os.path.exists(CHECKPOINT):
        with open(CHECKPOINT) as f:
            return json.load(f)
    return {'idx': 0, 'fetched': 0, 'failed': 0, 'time': 0}


def run(start_idx=0, end_idx=None, months=None):
    """
    分批获取K线。
    run()                   - 继续上次进度
    run(start_idx=0, end_idx=1000) - 只跑前1000只
    """
    stocks = get_stock_list()
    if end_idx is None:
        end_idx = len(stocks)
    stocks = stocks[start_idx:end_idx]
    print(f"[K线获取] {len(stocks)}只股票 ({start_idx}~{end_idx})")

    ckpt = load_checkpoint()
    ckpt['idx'] = max(ckpt['idx'], start_idx)
    save_checkpoint(ckpt['idx'], ckpt['fetched'], ckpt['failed'])

    bs.login()
    t0 = time.time()
    t0_session = t0
    batch_data = []
    batch_num = 0
    total_fetched = ckpt['fetched']
    total_failed = ckpt['failed']

    for i, stock in enumerate(stocks):
        global_idx = start_idx + i
        code = stock['code']

        # 跳过已完成的
        if global_idx < ckpt['idx']:
            continue

        df = fetch_kline(code, '2024-01-01', '2026-03-20')
        if df is not None and len(df) > 0:
            batch_data.append(df)
            total_fetched += 1
        else:
            total_failed += 1

        # 每100只报告
        if (i + 1) % 100 == 0:
            elapsed = time.time() - t0_session
            speed = 100 / elapsed if elapsed > 0 else 0
            total_elapsed = time.time() - t0
            print(f"  [{global_idx+1}/{end_idx}] 本批100只 {elapsed:.0f}秒 {speed:.1f}只/秒 "
                  f"| 累计成功{total_fetched} 失败{total_failed} | 总耗时{total_elapsed/60:.1f}分钟")
            t0_session = time.time()

        # 每BATCH_SIZE只写一次磁盘
        if len(batch_data) >= BATCH_SIZE:
            batch_num += 1
            merged = pd.concat(batch_data, ignore_index=True)
            # 用全局批次号：避免跨run覆盖
            global_batch = (global_idx // BATCH_SIZE) + 1
            batch_file = os.path.join(BATCH_DIR, f'batch_{global_batch:03d}.parquet')
            merged.to_parquet(batch_file, index=False)
            print(f"  💾 批次{batch_num}(全局#{global_batch})已写入: {len(merged)}行 → {batch_file}")
            batch_data = []
            save_checkpoint(global_idx + 1, total_fetched, total_failed)

        time.sleep(random.uniform(0.02, 0.07))

    # 写最后一批
    if batch_data:
        batch_num += 1
        merged = pd.concat(batch_data, ignore_index=True)
        global_batch = (global_idx // BATCH_SIZE) + 1
        batch_file = os.path.join(BATCH_DIR, f'batch_{global_batch:03d}.parquet')
        merged.to_parquet(batch_file, index=False)
        print(f"  💾 最后批次{batch_num}已写入: {len(merged)}行")

    save_checkpoint(end_idx, total_fetched, total_failed)
    bs.logout()
    print(f"\n完成! 成功{total_fetched}只 失败{total_failed}只 总耗时{time.time()-t0:.0f}秒")
    return total_fetched, total_failed


def merge_to_monthly():
    """把批次文件合并成按月存储"""
    batch_files = sorted([f for f in os.listdir(BATCH_DIR) if f.endswith('.parquet')])
    if not batch_files:
        print("无批次文件")
        return

    print(f"合并{batch_files}...")
    dfs = []
    for bf in batch_files:
        df = pd.read_parquet(os.path.join(BATCH_DIR, bf))
        dfs.append(df)
        print(f"  {bf}: {len(df)}行")

    full = pd.concat(dfs, ignore_index=True).sort_values(['date', 'code']).reset_index(drop=True)
    print(f"总计: {len(full)}行 {full['date'].min()} ~ {full['date'].max()}")

    full['ym'] = full['date'].str[:7]
    for ym, g in full.groupby('ym'):
        out = os.path.join(DATA_DIR, f'kline_{ym}.parquet')
        g.drop(columns=['ym']).to_parquet(out, index=False)
        print(f"  💾 {out} ({len(g)}行)")
    print("合并完成!")


if __name__ == '__main__':
    import fire
    fire.Fire()
