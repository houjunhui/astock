#!/usr/bin/env python3
"""
A股竞价选股策略 v2 - 增强版
- 历史数据扩大至120天
- 修复身位计算bug
- 补充板块联动分析
- 识别一字板
- 计算相对量比/额比
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
    """获取K线数据，days=历史天数"""
    if code.startswith(('9','8')):
        mkt = 'bj'
    elif code.startswith(('6','5','7')):
        mkt = 'sh'
    else:
        mkt = 'sz'
    full = f"{mkt}{code}"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline_dayhfq&param={full},day,,,{days},qfq"
    text = curl(url)
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

def get_stock_info(codes):
    """批量获取股票详细信息（板块、市值、流通股本等）"""
    if not codes:
        return {}
    code_str = ','.join([f"{'sh' if c.startswith(('6','5','7')) else ('bj' if c.startswith(('9','8')) else 'sz')}{c}" for c in codes])
    text = curl(f"https://qt.gtimg.cn/q={code_str}")
    result = {}
    for line in text.split('\n'):
        if '="' not in line:
            continue
        parts = line.split('~')
        if len(parts) < 90:
            continue
        raw = parts[2]
        code = raw.replace('sz','').replace('sh','').replace('bj','')
        try:
            # 流通市值(万) ≈ float(parts[44]) 字段44/45可能有
            # 字段38=总市值(万), 字段45=流通市值(万)
            float_mkt = float(parts[38]) if parts[38] else 0  # 总市值（万）
            float_float = float(parts[45]) if parts[45] else 0  # 流通市值（万）
            # 板块信息可能在字段90+
            plate = parts[90] if len(parts) > 90 and parts[90] else ''
            result[code] = {
                'name': parts[1],
                'float_mkt': float_float,  # 流通市值（万）
                'total_mkt': float_mkt,   # 总市值（万）
                'plate': plate
            }
        except:
            pass
    return result

def get_zt_stocks_today():
    """获取今日涨停股"""
    text = curl('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page=1&num=200&sort=changepercent&asc=0&node=hs_a&symbol=&_s_r_a=page')
    try:
        rows = json.loads(text)
        return [r for r in rows if float(r.get('changepercent', 0)) >= 9.9]
    except:
        return []

def calc_vol_ratio(kline, date, lbk=5):
    """量比 = 今日量 / 近N日均量"""
    dates = sorted(kline.keys())
    if date not in dates:
        return None
    idx = dates.index(date)
    s = max(0, idx - lbk)
    prev = [kline[d]['vol'] for d in dates[s:idx] if d in kline]
    if not prev:
        return None
    return kline[date]['vol'] / (sum(prev) / len(prev))

def calc_amt_ratio(kline, date):
    """成交额比 = 今日成交额 / 昨日成交额"""
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

def calc_rel_vol_ratio(kline, date, float_mkt_wan, lbk=5):
    """相对量比 = 绝对量比 / (流通市值/100亿)
    流通市值越大，正常量比应该越小
    公式：相对量比 = 今日量/均量 / (流通市值/100亿)
    """
    vr = calc_vol_ratio(kline, date, lbk)
    if vr is None or float_mkt_wan <= 0:
        return None
    # 归一化：200亿流通市值为基准1.0
    scale = float_mkt_wan / 2000000  # 万/200亿=2000000万
    return vr / max(scale, 0.1)

def is_one_word_board(kline, date):
    """判断昨日涨停是否为一字板（开盘即涨停，14:30前封板）"""
    # 一字板特征：涨停时间在14:00前，且当日K线为光头阳线（开盘=收盘≈最高）
    # 但K线数据无法直接判断封板时间
    # 替代判断：涨停日的open和close非常接近（误差<0.5%），且open接近high
    dates = sorted(kline.keys())
    if date not in dates:
        return None
    idx = dates.index(date)
    if idx == 0:
        return None
    
    d = kline[date]
    # 判断昨日是否涨停
    prev_close = kline[dates[idx-1]]['close']
    if (d['close'] / prev_close - 1) < 0.099:
        return False  # 昨日未涨停
    
    # 涨停日特征：open接近high（没有下影线），且open接近收盘
    if d['high'] <= 0:
        return None
    open_to_high = (d['high'] - d['open']) / d['high'] * 100
    open_to_close = abs(d['close'] - d['open']) / d['close'] * 100
    
    # 一字板：开盘价=最高价，且开收价差极小
    is_一字 = (open_to_high < 0.5) and (open_to_close < 0.3)
    return is_一字

def get_consecutive_chain(kline, target_date):
    """获取某日属于哪个连续涨停链，返回(链起始索引, 当前位置, 链长度)"""
    dates = sorted(kline.keys())
    
    # 找所有涨停日
    zt_dates = []
    for j in range(1, len(dates)):
        pct = (kline[dates[j]]['close'] / kline[dates[j-1]]['close'] - 1) * 100
        if pct >= 9.9:
            zt_dates.append(j)  # 在dates中的索引
    
    if not zt_dates or target_date not in dates:
        return None
    
    target_idx = dates.index(target_date)
    
    # 找该日在哪个链中
    # 从target_idx往前找连续的涨停日
    chain_start = None
    i = target_idx
    while i in zt_dates:
        chain_start = i
        i -= 1
        if i < 0:
            break
        if i not in zt_dates:
            break
    
    if chain_start is None:
        return None
    
    # 从链起始往后数
    chain_len = 1
    i = chain_start + 1
    while i in zt_dates and i < len(dates):
        chain_len += 1
        i += 1
    
    pos_in_chain = 0
    i = chain_start
    while i <= target_idx:
        if i in zt_dates:
            pos_in_chain += 1
        i += 1
    
    # target_idx是dates中的索引，pos_in_chain是身位（第N个涨停）
    return {
        'chain_start_idx': chain_start,
        'position': pos_in_chain,  # 1=首板, 2=二板...
        'chain_length': chain_len,
        'dates_in_chain': [dates[j] for j in range(chain_start, chain_start + chain_len) if j in zt_dates]
    }

def main():
    print("="*80)
    print("A股竞价策略 v2 增强分析")
    print("="*80)
    
    # Step 1: 获取今日涨停股（作为种子池）
    print("\n[1] 获取涨停股...")
    today_stocks = get_zt_stocks_today()
    print(f"  今日涨停: {len(today_stocks)}只")
    
    codes = [s['code'] for s in today_stocks]
    
    # Step 2: 获取这批股票的基本信息（板块、市值）
    print("[2] 获取股票基本信息和板块...")
    stock_info = get_stock_info(codes)
    print(f"  获取到: {len(stock_info)}只")
    
    # Step 3: 获取K线（扩大至120天）
    print("[3] 获取K线(120天)...")
    klines = {}
    for i, code in enumerate(codes):
        klines[code] = get_kline(code, 120)
        if (i+1) % 10 == 0:
            print(f"  {i+1}/{len(codes)}")
    print("  完成")
    
    # Step 4: 分析所有晋级记录
    print("[4] 分析晋级记录...")
    all_trans = []
    
    for s in today_stocks:
        code, name = s['code'], s['name']
        kl = klines.get(code, {})
        if not kl:
            continue
        dates = sorted(kl.keys())
        
        info = stock_info.get(code, {})
        float_mkt = info.get('float_mkt', 0) or 0
        plate = info.get('plate', '')
        
        # 找所有涨停日（跨整个120天）
        zt_idxs = []
        for j in range(1, len(dates)):
            pct = (kl[dates[j]]['close'] / kl[dates[j-1]]['close'] - 1) * 100
            if pct >= 9.9:
                zt_idxs.append(j)
        
        # 分析每对连续晋级（只看链内的连续晋级）
        for pos_in_chain, idx in enumerate(zt_idxs):
            nd_idx = idx + 1
            if nd_idx >= len(dates):
                continue
            
            zt_date = dates[idx]
            nd_date = dates[nd_idx]
            
            # 检查是否连续（中间不能有非涨停日）
            # idx和nd_idx之间如果还有其他日期但不是连续涨停，则不算晋级
            zt_idx_set = set(zt_idxs)
            is_consecutive = True
            for k in range(idx + 1, nd_idx):
                if k not in zt_idx_set:
                    is_consecutive = False
                    break
            
            if not is_consecutive:
                continue
            
            chain = get_consecutive_chain(kl, zt_date)
            if chain is None:
                continue
            
            position = chain['position']
            
            # 昨日是否一字板
            is_yzb = is_one_word_board(kl, zt_date)
            
            zt = kl[zt_date]
            nd = kl[nd_date]
            nd_prev_close = zt['close']
            
            auction = (nd['open'] / nd_prev_close - 1) * 100
            close_pct = (nd['close'] / nd_prev_close - 1) * 100
            high_pct = (nd['high'] / nd_prev_close - 1) * 100
            
            nd_vr = calc_vol_ratio(kl, nd_date, 5)
            nd_ar = calc_amt_ratio(kl, nd_date)
            nd_rel_vr = calc_rel_vol_ratio(kl, nd_date, float_mkt, 5)
            
            if position == 1:
                pk = '1进2'
            elif position == 2:
                pk = '2进3'
            elif position == 3:
                pk = '3进4'
            elif position == 4:
                pk = '4进5'
            elif position == 5:
                pk = '5进6'
            else:
                pk = '6进7+'
            
            all_trans.append({
                'code': code, 'name': name,
                'pk': pk, 'position': position,
                'zt': zt_date, 'nd': nd_date,
                'auction': auction, 'close': close_pct, 'high': high_pct,
                'nd_vr': nd_vr, 'nd_ar': nd_ar, 'nd_rel_vr': nd_rel_vr,
                'is_yzb': is_yzb, 'float_mkt': float_mkt,
                'plate': plate,
                'lb': close_pct >= 9.5
            })
    
    by_pos = defaultdict(list)
    for t in all_trans:
        by_pos[t['pk']].append(t)
    
    # 输出各身位分析（增强版）
    print("\n" + "="*80)
    print("各身位晋级分析（v2增强版）")
    print("="*80)
    
    for pk in ['1进2', '2进3', '3进4', '4进5', '5进6', '6进7+']:
        recs = by_pos[pk]
        print(f"\n{'='*80}")
        print(f"【{pk}】{len(recs)}条记录")
        print(f"{'='*80}")
        
        if not recs:
            continue
        
        lb_recs = [r for r in recs if r['lb']]
        zb_recs = [r for r in recs if r['high'] >= 9.5 and not r['lb']]
        
        print(f"连板: {len(lb_recs)}/{len(recs)} ({len(lb_recs)*100//len(recs)}%) | 炸板: {len(zb_recs)}")
        
        # 排除一字板后分析
        non_yzb = [r for r in recs if r['is_yzb'] != True]
        yzb = [r for r in recs if r['is_yzb'] == True]
        print(f"一字板: {len(yzb)}只（已排除） | 非一字板: {len(non_yzb)}只")
        
        # 竞价区间分析
        bins = [(-20, 0), (0, 2), (2, 5), (5, 9), (9, 100)]
        bnames = ['低开<0%', '平开[0,2%)', '小幅[2,5%)', '中幅[5,9%)', '高开>=9%']
        
        print(f"\n竞价区间（非一字板） | 样本 | 连板 | 率 | 均量比 | 均额比 | 均相对量比")
        print("-"*80)
        for bn, (lo, hi) in zip(bnames, bins):
            sub = [r for r in non_yzb if lo <= r['auction'] < hi]
            if not sub:
                continue
            lb = len([r for r in sub if r['lb']])
            vr = [r['nd_vr'] for r in sub if r['nd_vr']]
            ar = [r['nd_ar'] for r in sub if r['nd_ar']]
            rel_vr = [r['nd_rel_vr'] for r in sub if r['nd_rel_vr']]
            rate = lb * 100 // len(sub)
            vr_s = "%.2f" % (sum(vr)/len(vr)) if vr else 'N/A'
            ar_s = "%.2f" % (sum(ar)/len(ar)) if ar else 'N/A'
            rel_s = "%.2f" % (sum(rel_vr)/len(rel_vr)) if rel_vr else 'N/A'
            print(f"  {bn:12s} | {len(sub):4d} | {lb:4d} | {rate:3d}% | {vr_s:>7s} | {ar_s:>7s} | {rel_s:>10s}")
        
        # 详细记录
        print(f"\n详细记录(非一字板):")
        for r in sorted(non_yzb, key=lambda x: (-x['lb'], -x['auction']))[:10]:
            tag = 'OK' if r['lb'] else ('ZB' if r['high'] >= 9.5 else 'XX')
            vr_s = "%.2f" % r['nd_vr'] if r['nd_vr'] else 'N/A'
            ar_s = "%.2f" % r['nd_ar'] if r['nd_ar'] else 'N/A'
            rel_s = "%.2f" % r['nd_rel_vr'] if r['nd_rel_vr'] else 'N/A'
            yzb_tag = '一字' if r['is_yzb'] else ''
            print(f"  [{tag}] {r['name']}({r['code']}) {r['zt'][-5:]}->{r['nd'][-5:]} 竞价{r['auction']:+6.1f}% 量比{vr_s} 额比{ar_s} 相对量比{rel_s} 流通市值{r['float_mkt']/10000:.0f}亿 收{r['close']:+.1f}% {yzb_tag}")
    
    # 全局分析
    print("\n" + "="*80)
    print("全局分析（排除一字板）")
    print("="*80)
    
    all_non_yzb = [r for r in all_trans if r['is_yzb'] != True]
    all_lb = [r for r in all_non_yzb if r['lb']]
    
    print(f"\n总晋级（排除一字板）: {len(all_non_yzb)}条  连板:{len(all_lb)}({len(all_lb)*100//max(len(all_non_yzb),1)}%)")
    
    # 竞价位移
    print("\n竞价位移 vs 连板率:")
    auction_bins = [(-20, 0), (0, 2), (2, 5), (5, 7), (7, 9), (9, 100)]
    for lo, hi in auction_bins:
        sub = [r for r in all_non_yzb if lo <= r['auction'] < hi]
        if not sub:
            continue
        lb = len([r for r in sub if r['lb']])
        zb = len([r for r in sub if r['high'] >= 9.5 and not r['lb']])
        rate = lb * 100 // len(sub)
        print(f"  竞价%+d~%+d%%: %2d只 连板%2d(%2d%%) 炸板%2d" % (lo, hi-1, len(sub), lb, rate, zb))
    
    # 额比分层
    print("\n额比分层 vs 连板率:")
    amt_bins = [(0, 0.5), (0.5, 0.8), (0.8, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 100)]
    for lo, hi in amt_bins:
        sub = [r for r in all_non_yzb if r['nd_ar'] and lo <= r['nd_ar'] < hi]
        if not sub:
            continue
        lb = len([r for r in sub if r['lb']])
        zb = len([r for r in sub if r['high'] >= 9.5 and not r['lb']])
        rate = lb * 100 // len(sub)
        print("  额比%.1f~%.1f: %2d只 连板%2d(%2d%%) 炸板%2d" % (lo, hi, len(sub), lb, rate, zb))
    
    # 量比分层
    print("\n量比分层 vs 连板率:")
    vol_bins = [(0, 0.5), (0.5, 0.8), (0.8, 1.2), (1.2, 1.6), (1.6, 2.0), (2.0, 100)]
    for lo, hi in vol_bins:
        sub = [r for r in all_non_yzb if r['nd_vr'] and lo <= r['nd_vr'] < hi]
        if not sub:
            continue
        lb = len([r for r in sub if r['lb']])
        zb = len([r for r in sub if r['high'] >= 9.5 and not r['lb']])
        rate = lb * 100 // len(sub)
        print("  量比%.1f~%.1f: %2d只 连板%2d(%2d%%) 炸板%2d" % (lo, hi, len(sub), lb, rate, zb))
    
    # 流通市值分层
    print("\n流通市值分层 vs 连板率:")
    mkt_bins = [(0, 30), (30, 50), (50, 100), (100, 200), (200, 500), (500, 1000), (1000, 10000)]
    for lo, hi in mkt_bins:
        sub = [r for r in all_non_yzb if r['float_mkt'] and lo <= r['float_mkt']/10000 < hi]
        if not sub:
            continue
        lb = len([r for r in sub if r['lb']])
        zb = len([r for r in sub if r['high'] >= 9.5 and not r['lb']])
        rate = lb * 100 // len(sub)
        print("  %d~%d亿: %2d只 连板%2d(%2d%%) 炸板%2d" % (lo, hi, len(sub), lb, rate, zb))
    
    # 保存
    with open(f"{DATA_DIR}/v2_analysis.txt", 'w') as f:
        f.write(f"# A股竞价策略 v2 增强分析\n")
        f.write(f"# 时间: {datetime.datetime.now()}\n")
        f.write(f"# 总晋级记录(排除一字板): {len(all_non_yzb)}条\n\n")
        for pk in ['1进2', '2进3', '3进4', '4进5', '5进6', '6进7+']:
            recs = by_pos[pk]
            non_yzb = [r for r in recs if r['is_yzb'] != True]
            f.write(f"## {pk} ({len(non_yzb)}条非一字板)\n")
            for r in sorted(non_yzb, key=lambda x: x['zt']):
                tag = 'OK' if r['lb'] else ('ZB' if r['high'] >= 9.5 else 'XX')
                f.write(f"[{tag}] {r['name']}({r['code']}) {r['zt'][-5:]}->{r['nd'][-5:]} 竞价{r['auction']:+.1f}% 量比{r['nd_vr']:.2f if r['nd_vr'] else 'N/A'} 额比{r['nd_ar']:.2f if r['nd_ar'] else 'N/A'} 相对量比{r['nd_rel_vr']:.2f if r['nd_rel_vr'] else 'N/A'} 市值{r['float_mkt']/10000:.0f}亿 收{r['close']:+.1f}%\n")
            f.write("\n")
    
    print(f"\n已保存: {DATA_DIR}/v2_analysis.txt")
    print(f"总记录: {len(all_trans)}条 | 排除一字板: {len(all_non_yzb)}条")

if __name__ == '__main__':
    main()
