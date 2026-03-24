#!/usr/bin/env python3
"""
A股每日竞价复盘 - 主分析脚本
每天22:00运行:
1. 收集今日涨停股 + 竞价数据，保存
2. 对比昨日涨停股今日表现，分析连板情况
3. 输出报告
"""

import subprocess, json, datetime, os, sys, time

DATA_DIR = "/home/gem/workspace/agent/workspace/data/astock"
OUT_DIR = "/home/gem/workspace/agent/workspace/astock_strategy"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

def curl(url, headers=None, encoding='utf-8'):
    args = ['curl', '-s', url]
    if headers:
        for k, v in headers.items():
            args += ['-H', f'{k}: {v}']
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    out, _ = p.communicate()
    try:
        return out.decode(encoding)
    except:
        return out.decode('utf-8', errors='ignore')

def get_zt_stocks():
    """获取今日涨停股列表"""
    text = curl(
        'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page=1&num=200&sort=changepercent&asc=0&node=hs_a&symbol=&_s_r_a=page',
        {'Referer': 'https://finance.sina.com.cn'}
    )
    try:
        rows = json.loads(text)
        return [r for r in rows if float(r.get('changepercent', 0)) >= 9.9]
    except:
        return []

def get_realtime_quotes(codes):
    """批量获取实时行情"""
    if not codes:
        return {}
    code_str = ','.join(codes)
    text = curl(f'https://qt.gtimg.cn/q={code_str}', {'Referer': 'https://finance.qq.com'}, 'gbk')
    results = {}
    for line in text.split('\n'):
        if '="' not in line:
            continue
        parts = line.split('~')
        if len(parts) < 40:
            continue
        code = parts[2].replace('sz','').replace('sh','').replace('bj','')
        try:
            yc = float(parts[4]) if parts[4] else 0
            op = float(parts[5]) if parts[5] else 0
            hp = float(parts[33]) if parts[33] else 0
            cp = float(parts[3]) if parts[3] else 0
            if yc <= 0:
                continue
            results[code] = {
                'name': parts[1], 'yc': yc, 'op': op, 'hp': hp, 'cp': cp,
                'ap': (op/yc-1)*100, 'hp_pct': (hp/yc-1)*100, 'cp_pct': (cp/yc-1)*100
            }
        except:
            continue
    return results

def get_index():
    """获取指数"""
    text = curl('https://qt.gtimg.cn/q=s_sh000001,s_sz399001,s_sz399006,s_sh000016,s_sh000300',
                {'Referer': 'https://finance.qq.com'}, 'gbk')
    indices = {}
    for line in text.split('\n'):
        if '="' not in line:
            continue
        parts = line.split('~')
        if len(parts) < 5:
            continue
        name = parts[1]
        close = parts[3]
        change = parts[4]
        try:
            indices[name] = {'close': float(close), 'change': float(change)}
        except:
            pass
    return indices

def save_today_data(date_str, stocks):
    """保存当日涨停股基础数据（来自新浪）"""
    fpath = f"{DATA_DIR}/zt_{date_str}.txt"
    with open(fpath, 'w') as f:
        f.write(f"# Date: {date_str}\n")
        f.write(f"# Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# Count: {len(stocks)}\n")
        for s in stocks:
            f.write(f"{s['code']}|{s['name']}|{s['changepercent']}\n")
    return fpath

def analyze(yesterday_stocks, today_realtime, date_label):
    """生成分析报告"""
    if not yesterday_stocks:
        return "⚠️ 无昨日数据\n"

    lianban, zhaban, gaokai, pingkai, other = [], [], [], [], []
    
    for s in yesterday_stocks:
        code = s['code']
        rt = today_realtime.get(code, {})
        if not rt:
            other.append((code, s['name'], s.get('changepercent', 0), None))
            continue
        
        ap = rt['ap']
        cp = rt['cp_pct']
        
        if cp >= 9.5:
            lianban.append((code, s['name'], s.get('changepercent', 0), rt))
        elif rt['hp_pct'] >= 9.5 and cp < 9.5:
            zhaban.append((code, s['name'], s.get('changepercent', 0), rt))
        elif ap > 5:
            gaokai.append((code, s['name'], s.get('changepercent', 0), rt))
        elif ap >= 0:
            pingkai.append((code, s['name'], s.get('changepercent', 0), rt))
        else:
            other.append((code, s['name'], s.get('changepercent', 0), rt))
    
    lines = []
    lines.append(f"## 📊 {date_label} 涨停股 → 今日表现\n")
    
    # 指数
    idx = get_index()
    if idx:
        lines.append("### 大盘\n")
        for name, d in idx.items():
            c = d['change']
            emoji = '🔴' if c < 0 else '🟢'
            lines.append(f"- {name}: **{d['close']:.2f}** {emoji} {c:+.2f}%\n")
        lines.append("\n")
    
    # 连板
    total = len(yesterday_stocks)
    lines.append(f"### ✅ 继续涨停（{len(lianban)}/{total}只，连板率{len(lianban)*100//total}%)\n")
    if lianban:
        lines.append("| 代码 | 名称 | 昨日% | 竞价% | 最高% | 现价% |\n")
        lines.append("|------|------|-------|-------|-------|-------|\n")
        for code, name, pct, rt in sorted(lianban, key=lambda x: -x[3]['hp_pct']):
            lines.append(f"| {code} | {name} | {pct:.1f} | {rt['ap']:+.2f} | {rt['hp_pct']:+.2f} | {rt['cp_pct']:+.2f} |\n")
    lines.append("\n")
    
    # 炸板
    lines.append(f"### 💥 炸板（{len(zhaban)}只）\n")
    if zhaban:
        for code, name, pct, rt in sorted(zhaban, key=lambda x: -x[3]['hp_pct'])[:8]:
            lines.append(f"- {name}({code}) 最高{round(rt['hp_pct'],1)}% → {round(rt['cp_pct'],1)}%\n")
    lines.append("\n")
    
    # 高开
    lines.append(f"### ⚠️ 高开预警（{len(gaokai)}只）\n")
    if gaokai:
        for code, name, pct, rt in gaokai[:5]:
            lines.append(f"- {name}({code}) 竞价{round(rt['ap'],1)}% → {round(rt['cp_pct'],1)}%\n")
    lines.append("\n")
    
    # 平开
    lines.append(f"### 📋 平开/小幅（{len(pingkai)}只）\n")
    if pingkai:
        lines.append("| 代码 | 名称 | 昨日% | 竞价% | 现价% |\n")
        lines.append("|------|------|-------|-------|-------|\n")
        for code, name, pct, rt in sorted(pingkai, key=lambda x: -x[3]['cp_pct'])[:10]:
            lines.append(f"| {code} | {name} | {pct:.1f} | {rt['ap']:+.2f} | {rt['cp_pct']:+.2f} |\n")
    lines.append("\n")
    
    # 竞价区间分析
    lines.append("### 📈 竞价涨幅区间 vs 连板率\n")
    bins = [(-10,0), (0,2), (2,5), (5,9), (9,100)]
    names = ['低开(-∞,0)', '平开[0,2)', '小幅[2,5)', '中幅[5,9)', '高开[9,∞)']
    for (lo, hi), name in zip(bins, names):
        subset = [(c,n,p,r) for c,n,p,r in lianban+zhaban+gaokai+pingkai+other if r and lo <= r['ap'] < hi]
        lb = len([x for x in subset if x[3] and x[3]['cp_pct'] >= 9.5])
        total_bin = len(subset)
        rate = lb*100//total_bin if total_bin > 0 else 0
        lines.append(f"- **{name}**: {total_bin}只中{lf}{lb}只连板(**{rate}%**)\n")
    
    return ''.join(lines)

def main():
    today = datetime.date.today()
    
    # 判断是否交易日（排除周末）
    weekday = today.weekday()
    if weekday >= 5:
        print(f"今日({today})是周末，跳过采集")
        # 仍然尝试分析最近一个交易日的数据
        friday = today - datetime.timedelta(days=today.weekday() - 4)
        yesterday = friday - datetime.timedelta(days=1)
        if yesterday.weekday() >= 5:
            yesterday = friday - datetime.timedelta(days=3 if yesterday.weekday() == 6 else 1)
    else:
        yesterday = today - datetime.timedelta(days=1)
        if yesterday.weekday() >= 5:
            yesterday = today - datetime.timedelta(days=3 if yesterday.weekday() == 6 else 1)
    
    today_str = today.strftime('%Y%m%d')
    yesterday_str = yesterday.strftime('%Y%m%d')
    
    print(f"今日: {today_str} | 昨日交易日: {yesterday_str}")
    
    # 1. 获取今日涨停股（收集用）
    print("抓取今日涨停股...")
    zt_today = get_zt_stocks()
    print(f"  今日涨停: {len(zt_today)}只")
    save_today_data(today_str, zt_today)
    
    # 2. 获取昨日涨停股列表
    yz_file = f"{DATA_DIR}/zt_{yesterday_str}.txt"
    yesterday_stocks = []
    if os.path.exists(yz_file):
        with open(yz_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') or '|' not in line:
                    continue
                parts = line.split('|')
                if len(parts) >= 2:
                    yesterday_stocks.append({'code': parts[0], 'name': parts[1], 'changepercent': float(parts[2]) if len(parts)>2 else 0})
    
    # 如果没有昨日文件（第一次运行），则获取最近一次的数据
    if not yesterday_stocks and zt_today:
        yesterday_stocks = zt_today
        yesterday_str = today_str
        print("  无昨日数据，使用今日数据")
    
    # 3. 获取昨日涨停股的今日实时行情
    print("抓取竞价数据...")
    codes = [s['code'] for s in yesterday_stocks]
    # 构造带前缀的代码
    def prefix(code):
        if code.startswith(('9','8')): return f"bj{code}"
        elif code.startswith(('6','5','7')): return f"sh{code}"
        else: return f"sz{code}"
    
    code_prefixed = [prefix(c) for c in codes]
    
    # 分批获取
    realtime = {}
    for i in range(0, len(code_prefixed), 20):
        batch = code_prefixed[i:i+20]
        batch_rt = get_realtime_quotes(batch)
        realtime.update(batch_rt)
    
    print(f"  获取到: {len(realtime)}只")
    
    # 4. 生成报告
    report = analyze(yesterday_stocks, realtime, yesterday_str)
    
    # 5. 保存报告
    report_file = f"{OUT_DIR}/复盘_{yesterday_str}.md"
    with open(report_file, 'w') as f:
        f.write(f"# A股竞价复盘 {yesterday_str}\n\n")
        f.write(report)
    
    # 6. 更新策略
    update_strategy(yesterday_stocks, realtime, yesterday_str, report)
    
    print(f"\n报告已生成: {report_file}")
    print("\n" + report)
    print("\n策略已更新!")

def update_strategy(yesterday_stocks, realtime, date_str, analysis):
    """更新策略文件"""
    strategy_file = f"{OUT_DIR}/竞价选股策略.md"
    
    # 计算各竞价区间统计
    bins = [(-10,0), (0,2), (2,5), (5,9), (9,100)]
    bin_names = ['低开(-∞,0)', '平开[0,2)', '小幅[2,5)', '中幅[5,9)', '高开[9,∞)']
    bin_stats = {n: {'total': 0, 'lianban': 0} for n in bin_names}
    
    for s in yesterday_stocks:
        code = s['code']
        rt = realtime.get(code)
        if not rt:
            continue
        ap = rt['ap']
        for (lo, hi), name in zip(bins, bin_names):
            if lo <= ap < hi:
                bin_stats[name]['total'] += 1
                if rt['cp_pct'] >= 9.5:
                    bin_stats[name]['lianban'] += 1
                break
    
    # 生成策略更新
    strategy_text = f"""
## 复盘 {date_str}

### 竞价区间连板率数据

| 竞价区间 | 总数 | 连板数 | 率 |
|---------|------|--------|---|
"""
    for name in bin_names:
        s = bin_stats[name]
        rate = f"{s['lianban']*100//s['total'] if s['total']>0 else 0}%"
        strategy_text += f"| {name} | {s['total']} | {s['lianban']} | {rate} |\n"
    
    # 追加到策略文件
    try:
        with open(strategy_file, 'r') as f:
            existing = f.read()
    except:
        existing = ""
    
    # 找到插入点（在 ## 复盘记录 之后）
    if '## 复盘记录' in existing:
        parts = existing.split('## 复盘记录')
        header = parts[0]
        rest = parts[1]
    else:
        header = existing
        rest = ""
    
    with open(strategy_file, 'w') as f:
        f.write(header)
        f.write("## 复盘记录\n")
        f.write(strategy_text)
        f.write(rest)
    
    print(f"策略文件已更新: {strategy_file}")

if __name__ == '__main__':
    main()
