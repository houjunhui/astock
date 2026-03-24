#!/usr/bin/env python3
"""
A股每日竞价复盘 v2 - 基于AKShare
每天22:00运行:
1. 获取今日涨停股池，追加到历史数据
2. 补充新股票的K线
3. 跑晋级分析，更新策略报告
4. 输出复盘报告
"""

import subprocess, json, datetime, os, sys, time
import akshare as ak
import pandas as pd

DATA_DIR = "/home/gem/workspace/agent/workspace/data/astock/100day"
KLINE_DIR = "/home/gem/workspace/agent/workspace/data/astock/100day/klines"
STRATEGY_FILE = "/home/gem/workspace/agent/workspace/astock_strategy/竞价选股策略.md"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(KLINE_DIR, exist_ok=True)

# ========================
# 1. 采集今日涨停股
# ========================
def get_today_zt():
    """获取今日涨停股池"""
    today = datetime.date.today().strftime("%Y%m%d")
    try:
        df = ak.stock_zt_pool_em(date=today)
        if len(df) > 0:
            records = []
            for _, row in df.iterrows():
                records.append({
                    'code': str(row.get('代码','')),
                    'name': str(row.get('名称','')),
                    'lb_count': int(row.get('连板数', 1)) if pd.notna(row.get('连板数')) else 1,
                    'industry': str(row.get('所属行业','')),
                })
            return today, records
    except Exception as e:
        print(f"获取今日涨停失败: {e}", file=sys.stderr)
    return today, []

# ========================
# 2. K线获取
# ========================
def curl_kline_tencents(code):
    """通过腾讯接口获取日K线"""
    mkt = 'sh' if code.startswith('6') or code.startswith('9') else 'sz'
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?_var=kline_dayhfq&param={mkt}{code},day,,,40,qfq")
    try:
        p = subprocess.Popen(['curl', '-s', url], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        out, _ = p.communicate()
        txt = out.decode('utf-8', errors='ignore')
        if not txt or 'kline_dayhfq=' not in txt:
            return None
        txt = txt.split('=', 1)[1]
        data = json.loads(txt)
        qfq = data.get('data', {}).get(mkt+code, {})
        days = qfq.get('day', []) or qfq.get('qfqday', [])
        result = {}
        for d in days[-60:]:
            if len(d) >= 6:
                dt = d[0]
                result[dt] = {
                    'open': float(d[1]), 'close': float(d[2]),
                    'high': float(d[3]), 'low': float(d[4]),
                    'vol': float(d[5]) if len(d) > 5 else 0
                }
        return result
    except:
        return None

def fetch_klines(codes):
    """批量获取K线，已有的跳过"""
    fetched = 0
    for code in codes:
        kf = f"{KLINE_DIR}/{code}.json"
        existing = {}
        if os.path.exists(kf):
            with open(kf) as f:
                existing = json.load(f)
        kl = curl_kline_tencents(code)
        if kl:
            existing.update(kl)
            with open(kf, 'w') as f:
                json.dump(existing, f, ensure_ascii=False)
            fetched += 1
        time.sleep(0.1)
    return fetched

# ========================
# 3. 晋级分析
# ========================
def to_kline_date(d): return f"{d[:4]}-{d[4:6]}-{d[6:8]}"

def is_one_word(kline, date):
    d = kline.get(date)
    if not d or d['high'] <= 0: return False
    return ((d['high']-d['open'])/d['high']*100 < 0.5 and
            abs(d['close']-d['open'])/d['close']*100 < 0.3)

def vol_ratio(kline, date, lbk=5):
    dts = sorted(kline.keys())
    if date not in dts: return None
    idx = dts.index(date)
    s = max(0, idx-lbk)
    prev = [kline[d]['vol'] for d in dts[s:idx] if d in kline]
    return kline[date]['vol']/(sum(prev)/len(prev)) if prev else None

def amt_ratio(kline, date):
    dts = sorted(kline.keys())
    if date not in dts: return None
    idx = dts.index(date)
    if idx == 0: return None
    pd = dts[idx-1]
    tc = kline[date]['close']*kline[date]['vol']
    pc = kline[pd]['close']*kline[pd]['vol']
    return tc/pc if pc > 0 else None

def auction_pct(kline, date):
    dts = sorted(kline.keys())
    if date not in dts: return None
    idx = dts.index(date)
    if idx == 0: return None
    prev_close = kline[dts[idx-1]]['close']
    return (kline[date]['open']/prev_close-1)*100

def run_analysis():
    """加载全部数据，跑晋级统计"""
    from collections import defaultdict

    files = sorted([f for f in os.listdir(DATA_DIR)
                    if f.startswith('202') and f.endswith('.json')], reverse=True)
    dates_list = [f.replace('.json','') for f in files]
    dates_set = set(dates_list)

    all_stocks_by_date = {}
    for f in files:
        date = f.replace('.json','')
        with open(f"{DATA_DIR}/{f}") as fp:
            data = json.load(fp)
        if isinstance(data, list) and len(data) > 0:
            all_stocks_by_date[date] = data

    def load_kline(code):
        path = f"{KLINE_DIR}/{code}.json"
        return json.load(open(path)) if os.path.exists(path) else {}

    def get_next_date(ref_date):
        if ref_date not in dates_set: return None
        idx = dates_list.index(ref_date)
        return dates_list[idx-1] if idx-1 >= 0 else None

    all_trans = []
    for date in dates_list:
        stocks = all_stocks_by_date.get(date, [])
        next_date = get_next_date(date)
        if not next_date: continue
        kd = to_kline_date(date)
        kn = to_kline_date(next_date)
        for s in stocks:
            code = s['code']; name = s['name']
            lb_count = s.get('lb_count', 1)
            kl = load_kline(code)
            if not kl or kd not in kl: continue
            yzb = is_one_word(kl, kd)
            if kn not in kl: continue
            nd = kl[kn]
            nd_prev_close = kl[kd]['close']
            nd_close_pct = (nd['close']/nd_prev_close-1)*100
            nd_high_pct = (nd['high']/nd_prev_close-1)*100
            nd_auction = auction_pct(kl, kn)
            nd_vr = vol_ratio(kl, kn, 5)
            nd_ar = amt_ratio(kl, kn)
            if nd_close_pct >= 9.5: outcome='连板'; lb=True
            elif nd_high_pct >= 9.5: outcome='炸板'; lb=False
            else: outcome='低走'; lb=False
            all_trans.append({
                'code':code,'name':name,'zt_date':date,'nd_date':next_date,
                'pk':f"{lb_count}进{lb_count+1}",'lb_count':lb_count,
                'auction':nd_auction,'close_pct':nd_close_pct,'high_pct':nd_high_pct,
                'nd_vr':nd_vr,'nd_ar':nd_ar,'outcome':outcome,'lb':lb,'yzb':yzb
            })

    # 统计分析
    by_pos = defaultdict(list)
    for r in all_trans: by_pos[r['pk']].append(r)
    non_yzb = [r for r in all_trans if not r['yzb']]
    total = len(all_trans)
    total_lb = len([r for r in all_trans if r['lb']])
    total_zb = len([r for r in all_trans if r['outcome']=='炸板'])
    total_dz = len([r for r in all_trans if r['outcome']=='低走'])

    return {
        'total': total, 'lb': total_lb, 'zb': total_zb, 'dz': total_dz,
        'by_pos': by_pos, 'non_yzb': non_yzb, 'all_trans': all_trans,
        'dates_range': f"{min(dates_list)}~{max(dates_list)}",
        'trading_days': len([f for f in files if json.load(open(f'{DATA_DIR}/{f}')) and len(json.load(open(f'{DATA_DIR}/{f}')))>0])
    }

# ========================
# 主流程
# ========================
if __name__ == '__main__':
    print("="*60)
    print("A股每日竞价复盘 v2")
    print("="*60)

    # 1. 今日涨停
    today_str, zt_stocks = get_today_zt()
    print(f"\n今日涨停: {len(zt_stocks)} 只")

    # 2. 保存今日数据（追加，不覆盖历史）
    fpath = f"{DATA_DIR}/{today_str}.json"
    if zt_stocks:
        with open(fpath, 'w') as f:
            json.dump(zt_stocks, f, ensure_ascii=False)
        print(f"已保存: {fpath}")

        # 3. 补全K线
        codes = [s['code'] for s in zt_stocks]
        print(f"补K线 {len(codes)} 只...")
        n = fetch_klines(codes)
        print(f"获取K线: {n} 只")

    # 4. 跑分析
    print("\n跑晋级分析...")
    stats = run_analysis()
    print(f"总晋级: {stats['total']} 条 | 连板{stats['lb']} | 炸板{stats['zb']} | 低走{stats['dz']}")
    print(f"数据范围: {stats['dates_range']} ({stats['trading_days']}个交易日)")

    # 5. 保存晋级记录
    with open(f"{DATA_DIR}/v3_trans.json", 'w') as f:
        json.dump(stats['all_trans'], f, ensure_ascii=False)

    print("\n完成！")
