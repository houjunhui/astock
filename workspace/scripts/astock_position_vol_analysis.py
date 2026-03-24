#!/usr/bin/env python3
import subprocess, json, os
from collections import defaultdict

DATA_DIR = "/home/gem/workspace/agent/workspace/data/astock"

def curl(url):
    p = subprocess.Popen(['curl', '-s', url], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    out, _ = p.communicate()
    try:
        return out.decode('gbk')
    except:
        return out.decode('utf-8', errors='ignore')

def get_kline(code):
    if code.startswith(('9','8')):
        mkt = 'bj'
    elif code.startswith(('6','5','7')):
        mkt = 'sh'
    else:
        mkt = 'sz'
    full = f"{mkt}{code}"
    text = curl(f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline_dayhfq&param={full},day,,,80,qfq")
    text = text.replace('kline_dayhfq=', '')
    try:
        j = json.loads(text)
        kdata = j.get('data', {}).get(full, {})
        klines = kdata.get('qfqday', []) or kdata.get('day', [])
        result = {}
        for row in klines:
            if len(row) >= 6:
                result[row[0]] = {
                    'open': float(row[1]), 'close': float(row[2]),
                    'high': float(row[3]), 'low': float(row[4]),
                    'vol': float(row[5])
                }
        return result
    except:
        return {}

def vol_ratio(kline, date, lbk=5):
    dates = sorted(kline.keys())
    if date not in dates:
        return None
    idx = dates.index(date)
    s = max(0, idx - lbk)
    prev = [kline[d]['vol'] for d in dates[s:idx] if d in kline]
    if not prev:
        return None
    return kline[date]['vol'] / (sum(prev) / len(prev))

def amt_ratio(kline, date):
    dates = sorted(kline.keys())
    if date not in dates:
        return None
    idx = dates.index(date)
    if idx == 0:
        return None
    pd = dates[idx - 1]
    tc = kline[date]['close'] * kline[date]['vol']
    pc = kline[pd]['close'] * kline[pd]['vol']
    return tc / pc if pc > 0 else None

stocks = []
with open(f"{DATA_DIR}/zt_20260320_full.txt") as f:
    for line in f:
        if line.startswith('#') or '|' not in line:
            continue
        parts = line.strip().split('|')
        if len(parts) >= 2:
            stocks.append({'code': parts[0], 'name': parts[1]})

print(f"获取K线({len(stocks)}只)...")
klines = {}
for i, s in enumerate(stocks):
    klines[s['code']] = get_kline(s['code'])
print("完成")

# 找所有连续晋级
all_trans = []
for s in stocks:
    code, name = s['code'], s['name']
    kl = klines.get(code, {})
    if not kl:
        continue
    dates = sorted(kl.keys())
    
    zt_idxs = []
    for j in range(1, len(dates)):
        pct = (kl[dates[j]]['close'] / kl[dates[j-1]]['close'] - 1) * 100
        if pct >= 9.9:
            zt_idxs.append(j)
    
    for pos_in_chain, idx in enumerate(zt_idxs):
        nd_idx = idx + 1
        if nd_idx >= len(dates):
            continue
        
        zt_date = dates[idx]
        nd_date = dates[nd_idx]
        chain_pos = pos_in_chain + 1
        
        zt = kl[zt_date]
        nd = kl[nd_date]
        nd_prev = zt['close']
        
        auction = (nd['open'] / nd_prev - 1) * 100
        close_pct = (nd['close'] / nd_prev - 1) * 100
        high_pct = (nd['high'] / nd_prev - 1) * 100
        nd_vr = vol_ratio(kl, nd_date, 5)
        nd_ar = amt_ratio(kl, nd_date)
        
        if chain_pos == 1:
            pk = '1进2'
        elif chain_pos == 2:
            pk = '2进3'
        elif chain_pos == 3:
            pk = '3进4'
        elif chain_pos == 4:
            pk = '4进5'
        elif chain_pos == 5:
            pk = '5进6'
        else:
            pk = '6进7+'
        
        all_trans.append({
            'code': code, 'name': name, 'pk': pk,
            'zt': zt_date, 'nd': nd_date,
            'auction': auction, 'close': close_pct, 'high': high_pct,
            'nd_vr': nd_vr, 'nd_ar': nd_ar,
            'lb': close_pct >= 9.5
        })

by_pos = defaultdict(list)
for t in all_trans:
    by_pos[t['pk']].append(t)

# 输出
print("\n" + "="*80)
print("各身位 竞价+量比+额比 综合分析")
print("="*80)

for pk in ['1进2', '2进3', '3进4', '4进5', '5进6', '6进7+']:
    recs = by_pos[pk]
    print(f"\n{'='*80}")
    print(f"【{pk}】{len(recs)}条记录")
    print(f"{'='*80}")
    
    if not recs:
        print("  无数据")
        continue
    
    lb_recs = [r for r in recs if r['lb']]
    zb_recs = [r for r in recs if r['high'] >= 9.5 and not r['lb']]
    
    total = len(recs)
    lb_cnt = len(lb_recs)
    zb_cnt = len(zb_recs)
    rate = lb_cnt * 100 // total if total > 0 else 0
    
    print(f"连板: {lb_cnt}/{total} ({rate}%) | 炸板: {zb_cnt}")
    
    bins = [(-20, 0), (0, 2), (2, 5), (5, 9), (9, 100)]
    bnames = ['低开<0%', '平开[0,2%)', '小幅[2,5%)', '中幅[5,9%)', '高开>=9%']
    
    print(f"\n竞价区间     | 样本 | 连板 | 连板率 | 均量比 | 均额比")
    print("-"*65)
    for bn, (lo, hi) in zip(bnames, bins):
        sub = [r for r in recs if lo <= r['auction'] < hi]
        if not sub:
            continue
        lb = len([r for r in sub if r['lb']])
        rate_sub = lb * 100 // len(sub) if sub else 0
        vr_list = [r['nd_vr'] for r in sub if r['nd_vr']]
        ar_list = [r['nd_ar'] for r in sub if r['nd_ar']]
        vr_s = "%.2f" % (sum(vr_list)/len(vr_list)) if vr_list else 'N/A'
        ar_s = "%.2f" % (sum(ar_list)/len(ar_list)) if ar_list else 'N/A'
        print(f"  {bn:12s} | {len(sub):4d} | {lb:4d} | {rate_sub:5d}%  | {vr_s:>7s} | {ar_s:>7s}")
    
    # 量比额比分组
    lb_vr = [r['nd_vr'] for r in lb_recs if r['nd_vr']]
    fail_vr = [r['nd_vr'] for r in recs if not r['lb'] and r['nd_vr']]
    if lb_vr:
        print(f"\n  连板组量比: 均=%.2f 范围=[%.2f, %.2f]" % (sum(lb_vr)/len(lb_vr), min(lb_vr), max(lb_vr)))
    if fail_vr:
        print(f"  失败组量比: 均=%.2f 范围=[%.2f, %.2f]" % (sum(fail_vr)/len(fail_vr), min(fail_vr), max(fail_vr)))
    
    lb_ar = [r['nd_ar'] for r in lb_recs if r['nd_ar']]
    fail_ar = [r['nd_ar'] for r in recs if not r['lb'] and r['nd_ar']]
    if lb_ar:
        print(f"  连板组额比: 均=%.2f 范围=[%.2f, %.2f]" % (sum(lb_ar)/len(lb_ar), min(lb_ar), max(lb_ar)))
    if fail_ar:
        print(f"  失败组额比: 均=%.2f 范围=[%.2f, %.2f]" % (sum(fail_ar)/len(fail_ar), min(fail_ar), max(fail_ar)))
    
    print(f"\n详细记录:")
    for r in sorted(recs, key=lambda x: (-x['lb'], -x['auction']))[:8]:
        tag = 'OK' if r['lb'] else ('ZB' if r['high'] >= 9.5 else 'XX')
        vr_s = "%.2f" % r['nd_vr'] if r['nd_vr'] else 'N/A'
        ar_s = "%.2f" % r['nd_ar'] if r['nd_ar'] else 'N/A'
        print(f"  [{tag}] {r['name']}({r['code']}) {r['zt'][-5:]}->{r['nd'][-5:]} 竞价{r['auction']:+6.1f}% 量比{vr_s} 额比{ar_s} 收{r['close']:+.1f}%")

# 全局分析
print("\n" + "="*80)
print("全局 竞价+量比+额比 分布")
print("="*80)

all_lb = [r for r in all_trans if r['lb']]
all_fail = [r for r in all_trans if not r['lb']]

print(f"\n总记录: {len(all_trans)}条  连板:{len(all_lb)} 失败:{len(all_fail)}")

# 竞价位移与连板率
print("\n竞价位移与连板率:")
auction_bins = [(-20, -5), (-5, 0), (0, 2), (2, 5), (5, 7), (7, 9), (9, 100)]
for lo, hi in auction_bins:
    sub = [r for r in all_trans if lo <= r['auction'] < hi]
    if not sub:
        continue
    lb = len([r for r in sub if r['lb']])
    zb = len([r for r in sub if r['high'] >= 9.5 and not r['lb']])
    rate = lb * 100 // len(sub)
    print("  竞价%+d~%+d%%: %2d只 连板%2d(%2d%%) 炸板%2d" % (lo, hi-1, len(sub), lb, rate, zb))

# 额比与连板率
print("\n额比与连板率:")
amt_bins = [(0, 0.5), (0.5, 0.8), (0.8, 1.0), (1.0, 1.3), (1.3, 1.6), (1.6, 2.0), (2.0, 100)]
for lo, hi in amt_bins:
    sub = [r for r in all_trans if r['nd_ar'] and lo <= r['nd_ar'] < hi]
    if not sub:
        continue
    lb = len([r for r in sub if r['lb']])
    zb = len([r for r in sub if r['high'] >= 9.5 and not r['lb']])
    rate = lb * 100 // len(sub)
    print("  额比%.1f~%.1f: %2d只 连板%2d(%2d%%) 炸板%2d" % (lo, hi, len(sub), lb, rate, zb))

# 量比与连板率
print("\n量比与连板率:")
vr_bins = [(0, 0.5), (0.5, 0.8), (0.8, 1.0), (1.0, 1.3), (1.3, 1.6), (1.6, 2.0), (2.0, 100)]
for lo, hi in vr_bins:
    sub = [r for r in all_trans if r['nd_vr'] and lo <= r['nd_vr'] < hi]
    if not sub:
        continue
    lb = len([r for r in sub if r['lb']])
    zb = len([r for r in sub if r['high'] >= 9.5 and not r['lb']])
    rate = lb * 100 // len(sub)
    print("  量比%.1f~%.1f: %2d只 连板%2d(%2d%%) 炸板%2d" % (lo, hi, len(sub), lb, rate, zb))

# 保存
with open(f"{DATA_DIR}/position_vol_amt_full.txt", 'w') as f:
    f.write("# 各身位竞价+量比+额比综合分析\n\n")
    for pk in ['1进2', '2进3', '3进4', '4进5', '5进6', '6进7+']:
        recs = by_pos[pk]
        f.write(f"## {pk} ({len(recs)}条)\n")
        for r in sorted(recs, key=lambda x: x['zt']):
            tag = 'OK' if r['lb'] else ('ZB' if r['high'] >= 9.5 else 'XX')
            vr_s = "%.2f" % r['nd_vr'] if r['nd_vr'] else 'N/A'
            ar_s = "%.2f" % r['nd_ar'] if r['nd_ar'] else 'N/A'
            f.write(f"[{tag}] {r['name']}({r['code']}) {r['zt'][-5:]}->{r['nd'][-5:]} 竞价{r['auction']:+.1f}% 量比{vr_s} 额比{ar_s} 收{r['close']:+.1f}%\n")
        f.write("\n")

print(f"\n已保存: {DATA_DIR}/position_vol_amt_full.txt")
