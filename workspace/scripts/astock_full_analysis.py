#!/usr/bin/env python3
"""
A股竞价策略 v3 - 全量分析（含失败案例）
核心改进：
1. 识别所有历史涨停日（含失败案例）
2. 判断次日真实走势：连板 / 炸板 / 低走
3. 计算真实连板率（vs 之前被高估的数据）
4. 量化失败模式
"""

import subprocess, json, os, datetime
from collections import defaultdict

DATA_DIR = "/home/gem/workspace/agent/workspace/data/astock"
os.makedirs(DATA_DIR, exist_ok=True)

def curl(url):
    p = subprocess.Popen(['curl', '-s', url], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    out, _ = p.communicate()
    try:
        return out.decode('gbk')
    except:
        return out.decode('utf-8', errors='ignore')

def get_kline(code, days=120):
    if code.startswith(('9','8')):
        mkt = 'bj'
    elif code.startswith(('6','5','7')):
        mkt = 'sh'
    else:
        mkt = 'sz'
    full = f"{mkt}{code}"
    text = curl(f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline_dayhfq&param={full},day,,,{days},qfq")
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

def get_mkt(code):
    if code.startswith(('9','8')):
        mkt = 'bj'
    elif code.startswith(('6','5','7')):
        mkt = 'sh'
    else:
        mkt = 'sz'
    text = curl(f"https://qt.gtimg.cn/q={mkt}{code}")
    try:
        parts = text.split('~')
        if len(parts) > 45 and parts[45]:
            return float(parts[45])  # 流通市值（亿元）
    except:
        pass
    return None

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

def is_one_word_board(kl, date):
    """判断当日是否为一字板（开盘即封板）"""
    if date not in kl:
        return False
    d = kl[date]
    if d['high'] <= 0:
        return False
    open_to_high = (d['high'] - d['open']) / d['high'] * 100
    open_to_close = abs(d['close'] - d['open']) / d['close'] * 100
    return (open_to_high < 0.5) and (open_to_close < 0.3)

def get_consecutive_chain_length(kline, zt_date):
    """从zt_date往前数，看这个涨停链有多长（包含zt_date）"""
    dates = sorted(kline.keys())
    if zt_date not in dates:
        return 0
    idx = dates.index(zt_date)
    chain_len = 1
    i = idx - 1
    while i >= 0:
        pct = (kline[dates[i+1]]['close'] / kline[dates[i]]['close'] - 1) * 100 if i+1 < len(dates) else 0
        if pct >= 9.9:
            chain_len += 1
            i -= 1
        else:
            break
    return chain_len

def main():
    print("="*80)
    print("A股竞价策略 v3 - 全量分析（含失败案例）")
    print("="*80)
    
    # 加载股票列表（今日涨停池，作为分析种子）
    stocks_file = f"{DATA_DIR}/zt_20260320_full.txt"
    stocks = []
    with open(stocks_file) as f:
        for line in f:
            if line.startswith('#') or '|' not in line:
                continue
            parts = line.strip().split('|')
            if len(parts) >= 2:
                stocks.append({'code': parts[0], 'name': parts[1]})
    print(f"股票池: {len(stocks)}只")
    
    # 获取K线和市值
    print("获取K线(120天)和市值...")
    klines = {}
    mkts = {}
    for i, s in enumerate(stocks):
        klines[s['code']] = get_kline(s['code'], 120)
        mkts[s['code']] = get_mkt(s['code'])
        if (i+1) % 10 == 0:
            print(f"  {i+1}/{len(stocks)}")
    print("完成\n")
    
    # ========== 核心分析：找所有历史涨停日 + 次日表现 ==========
    # 每个涨停日 → 分析次日（下一交易日）表现
    # 次日结局：连板 / 炸板 / 低走
    
    all_records = []  # (stock, zt_date, next_date, outcome, ...)
    
    for s in stocks:
        code, name = s['code'], s['name']
        kl = klines.get(code, {})
        if not kl:
            continue
        dates = sorted(kl.keys())
        
        # 找所有涨停日（120天窗口内）
        zt_dates = []
        for j in range(1, len(dates)):
            pct = (kl[dates[j]]['close'] / kl[dates[j-1]]['close'] - 1) * 100
            if pct >= 9.9:
                zt_dates.append(j)  # dates索引
        
        # 对每个涨停日，找下一交易日
        for zt_idx in zt_dates:
            zt_date = dates[zt_idx]
            
            # 找下一交易日（跳过非交易日）
            nd_idx = zt_idx + 1
            if nd_idx >= len(dates):
                continue
            nd_date = dates[nd_idx]
            
            # 判断连续涨停链的身位
            # 往前数，看从第几个连续涨停开始
            chain_len = get_consecutive_chain_length(kl, zt_date)
            # 链长度=1: 首板；=2: 二板...
            
            # 判断zt日是否一字板
            yzb = is_one_word_board(kl, zt_date)
            
            zt = kl[zt_date]
            nd = kl[nd_date]
            nd_prev_close = zt['close']  # 昨日收盘=今日竞价基准
            
            auction = (nd['open'] / nd_prev_close - 1) * 100
            close_pct = (nd['close'] / nd_prev_close - 1) * 100
            high_pct = (nd['high'] / nd_prev_close - 1) * 100
            
            nd_vr = vol_ratio(kl, nd_date, 5)
            nd_ar = amt_ratio(kl, nd_date)
            float_mkt = mkts.get(code)
            
            # 判断结局
            if close_pct >= 9.5:
                outcome = '连板'
                lb = True
            elif high_pct >= 9.5 and close_pct < 9.5:
                outcome = '炸板'
                lb = False
            else:
                outcome = '低走'
                lb = False
            
            # 判断次日是否也是涨停（一字板逻辑）
            nd_yzb = is_one_word_board(kl, nd_date)
            
            # 身位（基于历史往前数）
            if chain_len == 1:
                pk = '1进2'
            elif chain_len == 2:
                pk = '2进3'
            elif chain_len == 3:
                pk = '3进4'
            elif chain_len == 4:
                pk = '4进5'
            elif chain_len == 5:
                pk = '5进6'
            else:
                pk = '6进7+'
            
            all_records.append({
                'code': code, 'name': name,
                'zt_date': zt_date, 'nd_date': nd_date,
                'pk': pk, 'chain_len': chain_len,
                'auction': auction, 'close_pct': close_pct, 'high_pct': high_pct,
                'nd_vr': nd_vr, 'nd_ar': nd_ar,
                'float_mkt': float_mkt,
                'yzb': yzb, 'nd_yzb': nd_yzb,
                'outcome': outcome, 'lb': lb
            })
    
    # 按身位分组
    by_pos = defaultdict(list)
    for r in all_records:
        by_pos[r['pk']].append(r)
    
    # ========== 输出结果 ==========
    print("="*80)
    print("v3 全量分析：含失败案例，真实连板率")
    print("="*80)
    
    # 全局统计
    total = len(all_records)
    total_lb = len([r for r in all_records if r['lb']])
    total_zb = len([r for r in all_records if r['outcome'] == '炸板'])
    total_dz = len([r for r in all_records if r['outcome'] == '低走'])
    total_yzb = len([r for r in all_records if r['yzb']])
    
    print(f"\n全局：总涨停日={total} | 连板={total_lb}({total_lb*100//max(total,1)}%) "
          f"| 炸板={total_zb} | 低走={total_dz} | 一字板={total_yzb}")
    
    # 非一字板统计
    non_yzb = [r for r in all_records if not r['yzb']]
    n_lb = len([r for r in non_yzb if r['lb']])
    n_zb = len([r for r in non_yzb if r['outcome'] == '炸板'])
    n_dz = len([r for r in non_yzb if r['outcome'] == '低走'])
    print(f"全局（排除一字板）：{len(non_yzb)}日 | 连板={n_lb}({n_lb*100//max(len(non_yzb),1)}%) "
          f"| 炸板={n_zb} | 低走={n_dz}")
    
    # 各身位详细分析
    for pk in ['1进2', '2进3', '3进4', '4进5', '5进6', '6进7+']:
        recs = by_pos[pk]
        if not recs:
            continue
        
        # 排除一字板
        ny_recs = [r for r in recs if not r['yzb']]
        
        lb = len([r for r in ny_recs if r['lb']])
        zb = len([r for r in ny_recs if r['outcome'] == '炸板'])
        dz = len([r for r in ny_recs if r['outcome'] == '低走'])
        
        print(f"\n{'='*80}")
        print(f"【{pk}】{len(ny_recs)}条(排除一字板{len(recs)-len(ny_recs)}只)")
        print(f"  连板={lb}({lb*100//max(len(ny_recs),1)}%) | 炸板={zb}({zb*100//max(len(ny_recs),1)}%) | 低走={dz}({dz*100//max(len(ny_recs),1)}%)")
        print(f"{'='*80}")
        
        if not ny_recs:
            continue
        
        # 竞价区间分析
        bins = [(-20, 0), (0, 2), (2, 5), (5, 9), (9, 100)]
        bnames = ['低开<0%', '平开[0,2%)', '小幅[2,5%)', '中幅[5,9%)', '高开>=9%']
        
        print(f"\n竞价区间 | 样本 | 连板 | 炸板 | 低走 | 率 | 均量比 | 均额比 | 均市值")
        print("-"*80)
        for bn, (lo, hi) in zip(bnames, bins):
            sub = [r for r in ny_recs if lo <= r['auction'] < hi]
            if not sub:
                continue
            n_lb = len([r for r in sub if r['lb']])
            n_zb = len([r for r in sub if r['outcome'] == '炸板'])
            n_dz = len([r for r in sub if r['outcome'] == '低走'])
            rate = n_lb * 100 // len(sub)
            vrs = [r['nd_vr'] for r in sub if r['nd_vr']]
            ars = [r['nd_ar'] for r in sub if r['nd_ar']]
            mkts_v = [r['float_mkt'] for r in sub if r['float_mkt']]
            vr_s = "%.2f" % (sum(vrs)/len(vrs)) if vrs else 'N/A'
            ar_s = "%.2f" % (sum(ars)/len(ars)) if ars else 'N/A'
            mkt_s = "%.0f" % (sum(mkts_v)/len(mkts_v)) if mkts_v else 'N/A'
            print(f"  {bn:12s} | {len(sub):4d} | {n_lb:4d} | {n_zb:4d} | {n_dz:4d} | {rate:3d}% | {vr_s:>7s} | {ar_s:>7s} | {mkt_s:>7s}亿")
        
        # 量比分析（总体，非按竞价分）
        print(f"\n量比区间 | 样本 | 连板 | 率 | 额比均 | 竞价均")
        for lo, hi in [(0, 0.5), (0.5, 0.8), (0.8, 1.2), (1.2, 1.6), (1.6, 2.0), (2.0, 100)]:
            sub = [r for r in ny_recs if r['nd_vr'] and lo <= r['nd_vr'] < hi]
            if not sub:
                continue
            n_lb = len([r for r in sub if r['lb']])
            rate = n_lb * 100 // len(sub)
            ars = ["%.2f" % r['nd_ar'] for r in sub if r['nd_ar']]
            aucs = ["%+.1f" % r['auction'] for r in sub]
            print(f"  量比{lo:.1f}~{hi:.1f}: {len(sub):3d}只 连板{n_lb:2d}({rate:2d}%) 额比均={sum(float(x) for x in ars)/len(ars):.2f} 竞价均={sum(float(x) for x in aucs)/len( aucs):+.1f}%")
        
        # 额比分析
        print(f"\n额比区间 | 样本 | 连板 | 率 | 量比均 | 竞价均")
        for lo, hi in [(0, 0.5), (0.5, 0.8), (0.8, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 100)]:
            sub = [r for r in ny_recs if r['nd_ar'] and lo <= r['nd_ar'] < hi]
            if not sub:
                continue
            n_lb = len([r for r in sub if r['lb']])
            rate = n_lb * 100 // len(sub)
            vrs = ["%.2f" % r['nd_vr'] for r in sub if r['nd_vr']]
            aucs = ["%+.1f" % r['auction'] for r in sub]
            print(f"  额比{lo:.1f}~{hi:.1f}: {len(sub):3d}只 连板{n_lb:2d}({rate:2d}%) 量比均={sum(float(x) for x in vrs)/len(vrs):.2f} 竞价均={sum(float(x) for x in aucs)/len( aucs):+.1f}%")
        
        # 失败案例详细（按结局分类）
        print(f"\n失败案例（低走）:")
        fail_dz = sorted([r for r in ny_recs if r['outcome'] == '低走'], 
                         key=lambda x: x['auction'])
        for r in fail_dz[:5]:
            vr_s = "%.2f" % r['nd_vr'] if r['nd_vr'] else 'N/A'
            ar_s = "%.2f" % r['nd_ar'] if r['nd_ar'] else 'N/A'
            mkt_s = "%.0f亿" % r['float_mkt'] if r['float_mkt'] else 'N/A'
            print(f"  [低走] {r['name']}({r['code']}) {r['zt_date'][-5:]}->{r['nd_date'][-5:]} "
                  f"竞价{r['auction']:+6.1f}% 量比{vr_s} 额比{ar_s} 流通{mkt_s} 收{r['close_pct']:+.1f}%")
        
        print(f"\n失败案例（炸板）:")
        fail_zb = sorted([r for r in ny_recs if r['outcome'] == '炸板'],
                        key=lambda x: x['auction'])
        for r in fail_zb[:5]:
            vr_s = "%.2f" % r['nd_vr'] if r['nd_vr'] else 'N/A'
            ar_s = "%.2f" % r['nd_ar'] if r['nd_ar'] else 'N/A'
            mkt_s = "%.0f亿" % r['float_mkt'] if r['float_mkt'] else 'N/A'
            print(f"  [炸板] {r['name']}({r['code']}) {r['zt_date'][-5:]}->{r['nd_date'][-5:]} "
                  f"竞价{r['auction']:+6.1f}% 量比{vr_s} 额比{ar_s} 流通{mkt_s} 收{r['close_pct']:+.1f}%")
        
        # 连板成功案例TOP
        print(f"\n连板成功案例TOP5:")
        ok_recs = sorted([r for r in ny_recs if r['lb']], 
                         key=lambda x: (-x['chain_len'], -x['nd_ar']))
        for r in ok_recs[:5]:
            vr_s = "%.2f" % r['nd_vr'] if r['nd_vr'] else 'N/A'
            ar_s = "%.2f" % r['nd_ar'] if r['nd_ar'] else 'N/A'
            mkt_s = "%.0f亿" % r['float_mkt'] if r['float_mkt'] else 'N/A'
            print(f"  [OK] {r['name']}({r['code']}) {r['zt_date'][-5:]}->{r['nd_date'][-5:]} "
                  f"竞价{r['auction']:+6.1f}% 量比{vr_s} 额比{ar_s} 流通{mkt_s} 收{r['close_pct']:+.1f}%")
    
    # 全局量比/额比分层
    print("\n" + "="*80)
    print("全局量比 vs 结局（非一字板）")
    print("="*80)
    for lo, hi in [(0, 0.5), (0.5, 0.8), (0.8, 1.2), (1.2, 1.6), (1.6, 2.0), (2.0, 3.0), (3.0, 100)]:
        sub = [r for r in non_yzb if r['nd_vr'] and lo <= r['nd_vr'] < hi]
        if not sub:
            continue
        n_lb = len([r for r in sub if r['lb']])
        n_zb = len([r for r in sub if r['outcome'] == '炸板'])
        n_dz = len([r for r in sub if r['outcome'] == '低走'])
        rate = n_lb * 100 // len(sub)
        print(f"  量比{lo:.1f}~{hi:.1f}: {len(sub):3d}只 连板{n_lb:2d}({rate:2d}%) 炸板{n_zb:2d} 低走{n_dz:2d}")
    
    print("\n" + "="*80)
    print("全局额比 vs 结局（非一字板）")
    print("="*80)
    for lo, hi in [(0, 0.5), (0.5, 0.8), (0.8, 1.0), (1.0, 1.3), (1.3, 1.6), (1.6, 2.0), (2.0, 100)]:
        sub = [r for r in non_yzb if r['nd_ar'] and lo <= r['nd_ar'] < hi]
        if not sub:
            continue
        n_lb = len([r for r in sub if r['lb']])
        n_zb = len([r for r in sub if r['outcome'] == '炸板'])
        n_dz = len([r for r in sub if r['outcome'] == '低走'])
        rate = n_lb * 100 // len(sub)
        print(f"  额比{lo:.1f}~{hi:.1f}: {len(sub):3d}只 连板{n_lb:2d}({rate:2d}%) 炸板{n_zb:2d} 低走{n_dz:2d}")
    
    # 保存
    out_file = f"{DATA_DIR}/v3_full_analysis.txt"
    with open(out_file, 'w') as f:
        f.write(f"# A股竞价策略 v3 全量分析\n")
        f.write(f"# 时间: {datetime.datetime.now()}\n")
        f.write(f"# 总记录: {total}条(排除一字板{len(non_yzb)}条)\n\n")
        for r in sorted(all_records, key=lambda x: x['zt_date']):
            tag = 'OK' if r['lb'] else ('ZB' if r['outcome']=='炸板' else 'XX')
            f.write(f"[{tag}] {r['name']}({r['code']}) {r['zt_date'][-5:]}->{r['nd_date'][-5:]} "
                    f"{r['pk']} 竞价{r['auction']:+.1f}% 收{r['close_pct']:+.1f}% "
                    f"量比{r['nd_vr']:.2f if r['nd_vr'] else 'N/A'} 额比{r['nd_ar']:.2f if r['nd_ar'] else 'N/A'} "
                    f"市值{r['float_mkt']:.0f if r['float_mkt'] else 'N/A'}亿 "
                    f"{'一字' if r['yzb'] else ''}\n")
    
    print(f"\n已保存: {out_file}")
    print(f"总记录: {total}条 | 排除一字板: {len(non_yzb)}条")

if __name__ == '__main__':
    main()
