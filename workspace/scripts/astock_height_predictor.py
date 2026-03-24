#!/usr/bin/env python3
"""
A股高度预测模块 v1.0
给定一只正在连板的股票，预测其最终能够达到的连板高度
"""

import json, os, datetime, math
from collections import defaultdict

DATA_DIR = "/home/gem/workspace/agent/workspace/data/astock/100day"
KLINE_DIR = f"{DATA_DIR}/klines"
os.makedirs(DATA_DIR, exist_ok=True)

# ========================
# 基础概率（从历史数据统计）
# ========================
# 条件概率：N板 → N+1板
COND_PROBS = {
    1: 0.13,   # 1进2
    2: 0.15,   # 2进3
    3: 0.20,   # 3进4
    4: 0.29,   # 4进5
    5: 0.00,   # 5进6（仅1样本）
}

# 从538只历史涨停股统计的最终高度分布
HEIGHT_DIST = {
    1: 0,   # 最终1板（即首板即最高）
    2: 0,   # 最终2板
    3: 0,   # 最终3板
    4: 0,   # 最终4板
    5: 0,   # 最终5板+
}

# 各板高度的大致概率（从样本估算）
# 注意：受幸存者偏差影响，1板股实际更高比例会断板
HEIGHT_PROB_FROM_1 = {1: 0.80, 2: 0.14, 3: 0.035, 4: 0.015, 5: 0.01}

# ========================
# 技术指标计算
# ========================
def curl_kline_tencent(code, days=120):
    import subprocess
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
                result[d[0]] = {
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

def ma(closes, n):
    if len(closes) < n: return None
    return sum(closes[-n:]) / n

def ema(closes, n):
    if len(closes) < n: return None
    k = 2.0 / (n + 1)
    e = closes[0]
    for v in closes[1:]: e = v*k + e*(1-k)
    return e

def macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow+signal: return None, None, None
    def _ema(c, n):
        k = 2.0/(n+1); e = c[0]
        for v in c[1:]: e = v*k + e*(1-k)
        return e
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    dif = ef - es
    de = _ema([dif]*signal, signal) if dif is not None else None
    macd = 2*(dif-de) if (dif and de) else None
    return dif, de, macd

def rsi(closes, n=14):
    if len(closes) < n+1: return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-n:]) / n
    avg_loss = sum(losses[-n:]) / n
    if avg_loss == 0: return 100
    return 100 - (100 / (1 + avg_gain/avg_loss))

def vol_ma(vols, n=20):
    if len(vols) < n: return None
    return sum(vols[-n:]) / n

# ========================
# 高度预测核心
# ========================
def predict_height(code, current_boards, kl=None):
    """
    预测股票最终能达到的连板高度
    
    参数:
        code: 股票代码
        current_boards: 当前身位（如3表示处于3板，已完成3板）
        kl: K线数据（可选）
    
    返回:
        {
            'code': str,
            'current_boards': int,
            'expected_boards': float,    # 期望高度（加权）
            'max_prob_board': int,       # 最可能的最终板数
            'prob_reach_N': {N: probability},  # 各高度的达成概率
            'confidence': str,           # 高/中/低
            'factors': [...],            # 影响因素
            'signal': str,               # 描述信号
        }
    """
    if kl is None:
        kl = get_kline(code)
    
    dts = sorted([k for k in kl.keys() if k.startswith('202')])
    if len(dts) < 5:
        return None
    
    # 技术面分析
    closes = [kl[d]['close'] for d in dts]
    vols = [kl[d]['vol'] for d in dts]
    highs = [kl[d]['high'] for d in dts]
    lows = [kl[d]['low'] for d in dts]
    
    ma5 = ma(closes, 5)
    ma10 = ma(closes, 10)
    ma20 = ma(closes, 20)
    ma60 = ma(closes, 60) if len(closes) >= 60 else None
    
    dif, de, macd_val = macd(closes)
    rsi_val = rsi(closes)
    cur = closes[-1]
    
    vol_ma20 = vol_ma(vols, 20)
    vol_ratio = vols[-1] / vol_ma20 if vol_ma20 else 1
    
    # === 判断趋势 ===
    trend = '震荡'
    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20:
            trend = '上升通道'
        elif ma5 < ma10 < ma20:
            trend = '下降通道'
    
    # === 调整系数：基于技术面对各条件概率做调整 ===
    adj = 1.0
    factors = []
    
    # 趋势加分
    if trend == '上升通道':
        adj *= 1.3
        factors.append('上升通道 +30%')
    elif trend == '下降通道':
        adj *= 0.5
        factors.append('下降通道 -50%')
    
    # 量能判断
    if vol_ratio < 0.5:
        adj *= 1.4
        factors.append('极度缩量 +40%')
    elif vol_ratio < 0.8:
        adj *= 1.2
        factors.append('缩量 +20%')
    elif vol_ratio > 3:
        adj *= 0.6
        factors.append('爆量 -40%')
    
    # MACD
    if dif and dif > 0 and macd_val and macd_val > 0:
        adj *= 1.2
        factors.append('MACD多头 +20%')
    elif dif and dif < 0:
        adj *= 0.7
        factors.append('MACD空头 -30%')
    
    # RSI
    if rsi_val:
        if rsi_val > 85:
            adj *= 0.5
            factors.append(f'RSI极度超买({rsi_val:.0f}) -50%')
        elif rsi_val > 75:
            adj *= 0.75
            factors.append(f'RSI超买({rsi_val:.0f}) -25%')
        elif rsi_val < 30:
            adj *= 1.1
            factors.append(f'RSI超卖({rsi_val:.0f}) +10%')
    
    # 位置判断
    if cur > ma20:
        adj *= 1.15
        factors.append('股价在MA20上方 +15%')
    else:
        adj *= 0.7
        factors.append('股价在MA20下方 -30%')
    
    if ma60 and cur > ma60:
        adj *= 1.1
        factors.append('突破MA60 +10%')
    
    # 妖股特殊判断（4板以上）
    if current_boards >= 4:
        if vol_ratio < 0.8:
            adj *= 1.3
            factors.append('妖股缩量控盘 +30%')
        elif vol_ratio > 2:
            adj *= 0.4
            factors.append('妖股爆量=出货 -60%')
        if rsi_val and rsi_val > 90:
            adj *= 0.3
            factors.append('妖股RSI>90极端风险 -70%')
    
    # === 计算各高度概率 ===
    probs = {}
    cb = current_boards  # 当前板数
    
    # 基础路径概率（Markov链）
    # P(达到N板) = P(续N板|曾在N-1板) × P(续N-1板|曾在N-2板) × ... × P(续2板|曾在1板)
    # 但这个是条件概率，实际我们要估算"在当前板数Cb下，最终到达Eh板的概率"
    
    # 简化模型：用条件概率×调整系数
    adj = max(0.1, min(adj, 2.5))  # 限制在[0.1, 2.5]
    
    for target_boards in range(cb, cb + 6):
        if target_boards == cb:
            # 当前已是Cb板，终点就是现在
            # 估算"在此高度断板"的概率
            success_prob = min(COND_PROBS.get(cb, 0.1) * adj, 0.95)
            fail_prob = 1 - success_prob
            # 断板后不会再续，所以"最终就是cb板"的概率
            probs[cb] = fail_prob + probs.get(cb, 0)
        else:
            # 连续突破到target_boards的概率
            p = 1.0
            for b in range(cb, target_boards):
                base_prob = COND_PROBS.get(b, 0.05)
                p *= min(base_prob * adj, 0.95)
            probs[target_boards] = p
    
    # 归一化
    total = sum(probs.values())
    if total > 0:
        probs = {k: v/total for k, v in probs.items()}
    
    # 期望值
    expected = sum(k * v for k, v in probs.items())
    
    # 最可能的高度
    max_prob_board = max(probs, key=probs.get)
    
    # 置信度
    max_prob = probs.get(max_prob_board, 0)
    if max_prob >= 0.6 and len(probs) <= 3:
        confidence = '高'
    elif max_prob >= 0.4:
        confidence = '中'
    else:
        confidence = '低（多空分歧）'
    
    # 信号判断
    if max_prob_board >= cb + 3:
        signal = f'🚀 有望冲击{max_prob_board}板'
    elif max_prob_board == cb:
        signal = f'⚠️ 可能在{cb}板结束'
    elif probs.get(cb+1, 0) > 0.4:
        signal = f'➡️ 大概率续涨至{cb+1}板'
    else:
        signal = f'➡️ 有望到{cb+1}板'
    
    return {
        'code': code,
        'name': kl.get('__name__', ''),
        'current_boards': cb,
        'expected_boards': round(expected, 1),
        'max_prob_board': max_prob_board,
        'prob_reach_N': {k: round(v*100, 1) for k, v in sorted(probs.items())},
        'confidence': confidence,
        'factors': factors,
        'signal': signal,
        'trend': trend,
        'rsi': round(rsi_val, 1) if rsi_val else None,
        'vol_ratio': round(vol_ratio, 2),
        'macd': '多头' if (dif and dif > 0) else '空头',
    }


def batch_predict_height(stocks):
    """
    stocks: [{code, name, lb_count, ...}]
    对涨停股列表做高度预测
    """
    results = []
    for s in stocks:
        code = s['code']
        current = s.get('lb_count', 1)
        kl = get_kline(code)
        if not kl or len(kl) < 10:
            continue
        kl['__name__'] = s.get('name', '')
        
        pred = predict_height(code, current, kl)
        if pred:
            pred['lb_count'] = current
            pred['industry'] = s.get('industry', '')
            results.append(pred)
    
    # 按期望高度排序
    results.sort(key=lambda x: x['expected_boards'], reverse=True)
    return results


def generate_height_report(predictions):
    """生成高度预测报告"""
    today = datetime.date.today().strftime("%Y%m%d")
    lines = [f"{'='*60}"]
    lines.append(f"A股连板高度预测  {today}")
    lines.append(f"{'='*60}")
    
    # 按身位分组
    by_pos = defaultdict(list)
    for p in predictions:
        by_pos[p['current_boards']].append(p)
    
    for boards in sorted(by_pos.keys()):
        recs = by_pos[boards]
        lines.append(f"\n{'━'*50}")
        lines.append(f"【{boards}板股】{len(recs)}只")
        lines.append(f"{'━'*50}")
        for r in recs:
            probs_str = ' '.join([f"{k}板={v}%" for k,v in r['prob_reach_N'].items()])
            lines.append(f"\n  {r['code']} {r['name']}")
            lines.append(f"    当前:{boards}板 → 期望:{r['expected_boards']}板 | 最可能:{r['max_prob_board']}板 | 置信:{r['confidence']}")
            lines.append(f"    概率分布: {probs_str}")
            lines.append(f"    {r['signal']} | 趋势:{r['trend']} RSI:{r['rsi']} 量比:{r['vol_ratio']} MACD:{r['macd']}")
            if r['factors']:
                lines.append(f"    调整因子: {' | '.join(r['factors'])}")
    
    lines.append(f"\n{'='*60}")
    lines.append(f"解读：期望高度=概率加权的均值，最可能板数=概率最高的那个")
    lines.append(f"{'='*60}")
    
    return '\n'.join(lines)


# ========================
# 主程序：演示
# ========================
if __name__ == '__main__':
    import sys
    
    # 读昨日涨停股（3月20日）
    fpath = f"{DATA_DIR}/20260320.json"
    if not os.path.exists(fpath):
        print("请先运行 astock_daily_v2.py 获取数据")
        sys.exit(1)
    
    with open(fpath) as f:
        stocks = json.load(f)
    
    print(f"分析 {len(stocks)} 只涨停股的高度预测...\n")
    
    results = batch_predict_height(stocks)
    report = generate_height_report(results)
    print(report)
    
    # 保存
    out = f"{DATA_DIR}/height_predictions_{datetime.date.today().strftime('%Y%m%d')}.json"
    with open(out, 'w') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n已保存: {out}")
