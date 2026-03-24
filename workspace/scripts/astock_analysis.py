#!/usr/bin/env python3
"""
A股每日竞价复盘分析
- 每日22:00运行
- 分析昨日涨停股今日连板情况
- 分析竞价特征形成策略
"""

import os
import json
import datetime
import subprocess
import sys

DATA_DIR = "/home/gem/workspace/agent/workspace/data/astock"
STRATEGY_FILE = "/home/gem/workspace/agent/workspace/astock_strategy/竞价选股策略.md"

def get_zt_data(date_str):
    """获取指定日期的涨停股数据"""
    f = f"{DATA_DIR}/zt_{date_str}.txt"
    if not os.path.exists(f):
        return None
    stocks = {}
    with open(f) as fp:
        for line in fp:
            line = line.strip()
            if line.startswith('#') or not line:
                continue
            parts = line.split('|')
            if len(parts) >= 6:
                code, name, pct, amount, vol, auction = parts[:6]
                stocks[code] = {
                    'name': name, 'pct': float(pct), 
                    'amount': float(amount), 'vol': float(vol),
                    'auction_pct': float(auction)
                }
    return stocks

def get_index_data():
    """获取当日指数数据"""
    try:
        result = subprocess.run([
            'curl', '-s', 
            'https://qt.gtimg.cn/q=s_sh000001,s_sz399001,s_sz399006,s_sh000016,s_sh000300',
            '-H', 'Referer: https://finance.qq.com'
        ], capture_output=True, text=True, timeout=10)
        output = result.stdout.decode('utf-8', errors='ignore')
        indices = {}
        for line in output.split('\n'):
            if 'sh000001' in line or 'sz399001' in line or 'sz399006' in line:
                parts = line.split('~')
                if len(parts) > 5:
                    name = parts[1]
                    close = parts[3]
                    change = parts[4]
                    indices[name] = {'close': close, 'change': change}
        return indices
    except:
        return {}

def get_stock_realtime(codes):
    """批量获取股票实时数据（今日竞价分析用）"""
    if not codes:
        return {}
    code_str = ','.join([f"sh{c}" if c.startswith('6') or c.startswith('9') else f"sz{c}" for c in codes])
    try:
        result = subprocess.run([
            'curl', '-s', f'https://qt.gtimg.cn/q={code_str}',
            '-H', 'Referer: https://finance.qq.com'
        ], capture_output=True, text=True, timeout=15)
        output = result.stdout.decode('gbk', errors='ignore')
        stocks = {}
        for line in output.split('\n'):
            if 'v_p_' not in line:
                continue
            parts = line.split('~')
            if len(parts) > 5:
                raw_code = parts[2]
                code = raw_code.replace('sh', '').replace('sz', '')
                name = parts[1]
                yesterday_close = float(parts[4])  # 昨收
                today_open = float(parts[5]) if parts[5] else 0  # 今开
                today_high = float(parts[33]) if parts[33] else 0  # 最高
                today_low = float(parts[34]) if parts[34] else 0   # 最低
                current = float(parts[3])  # 现价
                
                auction_pct = (today_open/yesterday_close - 1)*100 if yesterday_close > 0 else 0
                high_pct = (today_high/yesterday_close - 1)*100 if yesterday_close > 0 else 0
                
                stocks[code] = {
                    'name': name, 'yesterday_close': yesterday_close,
                    'today_open': today_open, 'today_high': today_high,
                    'today_low': today_low, 'current': current,
                    'auction_pct': auction_pct, 'high_pct': high_pct
                }
        return stocks
    except Exception as e:
        print(f"Error fetching realtime: {e}")
        return {}

def format_report(zt_yesterday, realtime_today, date_label):
    """生成复盘报告"""
    lines = []
    today_str = datetime.datetime.now().strftime('%Y年%m月%d日')
    lines.append(f"# 📊 A股竞价复盘 — {today_str}\n")
    lines.append(f"**分析日期**: {date_label} 涨停股 → 今日表现\n")
    
    # 指数
    indices = get_index_data()
    if indices:
        lines.append("## 大盘指数\n")
        for name, data in indices.items():
            change = float(data['change'])
            emoji = '🔴' if change < 0 else '🟢'
            lines.append(f"- {name}: **{data['close']}** {emoji} {change:+.2f}%\n")
        lines.append("\n")
    
    if not zt_yesterday:
        lines.append("⚠️ 昨日无涨停股数据\n")
        return ''.join(lines)
    
    # 连板分析
    lines.append(f"## 昨日涨停股概况（共 {len(zt_yesterday)} 只）\n")
    
    codes = list(zt_yesterday.keys())
    realtime = get_stock_realtime(codes)
    
    # 分类
    lianban = []      # 继续涨停
    kaipan_bi = []   # 开盘即涨停
    chaoban = []     # 炸板（曾涨停但开板）
    pingkai = []     # 平开或小幅高开
    gaokai_drop = [] # 高开低走
    
    for code, info in zt_yesterday.items():
        rt = realtime.get(code, {})
        name = info['name']
        yesterday_close = info['yesterday_close']
        
        if not rt:
            continue
            
        auction_pct = rt['auction_pct']
        high_pct = rt['high_pct']
        current_pct = (rt['current']/yesterday_close - 1)*100 if yesterday_close > 0 else 0
        
        if current_pct >= 9.5:
            lianban.append((code, name, info, rt))
        elif high_pct >= 9.5 and current_pct < 9.5:
            chaoban.append((code, name, info, rt))
        elif auction_pct >= 9.5 and current_pct < 9.5:
            kaipan_bi.append((code, name, info, rt))
        elif auction_pct > 0 and auction_pct < 5 and current_pct > 5:
            pingkai.append((code, name, info, rt))
        elif auction_pct > 5 and current_pct < auction_pct - 2:
            gaokai_drop.append((code, name, info, rt))
    
    # 连板股详情
    if lianban:
        lines.append(f"### ✅ 继续涨停（{len(lianban)}只）\n")
        lines.append("| 代码 | 名称 | 昨日涨停% | 竞价涨幅% | 今日最高% | 现价% |\n")
        lines.append("|------|------|---------|---------|---------|------|\n")
        for code, name, info, rt in sorted(lianban, key=lambda x: -x[3]['high_pct']):
            lines.append(f"| {code} | {name} | {info['pct']:.1f} | {rt['auction_pct']:+.2f} | {rt['high_pct']:+.2f} | {(rt['current']/info['yesterday_close']-1)*100:+.2f} |\n")
        lines.append("\n")
    
    # 炸板股
    if chaoban:
        lines.append(f"### 💥 炸板（{len(chaoban)}只）\n")
        lines.append("| 代码 | 名称 | 竞价涨幅% | 最高% | 现价% |\n")
        lines.append("|------|------|---------|------|------|\n")
        for code, name, info, rt in chaoban[:10]:
            lines.append(f"| {code} | {name} | {rt['auction_pct']:+.2f} | {rt['high_pct']:+.2f} | {(rt['current']/info['yesterday_close']-1)*100:+.2f} |\n")
        lines.append("\n")
    
    # 开盘秒板后炸
    if kaipan_bi:
        lines.append(f"### ⚡ 开盘即板后炸（{len(kaipan_bi)}只）\n")
        for code, name, info, rt in kaipan_bi[:5]:
            lines.append(f"- {name}({code}) 竞价{Rt['auction_pct']:+.2f}% 最高{rt['high_pct']:+.2f}%\n")
        lines.append("\n")
    
    # 竞价分析
    lines.append("## 竞价特征分析\n")
    
    # 统计各竞价区间表现
    bins = [(-10, 0), (0, 2), (2, 5), (5, 9), (9, 100)]
    bin_names = ['低开(-∞,0)', '平开[0,2)', '小幅[2,5)', '中幅[5,9)', '高开[9,∞)']
    
    lines.append("### 竞价涨幅 vs 连板率\n")
    bin_stats = {b: {'total': 0, 'lianban': 0} for b in bins}
    
    for code, info in zt_yesterday.items():
        rt = realtime.get(code, {})
        if not rt:
            continue
        ap = rt['auction_pct']
        current_pct = (rt['current']/info['yesterday_close']-1)*100 if info['yesterday_close'] > 0 else -999
        
        for (low, high), name in zip(bins, bin_names):
            if low <= ap < high:
                bin_stats[(low, high)]['total'] += 1
                if current_pct >= 9.5:
                    bin_stats[(low, high)]['lianban'] += 1
                break
    
    for (low, high), name in zip(bins, bin_names):
        s = bin_stats[(low, high)]
        total = s['total']
        lb = s['lianban']
        rate = (lb/total*100) if total > 0 else 0
        lines.append(f"- **{name}**: 共{total}只，连板{lb}只，率 **{rate:.0f}%**\n")
    
    lines.append("\n## 今日策略信号\n")
    
    # 简单策略信号
    if bin_stats[(0, 2)]['total'] > 0:
        rate = bin_stats[(0, 2)]['lianban'] / bin_stats[(0, 2)]['total'] * 100
        if rate > 50:
            lines.append(f"✅ **平开低吸策略有效**: 平开区间连板率 {rate:.0f}%，可关注\n")
        else:
            lines.append(f"⚠️ **平开区间连板率低**: {rate:.0f}%，谨慎\n")
    
    if bin_stats[(2, 5)]['total'] > 0:
        rate = bin_stats[(2, 5)]['lianban'] / bin_stats[(2, 5)]['total'] * 100
        lines.append(f"📊 **小幅高开区间**连板率: {rate:.0f}%\n")
    
    return ''.join(lines)

def update_strategy_report(analysis_text):
    """更新策略文件"""
    header = """# 竞价选股策略

> ⚠️ 策略随每日复盘持续迭代进化

## 核心指标库

| 指标 | 说明 | 数据来源 |
|------|------|---------|
| 竞价涨幅 | (今开-昨收)/昨收×100% | 腾讯实时 |
| 竞价成交量 | 开盘成交额/昨日成交额 | 腾讯实时 |
| 封单金额 | 涨停时封单量×股价 | 腾讯实时 |
| 换手率 | 成交额/流通市值 | 计算 |
| 板块联动 | 所属板块涨停数量 | 东财 |

## 策略公式 v1.0

### 一、连板晋级筛选条件

```
必须同时满足:
1. 昨日涨停（非新股、非北交所、非科创板）
2. 竞价涨幅 ∈ [0%, 5%]    ← 最佳区间，过高易炸板
3. 竞价成交量 > 昨日成交额×15%
4. 封单金额 > 5000万
5. 股价 < 50元（中小盘偏好）

排除:
× 竞价涨幅 > 8%（高开秒板易炸）
× 昨日涨停时间在14:30之后（弱势板）
× 所属板块无跟风股
```

### 二、各身位策略

| 身位 | 竞价涨幅参考 | 核心逻辑 |
|------|------------|---------|
| 首板 | [0%, 3%] | 温和放量引导，市场认可 |
| 二板 | [0%, 5%] | 资金接力意愿强 |
| 三板+ | [0%, 7%] | 妖股信仰，适度高开可接受 |
| 高位板(6板+) | 谨慎 | 情绪末端，高开多为陷阱 |

### 三、炸板预警

以下情况竞价优先卖出:
- 竞价涨幅 > 9% 且开盘30分钟内开板
- 封单金额10分钟内萎缩>50%
- 板块内跟风股大面积炸板

---

"""
    try:
        with open(STRATEGY_FILE, 'r') as f:
            existing = f.read()
    except:
        existing = ""
    
    # 追加今日分析
    with open(STRATEGY_FILE, 'w') as f:
        f.write(header)
        f.write(f"\n## 复盘记录\n\n{analysis_text}\n")

def main():
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    # 如果是周一，获取上周五数据
    if yesterday.weekday() == 6:  # 周日
        yesterday = yesterday - datetime.timedelta(days=2)
    yesterday_str = yesterday.strftime('%Y%m%d')
    
    print(f"分析: 昨日涨停({yesterday_str}) → 今日表现")
    
    zt_yesterday = get_zt_data(yesterday_str)
    if not zt_yesterday:
        print(f"无昨日数据: {yesterday_str}")
        # 尝试抓取
        os.system(f"bash {sys.path[0]}/astock_daily.sh {yesterday_str}")
        zt_yesterday = get_zt_data(yesterday_str)
    
    realtime = {}
    if zt_yesterday:
        realtime = get_stock_realtime(list(zt_yesterday.keys()))
    
    report = format_report(zt_yesterday, realtime, yesterday_str)
    print(report)
    
    update_strategy_report(report)
    print(f"\n策略文件已更新: {STRATEGY_FILE}")

if __name__ == '__main__':
    main()
