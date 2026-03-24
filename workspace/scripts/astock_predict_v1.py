#!/usr/bin/env python3
"""
A股晋级预测策略 v1.0
基于K线图形/成交量/技术指标，对涨停股做晋级成功率预测
身位 × 高度 × 图形 × 量价 综合判断
"""

import subprocess, json, datetime, os, sys, math
from collections import defaultdict

DATA_DIR = "/home/gem/workspace/agent/workspace/data/astock/100day"
KLINE_DIR = "/home/gem/workspace/agent/workspace/data/astock/100day/klines"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(KLINE_DIR, exist_ok=True)

# ========================
# K线获取（腾讯接口）
# ========================
def curl_kline_tencent(code, days=120):
    mkt = 'sh' if code.startswith(('6','9')) else 'sz'
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?_var=kline_dayhfq&param={mkt}{code},day,,,{days},qfq")
    try:
        p = subprocess.Popen(['curl', '-s', url], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        out, _ = p.communicate()
        txt = out.decode('utf-8', errors='ignore')
        if 'kline_dayhfq=' not in txt: return None
        data = json.loads(txt.split('=', 1)[1])
        qfq = data.get('data', {}).get(mkt+code, {})
        raw_days = qfq.get('day', []) or qfq.get('qfqday', [])
        result = {}
        for d in raw_days:
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

def get_kline(code):
    path = f"{KLINE_DIR}/{code}.json"
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    kl = curl_kline_tencent(code, 120)
    if kl:
        with open(path, 'w') as f:
            json.dump(kl, f, ensure_ascii=False)
    return kl or {}

# ========================
# 技术指标计算
# ========================
def ma(closes, n):
    if len(closes) < n: return None
    return sum(closes[-n:]) / n

def ema(closes, n):
    if len(closes) < n: return None
    k = 2.0 / (n + 1)
    ema_val = closes[0]
    for c in closes[1:]:
        ema_val = c * k + ema_val * (1 - k)
    return ema_val

def rsi(closes, n=14):
    if len(closes) < n+1: return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    if len(gains) < n: return None
    avg_gain = sum(gains[-n:]) / n
    avg_loss = sum(losses[-n:]) / n
    if avg_loss == 0: return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow+signal: return None, None, None
    def _ema(c, n):
        k = 2.0/(n+1)
        e = c[0]
        for v in c[1:]: e = v*k + e*(1-k)
        return e
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    dif = ef - es
    de = _ema([dif]*signal, signal) if dif is not None else None
    macd = 2*(dif - de) if (dif and de) else None
    return dif, de, macd

def boll(closes, n=20, k=2):
    if len(closes) < n: return None, None, None
    ms = sum(closes[-n:]) / n
    std = math.sqrt(sum((c-ms)**2 for c in closes[-n:]) / n)
    return ms, ms+k*std, ms-k*std

def kdj(highs, lows, closes, n=9):
    if len(closes) < n: return None, None, None
    k_val, d_val = 50.0, 50.0
    for i in range(n-1, len(closes)):
        low_n = min(lows[max(0,i-n+1):i+1])
        high_n = max(highs[max(0,i-n+1):i+1])
        rsv = (closes[i]-low_n)/(high_n-low_n)*100 if high_n != low_n else 50
        k_val = k_val*2/3 + rsv/3
        d_val = d_val*2/3 + k_val/3
    j_val = 3*k_val - 2*d_val
    return k_val, d_val, j_val

def vol_ma(vols, n=20):
    if len(vols) < n: return None
    return sum(vols[-n:]) / n

# ========================
# 周K线/月K线
# ========================
def to_weekly(kl):
    """日K线 → 周K线"""
    if not kl: return {}
    dts = sorted(kl.keys())
    weeks = defaultdict(lambda: {'open':None,'close':None,'high':0,'low':float('inf'),'vol':0})
    for dt in dts:
        wkey = dt[:7] + '-W' + str(datetime.date.fromisoformat(dt).isocalendar()[1]).zfill(2)
        d = kl[dt]
        if weeks[wkey]['open'] is None: weeks[wkey]['open'] = d['open']
        weeks[wkey]['close'] = d['close']
        weeks[wkey]['high'] = max(weeks[wkey]['high'], d['high'])
        weeks[wkey]['low'] = min(weeks[wkey]['low'], d['low'])
        weeks[wkey]['vol'] += d['vol']
    return {w: {k: v for k, v in weeks[w].items() if v != float('inf') or k != 'low'} for w in weeks}

def to_monthly(kl):
    """日K线 → 月K线"""
    if not kl: return {}
    months = defaultdict(lambda: {'open':None,'close':None,'high':0,'low':float('inf'),'vol':0})
    for dt in sorted(kl.keys()):
        mkey = dt[:7]
        d = kl[dt]
        if months[mkey]['open'] is None: months[mkey]['open'] = d['open']
        months[mkey]['close'] = d['close']
        months[mkey]['high'] = max(months[mkey]['high'], d['high'])
        months[mkey]['low'] = min(months[mkey]['low'], d['low'])
        months[mkey]['vol'] += d['vol']
    return {m: months[m] for m in months}

# ========================
# 图形识别
# ========================
def recognize_pattern(kl, n=20):
    """识别最近n天的典型K线图形"""
    if len(kl) < n: return "数据不足"
    dts = sorted([k for k in kl.keys() if k.startswith('202')])
    recent = [{'open':kl[d]['open'],'close':kl[d]['close'],'high':kl[d]['high'],'low':kl[d]['low'],'vol':kl[d]['vol']} for d in dts[-n:]]
    
    closes = [x['close'] for x in recent]
    highs = [x['high'] for x in recent]
    lows = [x['low'] for x in recent]
    vols = [x['vol'] for x in recent]
    
    # 计算指标
    ma5 = ma(closes, 5)
    ma10 = ma(closes, 10)
    ma20 = ma(closes, 20)
    ma60 = ma(closes, 60) if len(closes) >= 60 else None
    cur = closes[-1]
    
    # 趋势判断
    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20:
            trend = "上升通道"
        elif ma5 < ma10 < ma20:
            trend = "下降通道"
        elif ma5 > ma10 and ma10 < ma20:
            trend = "底部金叉"
        elif ma5 < ma10 and ma10 > ma20:
            trend = "顶部死叉"
        else:
            trend = "震荡整理"
    else:
        trend = "均线纠缠"
    
    # 放量缩量
    vol_ma20 = vol_ma(vols, 20)
    recent_vol_ratio = vols[-1] / vol_ma20 if vol_ma20 else 1
    
    # K线形态
    last = recent[-1]
    body = abs(last['close'] - last['open']) / last['open'] * 100
    upper_shadow = (last['high'] - max(last['close'], last['open'])) / last['open'] * 100
    lower_shadow = (min(last['close'], last['open']) - last['low']) / last['open'] * 100
    
    if body < 0.5:
        shape = "十字星"
    elif last['close'] > last['open']:
        if upper_shadow > body*2 and lower_shadow < body:
            shape = "倒锤头"
        elif body > 4:
            shape = "大阳线"
        else:
            shape = "阳线"
    else:
        if upper_shadow > body*2 and lower_shadow < body:
            shape = "射击星"
        elif body > 4:
            shape = "大阴线"
        else:
            shape = "阴线"
    
    # 突破判断
    breakout_20 = "突破" if cur > ma20 and closes[-2] <= ma20 else "未破"
    breakout_60 = "突破" if ma60 and cur > ma60 else "未破"
    
    # 缩量整理
    vol_shrink = "缩量" if recent_vol_ratio < 0.8 else ("放量" if recent_vol_ratio > 1.5 else "正常量")
    
    return {
        'trend': trend, 'shape': shape,
        'vol_status': vol_shrink, 'vol_ratio': round(recent_vol_ratio, 2),
        'ma5': round(ma5, 2) if ma5 else None,
        'ma10': round(ma10, 2) if ma10 else None,
        'ma20': round(ma20, 2) if ma20 else None,
        'ma60': round(ma60, 2) if ma60 else None,
        'price_vs_ma20': "MA20上方" if cur > ma20 else "MA20下方",
        'price_vs_ma10': "MA10上方" if cur > ma10 else "MA10下方",
        '突破20日': breakout_20, '突破60日': breakout_60,
        'rsi': round(rsi(closes), 1) if rsi(closes) else None,
    }

def analyze_position_height(kl, name=''):
    """判断股票身位和高度"""
    if not kl: return {}
    # 过滤掉非日期键
    dts = sorted([k for k in kl.keys() if k.startswith('202')])
    
    # 最近涨停日
    zt_days = []
    for i, dt in enumerate(dts):
        if i == 0: continue
        prev_close = kl[dts[i-1]]['close']
        pct = (kl[dt]['close'] - prev_close) / prev_close * 100
        if pct >= 9.5:
            zt_days.append({'date': dt, 'index': i, 'pct': pct})
    
    if not zt_days:
        return {'position': '首板', 'height': '1板', 'zt_count': 0, 'zt_days': []}
    
    last_zt = zt_days[-1]
    last_zt_idx = last_zt['index']
    
    # 计算身位：从哪一天开始连续涨停
    consecutive = 0
    expected_prev = None
    for zt in reversed(zt_days):
        if expected_prev is None:
            consecutive = 1
            expected_prev = zt['index'] - 1
        elif zt['index'] == expected_prev:
            consecutive += 1
            expected_prev = zt['index'] - 1
        else:
            break
    
    position = f"{consecutive}进{consecutive+1}"
    height_map = {1: '低位1板', 2: '中位2板', 3: '高位3板', 4: '妖股4板', 5: '妖股5板+'}
    height = height_map.get(consecutive, f"妖股{consecutive}板+")
    
    # 计算距今天数
    last_trade_date = dts[-1]
    zt_date_obj = datetime.date.fromisoformat(last_zt['date'])
    last_trade_obj = datetime.date.fromisoformat(last_trade_date)
    days_ago = (last_trade_obj - zt_date_obj).days
    
    return {
        'position': position,
        'height': height,
        'zt_count': len(zt_days),
        'zt_days': [z['date'] for z in zt_days],
        'days_ago': days_ago,  # 距上次涨停天数
        'last_zt_pct': round(last_zt['pct'], 1),
        'consecutive': consecutive,
    }

# ========================
# 晋级预测引擎
# ========================
def predict_single(code, kl, position_data, today_auction_pct=None):
    """对单只股票预测晋级成功率"""
    if not kl: return None
    dts = sorted([k for k in kl.keys() if k.startswith('202')])
    if len(dts) < 5: return None
    
    recent = dts[-20:]  # 最近20天
    closes = [kl[d]['close'] for d in recent]
    highs = [kl[d]['high'] for d in recent]
    lows = [kl[d]['low'] for d in recent]
    vols = [kl[d]['vol'] for d in recent]
    
    pattern = recognize_pattern(kl, 20)
    pos_info = position_data
    
    # === 基础分（竞价已知数据）===
    auction = today_auction_pct if today_auction_pct is not None else 0  # 假设平开
    base_score = 50
    
    # 竞价得分
    if auction < 0:
        auction_score = 0
    elif auction < 2:
        auction_score = 10
    elif auction < 5:
        auction_score = 30
    elif auction < 9:
        auction_score = 45
    else:
        auction_score = 60  # 一字板无买点
    
    # === 技术图形得分 ===
    tech_score = 0
    
    # 趋势得分
    trend = pattern.get('trend', '震荡整理')
    if trend == '上升通道': tech_score += 20
    elif trend == '底部金叉': tech_score += 15
    elif trend == '均线纠缠': tech_score += 5
    elif trend == '顶部死叉': tech_score -= 15
    elif trend == '下降通道': tech_score -= 20
    
    # 价格vs均线
    if pattern.get('price_vs_ma20') == 'MA20上方': tech_score += 10
    else: tech_score -= 10
    
    # RSI
    rsi_val = pattern.get('rsi')
    if rsi_val:
        if rsi_val > 80: tech_score -= 15  # 超买
        elif rsi_val > 70: tech_score -= 5
        elif rsi_val < 20: tech_score += 10  # 超卖反弹
        elif rsi_val < 30: tech_score += 5
    
    # MACD
    dif, de, macd_val = macd(closes)
    if dif and de:
        if dif > 0 and macd_val > 0: tech_score += 10  # MACD多头
        elif dif < 0 and macd_val < 0: tech_score -= 10  # MACD空头
    
    # 缩量整理（涨停次日预期整理）
    if pattern.get('vol_status') == '缩量': tech_score += 10
    elif pattern.get('vol_status') == '放量' and auction > 0: tech_score += 5  # 温和放量好
    
    # 突破均线
    if pattern.get('突破20日') == '✅突破': tech_score += 10
    if pattern.get('突破60日') == '✅突破': tech_score += 10
    
    # === 身位高度调整 ===
    pos = pos_info.get('position', '1进2')
    height = pos_info.get('height', '1板')
    pos_adj = 0
    
    if pos == '1进2':
        if auction < 2: pos_adj = -10  # 平开1进2风险大
        elif auction >= 2: pos_adj = 5
    elif pos == '2进3':
        if pattern.get('突破20日') == '❌未破': pos_adj = -15
        if pattern.get('vol_status') == '缩量': pos_adj += 10
    elif '妖股' in height:
        # 妖股高位，需要缩量和板块联动
        if pattern.get('vol_status') == '缩量': pos_adj += 10
        else: pos_adj -= 20
        if pattern.get('突破60日') == '✅突破': pos_adj += 5
        else: pos_adj -= 10
    
    # === 历史统计调整 ===
    # 从v3_trans.json读取历史晋级率
    trans_path = f"{DATA_DIR}/v3_trans.json"
    hist_rate = 0.19  # 默认全局19%
    if os.path.exists(trans_path):
        with open(trans_path) as f:
            all_trans = json.load(f)
        pos_key = pos  # e.g. "1进2"
        pos_recs = [r for r in all_trans if r.get('pk') == pos_key and not r.get('yzb')]
        if pos_recs:
            lb_rate = len([r for r in pos_recs if r['outcome']=='连板']) / len(pos_recs)
            hist_rate = lb_rate
    
    hist_adj = int((hist_rate - 0.19) * 300)  # 相对基准调整
    
    # === 资金估算 ===
    # 用成交量变化估算资金活跃度
    vol_r = pattern.get('vol_ratio', 1)
    money_score = 0
    if vol_r < 0.5: money_score = 15  # 极度缩量=主力控盘
    elif vol_r < 0.8: money_score = 8
    elif vol_r > 3: money_score = -10  # 爆量高位分歧
    
    # === 综合得分 ===
    total = base_score + auction_score + tech_score + pos_adj + hist_adj + money_score
    prob = max(0, min(100, total))
    
    return {
        'code': code,
        'name': kl.get('name', ''),
        'prob': prob,
        'auction_pct': auction,
        'pattern': pattern,
        'position': pos_info,
        'breakdown': {
            '基础分': base_score,
            '竞价分': auction_score,
            '图形分': tech_score,
            '身位调整': pos_adj,
            '历史统计': hist_adj,
            '资金活跃': money_score,
        },
        'signal': '✅强烈推荐' if prob >= 70 else ('⚠️可关注' if prob >= 50 else '❌回避'),
        'reason': build_reason(pattern, pos_info, auction, tech_score),
    }

def build_reason(pattern, pos_info, auction, tech_score):
    reasons = []
    trend = pattern.get('trend', '')
    if trend == '上升通道': reasons.append('上升通道')
    if pattern.get('突破20日') == '✅突破': reasons.append('突破20日均线')
    if pattern.get('突破60日') == '✅突破': reasons.append('突破60日均线')
    if pattern.get('vol_status') == '缩量': reasons.append('缩量整理')
    if pattern.get('rsi'):
        if pattern['rsi'] < 30: reasons.append(f'RSI超卖({pattern["rsi"]})')
        elif pattern['rsi'] > 70: reasons.append(f'RSI超买({pattern["rsi"]})')
    if auction >= 2: reasons.append(f'竞价{auction:.1f}%')
    if pos_info.get('height') == '低位1板': reasons.append('低位1板')
    if '妖股' in pos_info.get('height', ''):
        if pattern.get('vol_status') == '缩量': reasons.append('妖股缩量')
    return ', '.join(reasons) if reasons else '无明显信号'

# ========================
# 主预测函数
# ========================
def predict_batch(stocks, auction_data=None):
    """
    stocks: [{code, name, lb_count, industry, ...}]
    auction_data: {code: auction_pct} 今日竞价涨幅（已知时传入）
    """
    results = []
    for s in stocks:
        code = s['code']
        name = s.get('name', '')
        kl = get_kline(code)
        if not kl: continue
        
        # 注入name到kl（但排除在日期键之外）
        kl['__name__'] = name
        
        pos_data = analyze_position_height(kl)
        auction = auction_data.get(code) if auction_data else None
        pred = predict_single(code, kl, pos_data, auction)
        if pred:
            pred['lb_count'] = s.get('lb_count', 1)
            pred['industry'] = s.get('industry', '')
            results.append(pred)
    
    # 按概率排序
    results.sort(key=lambda x: x['prob'], reverse=True)
    return results

# ========================
# 报告生成
# ========================
def generate_report(predictions, title="晋级预测报告"):
    today = datetime.date.today().strftime("%Y%m%d")
    lines = [f"{'='*60}"]
    lines.append(f"{title}  {today}")
    lines.append(f"{'='*60}")
    
    # 按身位分组
    by_pos = defaultdict(list)
    for p in predictions: by_pos[p['position'].get('position','未知')].append(p)
    
    for pos in sorted(by_pos.keys()):
        recs = by_pos[pos]
        lines.append(f"\n{'━'*50}")
        lines.append(f"【{pos}】{len(recs)}只")
        lines.append(f"{'━'*50}")
        for r in recs:
            code = r['code']
            prob = r['prob']
            signal = r['signal']
            h = r['position']['height']
            trend = r['pattern'].get('trend','')
            vol = r['pattern'].get('vol_status','')
            ma20 = r['pattern'].get('price_vs_ma20','')
            lines.append(f"\n  {code} {r['name']} | 晋级概率:{prob}% {signal}")
            lines.append(f"    高度:{h} | 趋势:{trend} | 量:{vol} | {ma20}")
            lines.append(f"    竞价:{r['auction_pct']}% | RSI:{r['pattern'].get('rsi', 'N/A')}")
            lines.append(f"    信号:{r['reason']}")
            bd = r['breakdown']
            lines.append(f"    评分: 基{ bd['基础分']} 竞{ bd['竞价分']} 图{ bd['图形分']} 位{ bd['身位调整']} 历{ bd['历史统计']} 金{ bd['资金活跃']}")
    
    strong = [r for r in predictions if r['prob'] >= 70]
    monitor = [r for r in predictions if 50 <= r['prob'] < 70]
    avoid = [r for r in predictions if r['prob'] < 50]
    
    lines.append(f"\n{'='*60}")
    lines.append(f"汇总：✅强烈推荐 {len(strong)} 只 | ⚠️可关注 {len(monitor)} 只 | ❌回避 {len(avoid)} 只")
    lines.append(f"{'='*60}")
    
    if strong:
        lines.append("\n【✅强烈推荐】")
        for r in strong:
            lines.append(f"  {r['code']} {r['name']} {r['prob']}% [{r['position']['height']}] {r['reason']}")
    
    if monitor:
        lines.append("\n【⚠️可关注】")
        for r in monitor[:10]:
            lines.append(f"  {r['code']} {r['name']} {r['prob']}% [{r['position']['height']}] {r['reason']}")
    
    return '\n'.join(lines)

# ========================
# 演示：读取昨日涨停股，跑预测
# ========================
if __name__ == '__main__':
    import pandas as pd
    
    # 读取昨日涨停股
    today_file = f"{DATA_DIR}/20260320.json"
    if not os.path.exists(today_file):
        print("无历史数据文件，请先运行 astock_daily_v2.py 收集数据")
        sys.exit(1)
    
    with open(today_file) as f:
        stocks = json.load(f)
    
    print(f"分析 {len(stocks)} 只昨日涨停股...")
    
    predictions = predict_batch(stocks)
    report = generate_report(predictions, "昨日涨停股晋级预测")
    print(report)
    
    # 保存
    out_path = f"{DATA_DIR}/predictions_{datetime.date.today().strftime('%Y%m%d')}.json"
    with open(out_path, 'w') as f:
        json.dump(predictions, f, ensure_ascii=False, indent=2)
    print(f"\n预测结果已保存: {out_path}")
