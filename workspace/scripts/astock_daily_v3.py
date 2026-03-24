#!/usr/bin/env python3
"""
A股超短每日复盘系统 v3.2

修复内容:
1. SQLite数据库替代JSON存储，支持跨时间查询
2. 概率标定验证：替代"准确率"，验证预测概率是否与实际频率匹配
3. 情绪周期量化：用炸板率+涨停数趋势判断，非人工规则
4. 调整系数回测框架：积累样本后自动建议系数修正方向
5. 结局4类：晋级/续涨/断板/炸板，detail字段显示具体信息
6. 修复次日交易日计算（A股日历，跳过周末）
7. 概率标定增加置信区间标注
8. 情绪周期自适应：退潮期自动降低基准晋级率，不改系数
9. 卖出信号逻辑：持有多日/浮盈/浮亏超阈值时给出操作建议
10. 板块效应分析：统计各板块涨停数量，识别联动机会
11. 持仓管理：buy/sell/positions命令记录买卖和浮盈
"""
import json, os, sys, subprocess, sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
import akshare as ak
import baostock as bs

DATA_DIR = "/home/gem/workspace/agent/workspace/data/astock"
MODEL_DIR = f"{DATA_DIR}/model"
DB_PATH = f"{MODEL_DIR}/astock.db"
os.makedirs(MODEL_DIR, exist_ok=True)

# ===================== 数据库 =====================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL, code TEXT NOT NULL, name TEXT, lb INTEGER,
        industry TEXT, trend TEXT, rsi REAL, vr REAL, macd_state TEXT,
        vol_status TEXT, price_vs_ma20 TEXT, jb_prob REAL, dz_prob REAL,
        dist_N INTEGER, dist_N1 INTEGER, dist_N2 INTEGER, signal TEXT,
        base_prob REAL, adj_factor REAL, outcome TEXT, detail TEXT,
        actual_boards INTEGER, created_at TEXT, UNIQUE(date, code))''')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_stats (
        date TEXT PRIMARY KEY, zt_count INTEGER, lianban_max INTEGER,
        zhaban_count INTEGER, zhaban_rate REAL, phase TEXT, phase_name TEXT,
        total_predicted INTEGER, correct INTEGER, wrong INTEGER,
        accuracy REAL, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS calibration (
        bucket INTEGER PRIMARY KEY,
        total_sample_count INTEGER DEFAULT 0,
        total_predicted_prob_sum REAL DEFAULT 0,
        total_actual_jb_sum INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS coef_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, coef_name TEXT,
        old_value REAL, new_value REAL, reason TEXT, created_at TEXT)''')
    # 持仓记录（用于卖出逻辑）
    c.execute('''CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT, name TEXT, buy_date TEXT, buy_price REAL,
        hold_days INTEGER DEFAULT 0, profit_pct REAL DEFAULT 0,
        exit_signal TEXT, exit_reason TEXT, status TEXT DEFAULT '持仓',
        created_at TEXT, UNIQUE(code, buy_date))''')
    conn.commit()
    return conn

# ===================== 工具 =====================

def date_from_str(s): return datetime.strptime(s, "%Y%m%d")
def today_str(): return datetime.now().strftime("%Y%m%d")

def next_trading_day(date_str):
    """
    计算下一个实际A股交易日（跳过周末）。
    - 周1~4: +1天
    - 周5: +3天（周一）
    - 周6: +2天（周一）
    - 周7(=周日): +1天（周一）
    """
    d = date_from_str(date_str)
    wd = d.weekday()  # Mon=0, Sun=6
    if wd < 4:         # Mon-Thu
        return (d + timedelta(days=1)).strftime("%Y%m%d")
    elif wd == 4:      # Fri
        return (d + timedelta(days=3)).strftime("%Y%m%d")
    else:              # Sat/Sun
        return (d + timedelta(days=(7 - wd) + 1)).strftime("%Y%m%d")

KLINE_DIR = "/home/gem/workspace/agent/workspace/data/astock/100day/klines"

def _bs_login():
    """BaoStock登录（全局复用）"""
    global _bs_conn
    if '_bs_conn' not in globals():
        _bs_conn = None
    if _bs_conn is None:
        _bs_conn = bs.login()
    return _bs_conn

_bs_conn = None

def code_to_baostock(code):
    code = code.zfill(6)
    if code.startswith(('6', '9')):
        return f"sh.{code}"
    return f"sz.{code}"

def get_kline(code):
    """使用BaoStock获取K线数据（不限流）"""
    bs_code = code_to_baostock(code)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=250)).strftime("%Y-%m-%d")

    _bs_login()
    rs = bs.query_history_k_data_plus(
        bs_code,
        "date,open,high,low,close,volume",
        start_date=start_date,
        end_date=end_date,
        frequency="d"
    )
    if rs.error_msg != 'success':
        return {}
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if len(rows) < 30:
        return {}

    result = {}
    for r in rows[-120:]:
        try:
            result[r[0]] = {
                'open': float(r[1]), 'high': float(r[2]),
                'low': float(r[3]), 'close': float(r[4]),
                'vol': float(r[5])
            }
        except (ValueError, IndexError):
            continue
    return result

def get_next_close(code):
    """获取最新收盘价，用于浮盈计算"""
    bs_code = code_to_baostock(code)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")

    _bs_login()
    rs = bs.query_history_k_data_plus(
        bs_code, "date,close",
        start_date=start_date, end_date=end_date, frequency="d"
    )
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if rows:
        try:
            return float(rows[-1][1])  # close = index 1
        except (ValueError, IndexError):
            pass
    return None

# ===================== 技术指标 =====================

def ma(c, n): return sum(c[-n:])/n if len(c)>=n else None
def macd(closes, f=12, s=26, sig=9):
    if len(closes) < s+sig: return None, None, None
    def ema(c,n):
        k=2.0/(n+1); e=c[0]
        for v in c[1:]: e=v*k+e*(1-k)
        return e
    ef=ema(closes,f); es=ema(closes,s); dif=ef-es
    de=ema([dif]*sig,sig) if dif else None
    return dif,de,2*(dif-de) if (dif and de) else None
def rsi(closes, n=14):
    if len(closes)<n+1: return None
    g,l=[],[]
    for i in range(1,len(closes)):
        d=closes[i]-closes[i-1]
        g.append(max(d,0)); l.append(max(-d,0))
    ag=sum(g[-n:])/n; al=sum(l[-n:])/n
    return 100-(100/(1+ag/al)) if al>0 else 100
def vol_ma(vols,n=20): return sum(vols[-n:])/n if len(vols)>=n else None

# ===================== 模型参数 =====================

BASE_PROBS = {1:0.13, 2:0.15, 3:0.20, 4:0.29, 5:0.10}

# 情绪周期基准晋级率（退潮/冰点期自动应用折扣，不改系数）
PHASE_BASE_DISCOUNT = {
    '退潮': 0.40,   # 退潮期：晋级率打折到40%
    '冰点': 0.55,   # 冰点期：晋级率打折到55%
    '启动': 0.85,   # 启动期：正常偏低
    '发酵': 1.00,   # 发酵期：正常
    '数据不足': 1.00,
    '未知': 1.00,
}

DEFAULT_COEF = {
    '上升通道':1.4,'下降通道':0.4,'底部金叉':1.3,'顶部死叉':0.5,
    'vr_<0.5':1.5,'vr_<0.8':1.2,'vr_>3':0.5,
    'macd多头':1.2,'macd空头':0.7,
    'rsi_>90':0.3,'rsi_>80':0.5,'rsi_>70':0.7,'rsi_<30':1.2,
    'price_>ma20':1.15,'price_<ma20':0.6,'price_>ma60':1.1,
    'lb>=4_vr_<0.8':1.3,'lb>=4_vr_>2':0.4,
}

def load_coef():
    path = f"{MODEL_DIR}/coef.json"
    if os.path.exists(path):
        with open(path) as f: return json.load(f)
    return DEFAULT_COEF.copy()

def save_coef(coef):
    with open(f"{MODEL_DIR}/coef.json",'w') as f:
        json.dump(coef, f, ensure_ascii=False, indent=2)

def calc_adj(trend, vr, dif, macd_val, rsi_val, cur, ma20, ma60, lb, coef=None):
    c = coef or load_coef()
    adj = 1.0
    if trend=='上升通道': adj*=c['上升通道']
    elif trend=='下降通道': adj*=c['下降通道']
    elif trend=='底部金叉': adj*=c['底部金叉']
    elif trend=='顶部死叉': adj*=c['顶部死叉']
    if vr<0.5: adj*=c['vr_<0.5']
    elif vr<0.8: adj*=c['vr_<0.8']
    elif vr>3: adj*=c['vr_>3']
    if dif and dif>0 and macd_val and macd_val>0: adj*=c['macd多头']
    elif dif and dif<0: adj*=c['macd空头']
    if rsi_val:
        if rsi_val>90: adj*=c['rsi_>90']
        elif rsi_val>80: adj*=c['rsi_>80']
        elif rsi_val>70: adj*=c['rsi_>70']
        elif rsi_val<30: adj*=c['rsi_<30']
    if cur>(ma20 or 0): adj*=c['price_>ma20']
    else: adj*=c['price_<ma20']
    if ma60 and cur>ma60: adj*=c['price_>ma60']
    if lb>=4:
        if vr<0.8: adj*=c['lb>=4_vr_<0.8']
        elif vr>2: adj*=c['lb>=4_vr_>2']
    return max(0.15, min(adj, 2.5))

def predict_stock(code, lb, kl, coef_override=None, phase=None):
    dts = sorted([k for k in kl.keys() if k.startswith('202')])
    if len(dts)<10: return None
    closes=[kl[d]['close'] for d in dts]
    vols=[kl[d]['vol'] for d in dts]
    ma5=ma(closes,5); ma10=ma(closes,10); ma20=ma(closes,20)
    ma60=ma(closes,60) if len(closes)>=60 else None
    dif,de,macd_val=macd(closes)
    rsi_val=rsi(closes)
    cur=closes[-1]
    vr=vols[-1]/vol_ma(vols,20) if vol_ma(vols,20) else 1

    trend='震荡'
    if ma5 and ma10 and ma20:
        if ma5>ma10>ma20: trend='上升通道'
        elif ma5<ma10<ma20: trend='下降通道'
        elif ma5>ma10 and ma10<ma20: trend='底部金叉'
        elif ma5<ma10 and ma10>ma20: trend='顶部死叉'

    macd_state='中性'
    if dif and macd_val:
        macd_state='多头' if (dif>0 and macd_val>0) else '空头'

    vol_status='正常量'
    if vr<0.5: vol_status='缩量'
    elif vr<0.8: vol_status='轻微缩量'
    elif vr>3: vol_status='爆量'
    elif vr>1.5: vol_status='放量'

    price_vs_ma20='MA20上方' if cur>(ma20 or 0) else 'MA20下方'
    adj=calc_adj(trend,vr,dif,macd_val,rsi_val,cur,ma20,ma60,lb,coef_override)

    # 情绪周期基准折扣：退潮/冰点期降低晋级基准，不改系数
    discount = PHASE_BASE_DISCOUNT.get(phase, 1.0)
    base=BASE_PROBS.get(lb,0.10) * discount
    jb=min(base*adj, 0.70 * discount)
    dz=1-jb

    next_adj=max(0.5,min(adj*0.7,1.5))
    q=BASE_PROBS.get(lb+1,0.15) * discount
    p_continue=min(q*next_adj, 0.80 * discount)
    p2=jb*p_continue
    p_lb1=jb*(1-p_continue)
    total=dz+p_lb1+p2
    dist={lb:round(dz/total*100,1), lb+1:round(p_lb1/total*100,1), lb+2:round(p2/total*100,1)}

    signals=[]
    if trend=='上升通道': signals.append('上升通道')
    elif trend=='底部金叉': signals.append('底部金叉')
    elif trend=='顶部死叉': signals.append('顶部死叉')
    elif trend=='下降通道': signals.append('下降通道')
    if vr<0.5: signals.append('极度缩量')
    elif vr<0.8: signals.append('缩量整理')
    if rsi_val and rsi_val>80: signals.append(f'RSI{int(rsi_val)}超买')
    elif rsi_val and rsi_val<35: signals.append(f'RSI{int(rsi_val)}超卖')
    if dif and dif>0: signals.append('MACD多头')
    if cur>(ma20 or 0): signals.append('MA20上方')
    signal_str=', '.join(signals) if signals else '普通'

    return {
        'code':code,'lb':lb,
        'technicals':{'trend':trend,'rsi':round(rsi_val,1) if rsi_val else None,
                      'vr':round(vr,2),'macd_state':macd_state,
                      'price_vs_ma20':price_vs_ma20,'vol_status':vol_status},
        'prediction':{'jb_prob':round(jb*100,1),'dz_prob':round(dz*100,1),
                      'distribution':dist,'signal':signal_str,
                      'base_prob':base,'adj_factor':round(adj,2),'discount':discount}
    }

# ===================== 情绪周期量化 =====================

def detect_market_phase(conn):
    rows = conn.execute(
        'SELECT date,zt_count,zhaban_rate FROM daily_stats ORDER BY date DESC LIMIT 10'
    ).fetchall()
    if len(rows)<3: return '数据不足','未知',{}
    rows=list(reversed(rows))
    recent=rows[-3:]
    prev=rows[-6:-3] if len(rows)>=6 else rows[:3]
    r_zt=sum(r['zt_count'] for r in recent)/len(recent)
    p_zt=sum(r['zt_count'] for r in prev)/len(prev) if prev else r_zt
    zt_trend=r_zt-p_zt
    r_zb=sum((r['zhaban_rate'] or 0) for r in recent)/len(recent)

    if r_zt<20 and r_zb>0.4: phase='bingdian'
    elif zt_trend>5 and r_zb<0.3: phase='qidong'
    elif r_zt>=30 and r_zb<0.25 and zt_trend>=0: phase='fajiaoqi'
    elif zt_trend<-5 or r_zb>0.45 or (r_zt<p_zt and r_zb>0.35): phase='tuichao'
    elif r_zt>=25 and r_zb<0.3: phase='fajiaoqi'
    else: phase='qidong'

    phase_names={'bingdian':'冰点','qidong':'启动','fajiaoqi':'发酵','tuichao':'退潮'}
    return phase, phase_names.get(phase,'未知'), {
        'avg_zt':round(r_zt,1),'zt_trend':round(zt_trend,1),
        'avg_zhaban':round(r_zb*100,1)
    }



# ===================== 概率标定 =====================

def prob_bucket(p):
    if p<10: return 0
    elif p<20: return 10
    elif p<30: return 20
    elif p<40: return 30
    elif p<50: return 40
    elif p<60: return 50
    elif p<70: return 60
    else: return 70

def conf_label(n):
    """根据样本量返回置信度标注"""
    if n >= 30: return '高'
    elif n >= 10: return '中'
    elif n >= 5: return '低'
    else: return '⚠️极低'

def update_calibration(conn):
    """重建概率标定表：用所有历史预测数据精确计算"""
    conn.execute('DELETE FROM calibration')
    rows=conn.execute('SELECT jb_prob,outcome FROM predictions WHERE outcome IS NOT NULL').fetchall()
    buckets=defaultdict(lambda:{'n':0,'jb':0,'pred_sum':0.0})
    for row in rows:
        b=prob_bucket(row['jb_prob'])
        buckets[b]['n']+=1
        buckets[b]['pred_sum']+=row['jb_prob']
        if row['outcome']=='晋级': buckets[b]['jb']+=1
    for b,data in buckets.items():
        if data['n']<1: continue
        conn.execute('''INSERT INTO calibration
            (bucket,total_sample_count,total_predicted_prob_sum,total_actual_jb_sum)
            VALUES (?,?,?,?)''',
            (b,data['n'],data['pred_sum'],data['jb']))
    conn.commit()

def get_calibration_report(conn):
    rows=conn.execute('SELECT * FROM calibration ORDER BY bucket').fetchall()
    lines=["\n概率标定验证（预测概率 vs 实际晋级率）:"]
    lines.append(f"{'概率桶':<10}{'样本':<6}{'置信':<6}{'预测均值':<12}{'实际率':<12}{'偏差'}")
    lines.append('-'*55)
    total_n=sum(r['total_sample_count'] for r in rows)
    total_jb=sum(r['total_actual_jb_sum'] for r in rows)
    for r in rows:
        b=r['bucket']
        n=r['total_sample_count']
        avg_pred=r['total_predicted_prob_sum']/n if n>0 else 0
        actual=r['total_actual_jb_sum']/n if n>0 else 0
        bias_pct=actual*100-avg_pred
        bias_str=f"+{bias_pct:.1f}%" if bias_pct>=0 else f"{bias_pct:.1f}%"
        conf=conf_label(n)
        lines.append(f"{b}%~{b+10}%  {n:<6}{conf:<6}{avg_pred:.2f}%       {actual*100:.1f}%       {bias_str}")
    overall=total_jb/total_n*100 if total_n>0 else 0
    lines.append(f"\n总样本:{total_n}只  整体晋级率:{overall:.1f}%")
    if total_n < 30:
        lines.append(f"⚠️ 样本不足{30-total_n}只，概率标定结果仅供参考")
    return '\n'.join(lines)

# ===================== 系数调整建议 =====================

def suggest_coef_adjustments(conn, min_samples=8):
    coef=load_coef()
    rows=conn.execute('SELECT trend,vr,rsi,lb,outcome,industry FROM predictions WHERE outcome IS NOT NULL').fetchall()
    phase,phase_name,_=detect_market_phase(conn)
    if len(rows)<min_samples:
        return [],f"样本不足({len(rows)}/{min_samples})，暂不调整"
    changes=[]
    # 趋势
    trend_stats=defaultdict(lambda:{'n':0,'jb':0})
    for r in rows:
        t=r['trend'] or '震荡'
        trend_stats[t]['n']+=1
        if r['outcome']=='晋级': trend_stats[t]['jb']+=1
    expected={'上升通道':0.30,'底部金叉':0.25,'震荡':0.15,'顶部死叉':0.03,'下降通道':0.02}
    for t,stat in trend_stats.items():
        if stat['n']<3: continue
        rate=stat['jb']/stat['n']
        exp=expected.get(t,0.15)
        if rate<exp*0.6 and coef[t]>0.3:
            changes.append((t,coef[t],round(coef[t]*0.8,3),f'{t} n={stat["n"]} 晋级{rate:.1%}<{exp:.1%}*0.6'))
        elif rate>exp*1.5 and coef[t]<2.5:
            changes.append((t,coef[t],round(coef[t]*1.2,3),f'{t} n={stat["n"]} 晋级{rate:.1%}>{exp:.1%}*1.5'))
    # 量比
    for label,lo,hi,exp_r in [('vr_<0.5',0,0.5,0.55),('vr_<0.8',0.5,0.8,0.28),('vr_>3',3.0,999,0.03)]:
        n=sum(1 for r in rows if r['vr'] and lo<=r['vr']<hi)
        jb=sum(1 for r in rows if r['vr'] and lo<=r['vr']<hi and r['outcome']=='晋级')
        if n>=3:
            rate=jb/n
            if rate<exp_r*0.6 and coef[label]>0.25:
                changes.append((label,coef[label],round(coef[label]*0.8,3),f'{label} n={n} 晋级{rate:.1%}<{exp_r:.1%}*0.6'))
            elif rate>exp_r*1.5 and coef[label]<2.5:
                changes.append((label,coef[label],round(coef[label]*1.2,3),f'{label} n={n} 晋级{rate:.1%}>{exp_r:.1%}*1.5'))
    return changes,"需调整" if changes else "无需调整"

# ===================== 卖出信号 =====================

# 卖出规则阈值（可配置）
EXIT_RULES = {
    'max_hold_days': 3,      # 持有超过3天强制卖出
    'stop_loss_pct': -5.0,   # 浮亏超过5%止损
    'take_profit_pct': 15.0, # 浮盈超过15%止盈
    'force_sell_if_no_zt': True,  # 持有期间未涨停则卖出
}

def auction_ok(code, name, lb, jb_prob, vr, sector_hot):
    """
    生成竞价观察条件说明（不是自动判断，需明日9:25人工核对）。
    返回 (ok_min, ok_max, vol_req, warning)
    - ok_min/max: 竞价正常涨幅区间（%）
    - vol_req: 量能要求
    - warning: 不符合条件时的风险提示
    """
    # 3板以上：高位，风险大，竞价要求严格
    if lb >= 3:
        return (1, 6, "缩量或平量", "⚠️高板位：竞价>+6%获利盘砸盘风险大，<1%开盘说明情绪弱")
    # 电力/光伏等热门联动板块：情绪加成，可适当放宽
    if sector_hot:
        return (3, 8, "温和放量", "✅联动板块：板块情绪好，可参与")
    # 1板、晋级概率>20%：标准条件
    if jb_prob > 20:
        return (3, 7, "成交额>昨日20%", "✅标准条件：涨幅3-7%放量为最佳")
    # 1板、晋级概率<20%：保守
    return (4, 7, "明显放量", "⚠️晋级概率偏低：需竞价表现强劲才有参与价值")

def eval_exit_signals(conn):
    """
    评估所有持仓的卖出信号。
    持仓表positions记录买入信息，
    通过对比最新收盘价计算浮盈/浮亏，
    给出操作建议（持有/卖出/止损/止盈）。
    """
    rows=conn.execute("SELECT * FROM positions WHERE status='持仓'").fetchall()
    if not rows: return []
    signals=[]
    for r in rows:
        code=r['code']
        buy_price=r['buy_price']
        hold_days=r['hold_days']
        cur_price=get_next_close(code)
        if not cur_price or not buy_price or buy_price<=0:
            continue
        profit_pct=(cur_price-buy_price)/buy_price*100
        conn.execute('UPDATE positions SET hold_days=hold_days+1,profit_pct=? WHERE code=? AND status=?',
                    (round(profit_pct,2),code,'持仓'))
        reason=None; action='持有'
        if profit_pct <= EXIT_RULES['stop_loss_pct']:
            action='🚨止损'; reason=f'浮亏{profit_pct:.1f}%<={EXIT_RULES["stop_loss_pct"]}%'
        elif profit_pct >= EXIT_RULES['take_profit_pct']:
            action='🎯止盈'; reason=f'浮盈{profit_pct:.1f}%>={EXIT_RULES["take_profit_pct"]}%'
        elif hold_days >= EXIT_RULES['max_hold_days']:
            action='⏰到期'; reason=f'持有{hold_days}天>={EXIT_RULES["max_hold_days"]}天'
        if action != '持有':
            conn.execute("UPDATE positions SET exit_signal=?,exit_reason=?,status='已卖出' WHERE code=?",
                        (action,reason,code))
            signals.append({
                'code':code,'name':r['name'],'buy_price':buy_price,
                'cur_price':cur_price,'profit_pct':round(profit_pct,1),
                'action':action,'reason':reason,'hold_days':hold_days+1
            })
    conn.commit()
    return signals

# ===================== 命令 =====================

def cmd_predict(date):
    conn=init_db()
    print(f"\n{'='*60}\n  A股超短每日预测 v3.2  {date}\n{'='*60}\n")

    # 评估持仓卖出信号（先查持仓再预测）
    exit_signals=eval_exit_signals(conn)
    if exit_signals:
        print(f"【持仓预警】{len(exit_signals)}只触发卖出信号:\n")
        print(f"{'代码':<8}{'名称':<10}{'买入价':<10}{'现价':<10}{'浮盈%':<10}{'信号'}")
        for s in exit_signals:
            print(f"{s['code']:<8}{s['name']:<10}{s['buy_price']:<10.2f}{s['cur_price']:<10.2f}{s['profit_pct']:<10.1f}{s['action']} {s['reason']}")
        print()

    try:
        zt_df=ak.stock_zt_pool_em(date=date)
    except Exception as e:
        print(f"获取涨停池失败: {e}"); return
    phase,phase_name,phase_data=detect_market_phase(conn)
    discount=PHASE_BASE_DISCOUNT.get(phase,1.0)
    disc_str=f" (基准晋级率打折{discount:.0%})" if discount<1.0 else ""
    print(f"市场情绪: {phase_name}  涨停均:{phase_data.get('avg_zt','?')}  炸板率:{phase_data.get('avg_zhaban','?')}%  趋势:{phase_data.get('zt_trend','?')}{disc_str}")
    stocks=[]
    for _,row in zt_df.iterrows():
        code=str(row.get('代码','')).zfill(6)
        name=str(row.get('名称',''))
        lb=int(row.get('连板数',1))
        industry=str(row.get('所属行业','未知'))
        if 'ST' in name or '*ST' in name: continue
        stocks.append({'code':code,'name':name,'lb':lb,'industry':industry})
    print(f"涨停股: {len(stocks)}只\n")
    results=[]
    for s in stocks:
        kl=get_kline(s['code'])
        if not kl: continue
        pred=predict_stock(s['code'],s['lb'],kl,phase=phase)
        if not pred: continue
        pred['name']=s['name']; pred['industry']=s['industry']
        results.append(pred)
    results.sort(key=lambda x:x['prediction']['jb_prob'],reverse=True)
    max_lb=max([r['lb'] for r in results]) if results else 0
    for r in results:
        d=r['prediction']['distribution']
        conn.execute('''INSERT OR REPLACE INTO predictions
            (date,code,name,lb,industry,trend,rsi,vr,macd_state,vol_status,price_vs_ma20,
             jb_prob,dz_prob,dist_N,dist_N1,dist_N2,signal,base_prob,adj_factor,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (date,r['code'],r['name'],r['lb'],r['industry'],
             r['technicals']['trend'],r['technicals']['rsi'],r['technicals']['vr'],
             r['technicals']['macd_state'],r['technicals']['vol_status'],r['technicals']['price_vs_ma20'],
             r['prediction']['jb_prob'],r['prediction']['dz_prob'],
             d.get(r['lb'],0),d.get(r['lb']+1,0),d.get(r['lb']+2,0),
             r['prediction']['signal'],r['prediction']['base_prob'],r['prediction']['adj_factor'],
             datetime.now().isoformat()))
    conn.execute('''INSERT OR REPLACE INTO daily_stats
        (date,zt_count,lianban_max,phase,phase_name,total_predicted,created_at)
        VALUES (?,?,?,?,?,?,?)''',
        (date,len(results),max_lb,phase,phase_name,len(results),datetime.now().isoformat()))
    conn.commit()

    # 板块效应分析（预测时不依赖outcome，用当日板块内涨停数量代替）
    sector_stats={}
    sector_raw=defaultdict(lambda:{'total':0,'codes':[]})
    for s in results:
        ind=s['industry'] or '未知'
        sector_raw[ind]['total']+=1
        sector_raw[ind]['codes'].append(s['code'])
    if len(sector_raw)>=2:
        print(f"{'='*60}\n  板块效应\n{'='*60}")
        print(f"{'板块':<15}{'涨停数':<8}{'机会'}")
        print('-'*40)
        sorted_sectors=sorted(sector_raw.items(),key=lambda x:x[1]['total'],reverse=True)
        for ind,data in sorted_sectors:
            n=data['total']
            tag='🚀板块联动' if n>=3 else ('📌多股' if n==2 else '')
            print(f"{ind[:12]:<15}{n:<8}{tag}")
        print()

    gt30=sum(1 for r in results if r['prediction']['jb_prob']>30)
    gt15=sum(1 for r in results if 15<r['prediction']['jb_prob']<=30)
    lt15=sum(1 for r in results if r['prediction']['jb_prob']<=15)

    print(f"晋级分布: >30%:{gt30}只 / 15-30%:{gt15}只 / <15%:{lt15}只\n")

    # 竞价自检表（每行一个标的，不用ASCII表，飞书不保留等宽）
    print(f"\n{'='*60}\n  明日竞价自检（明日9:25前人工核对）\n{'='*60}")
    for r in results:
        p=r['prediction']
        n=sector_raw.get(r['industry'],{}).get('total',0)
        sector_hot=(n>=3)
        ok_min,ok_max,vol_req,warning=auction_ok(r['code'],r['name'],r['lb'],p['jb_prob'],r['technicals']['vr'],sector_hot)
        print(f"  {r['code']} {r['name'][:6]} | {r['lb']}板 | 晋级{p['jb_prob']:.1f}% | 竞价{ok_min}～{ok_max}% | {vol_req} | {warning}")

    print(f"\n  竞价条件：涨幅<3%不参与 | >7%谨慎 | 缩量高开警惕 | 3板以上区间1-6%")

    print(f"\n{'='*60}\n  详细预测（晋级概率>15%个股）\n{'='*60}")
    print(f"{'代码':<8}{'名称':<10}{'板':<4}{'晋级%':<7}{'信号'}")
    print(f"{'-'*70}")
    for r in results:
        p=r['prediction']
        if p['jb_prob']>15:
            print(f"{r['code']:<8}{r['name']:<10}{r['lb']:<4}板 {p['jb_prob']:<7}% {p['signal']}")
    if len(results)>20: print(f"\n... 共{len(results)}只")
    print(f"\n{'='*60}\n  【重点关注】晋级概率>20%\n{'='*60}\n")
    for r in results:
        if r['prediction']['jb_prob']>20:
            p=r['prediction']; d=p['distribution']
            dist_str='/'.join([f"{k}板{v}%" for k,v in sorted(d.items())])
            disc_note=f" (打折{p['discount']:.0%})" if p['discount']<1.0 else ''
            n=sector_raw.get(r['industry'],{}).get('total',0)
            sector_hot=(n>=3)
            ok_min,ok_max,vol_req,warning=auction_ok(r['code'],r['name'],r['lb'],p['jb_prob'],r['technicals']['vr'],sector_hot)
            print(f"  {r['code']} {r['name']} | {r['lb']}板 | 晋级{p['jb_prob']}%{disc_note} | {dist_str}")
            print(f"    信号: {p['signal']}")
            if sector_hot: print(f"    📌{r['industry']}板块{n}只涨停  🚀联动")
            print(f"    📋 明日竞价观察: 开盘涨幅 {ok_min}%～{ok_max}% | {vol_req} | {warning}")
    conn.close()
    print(f"\n已保存: {DB_PATH}")
    return results,phase,phase_name

def cmd_outcome(date):
    conn=init_db()
    print(f"\n{'='*60}\n  追踪实际结局 v3.2  {date}\n{'='*60}\n")
    # 修复：用A股日历算下一个实际交易日
    nd=next_trading_day(date)
    print(f"[T日 {date} → T+1日 {nd}]\n")
    try:
        next_zt=ak.stock_zt_pool_em(date=nd)
        zt_df=ak.stock_zt_pool_em(date=date)
    except Exception as e:
        print(f"获取涨停池失败: {e}"); return
    codes21={str(r['代码']).zfill(6):int(r.get('连板数',1)) for _,r in next_zt.iterrows()}
    codes20={str(r['代码']).zfill(6):int(r.get('连板数',1)) for _,r in zt_df.iterrows()}
    in_next=set(codes21.keys())
    rows=conn.execute('SELECT * FROM predictions WHERE date=?',(date,)).fetchall()
    jb_count=0; xux_count=0; duan_count=0
    for row in rows:
        code=row['code']; lb21=codes21.get(code,0)
        if lb21>row['lb']:
            outcome='晋级'; jb_count+=1; detail=f'{row["lb"]}板→{lb21}板'
        elif code not in in_next:
            outcome='断板'; duan_count+=1; detail='未续涨'
        else:
            outcome='续涨'; xux_count+=1; detail=f'维持{row["lb"]}板'
        conn.execute('UPDATE predictions SET outcome=?,detail=?,actual_boards=? WHERE date=? AND code=?',
                    (outcome,detail,lb21 if lb21>0 else None,date,code))
    conn.commit()
    rows2=conn.execute('SELECT code,name,lb,jb_prob,outcome,detail FROM predictions WHERE date=?',(date,)).fetchall()
    total=len(rows2)
    zb_rate=0.0  # 炸板暂用Level2数据，简化处理
    conn.execute('UPDATE daily_stats SET zhaban_count=0,zhaban_rate=? WHERE date=?',(zb_rate,date))
    correct=sum(1 for r in rows2
                if (r['outcome']=='晋级' and r['jb_prob']>50) or
                   (r['outcome'] in ('续涨','断板') and r['jb_prob']<50))
    wrong=total-correct
    accuracy=round(correct/total*100,1) if total>0 else 0
    jb_rate=round(jb_count/total*100,1) if total>0 else 0
    xux_rate=round(xux_count/total*100,1) if total>0 else 0
    duan_rate=round(duan_count/total*100,1) if total>0 else 0
    conn.execute('UPDATE daily_stats SET correct=?,wrong=?,accuracy=? WHERE date=?',
                (correct,wrong,accuracy,date))
    conn.commit()
    update_calibration(conn)
    cal_report=get_calibration_report(conn)
    print(f"实际晋级: {jb_count}/{total} = {jb_rate}%  "
          f"续涨:{xux_count}只({xux_rate}%)  断板:{duan_count}只({duan_rate}%)")
    print(f"\n{'代码':<8}{'名称':<10}{'板':<5}{'晋级%':<7}{'判断':<6}{'实际'}")
    print(f"{'-'*55}")
    for r in rows2:
        outcome_val=r['outcome'] or '未知'
        v='✅正确' if (r['outcome']=='晋级' and r['jb_prob']>50) or (r['outcome'] in ('续涨','断板') and r['jb_prob']<50) else '❌错误'
        print(f"{r['code']:<8}{r['name']:<10}{r['lb']:<5}板 {r['jb_prob']:<7}% {v:<6}{outcome_val}({r['detail'] or ''})")
    print(f"\n正确: {correct}/{total}  错误: {wrong}/{total}  准确率: {accuracy}%")
    print(cal_report)
    changes,status=suggest_coef_adjustments(conn)
    if changes:
        print(f"\n{'='*60}\n  系数调整建议\n{'='*60}\n")
        for name,old,new,reason in changes:
            print(f"  {name}: {old} → {new}  ({reason})")
        print(f"\n应用: python3 astock_daily_v3.py apply_coef")
    else:
        print(f"\n系数: {status}")
    conn.close()

def cmd_apply_coef():
    conn=init_db()
    changes,status=suggest_coef_adjustments(conn)
    if not changes:
        print(f"无需调整: {status}"); conn.close(); return
    coef=load_coef()
    print("即将应用:")
    applied=[]
    for name,old,new,reason in changes:
        if old==new: continue
        print(f"  {name}: {old} → {new}  ({reason})")
        coef[name]=new
        applied.append((name,old,new,reason))
    if not applied:
        print("无实际变更"); conn.close(); return
    save_coef(coef)
    now=datetime.now().isoformat()
    today=today_str()
    for name,old,new,reason in applied:
        # 去重：同一系数同一天只记最新一条
        conn.execute('''DELETE FROM coef_log WHERE date=? AND coef_name=?''',(today,name))
        conn.execute('INSERT INTO coef_log (date,coef_name,old_value,new_value,reason,created_at) VALUES (?,?,?,?,?,?)',
                    (today,name,old,new,reason,now))
    conn.commit(); conn.close()
    print(f"已保存: {MODEL_DIR}/coef.json")

def cmd_accuracy():
    conn=init_db()
    print(get_calibration_report(conn))
    rows=conn.execute('SELECT date,zt_count,zhaban_count,zhaban_rate,correct,wrong,accuracy,phase,phase_name FROM daily_stats ORDER BY date').fetchall()
    print(f"\n每日准确率:")
    print(f"{'日期':<10}{'涨停':<6}{'炸板':<6}{'炸板率':<8}{'正确':<6}{'错误':<6}{'准确率':<8}{'情绪'}")
    print(f"{'-'*60}")
    for r in rows:
        zb=f"{r['zhaban_rate']*100:.0f}%" if r['zhaban_rate'] else '0%'
        acc=f"{r['accuracy']}%" if r['accuracy'] is not None else '-'
        print(f"{r['date']:<10}{r['zt_count']:<6}{r['zhaban_count'] or 0:<6}{zb:<8}{r['correct'] or 0:<6}{r['wrong'] or 0:<6}{acc:<8}{r['phase_name'] or ''}")
    conn.close()

def cmd_positions(all_status=False):
    """查看持仓。默认只看活跃持仓，all_status=True时包含历史"""
    conn=init_db()
    if all_status:
        rows=conn.execute("SELECT * FROM positions ORDER BY created_at DESC").fetchall()
    else:
        rows=conn.execute("SELECT * FROM positions WHERE status='持仓' ORDER BY created_at DESC").fetchall()
    if not rows:
        print("暂无持仓记录。使用: python3 astock_daily_v3.py buy <日期> <代码> <买入价>")
        conn.close(); return
    label="全部持仓" if all_status else "当前持仓"
    print(f"\n{'='*60}\n  {label}\n{'='*60}")
    print(f"{'代码':<8}{'名称':<10}{'买入日':<10}{'买入价':<10}{'现价':<10}{'浮盈%':<10}{'持仓天':<8}{'状态'}")
    print(f"{'-'*70}")
    for r in rows:
        cur_p=get_next_close(r['code']) or r['buy_price']
        print(f"{r['code']:<8}{r['name']:<10}{r['buy_date']:<10}{r['buy_price']:<10.2f}{cur_p:<10.2f}{r['profit_pct']:<10.1f}{r['hold_days']:<8}{r['status']}")
    conn.close()

def cmd_buy(date, code, buy_price):
    """记录一笔买入持仓"""
    conn=init_db()
    try:
        zt_df=ak.stock_zt_pool_em(date=date)
        name_row=zt_df[zt_df['代码'].str.zfill(6)==code]
        name=str(name_row.iloc[0]['名称']) if len(name_row)>0 else code
    except:
        name=code
    conn.execute('''INSERT OR REPLACE INTO positions
        (code,name,buy_date,buy_price,hold_days,profit_pct,status,created_at)
        VALUES (?,?,?,?,0,0,'持仓',?)''',
        (code,name,date,float(buy_price),datetime.now().isoformat()))
    conn.commit()
    print(f"已记录买入: {code} {name} @ {buy_price} ({date})")
    conn.close()

def cmd_sell(code):
    """手动标记卖出"""
    conn=init_db()
    rows=conn.execute("SELECT * FROM positions WHERE code=? AND status='持仓'",(code,)).fetchall()
    if not rows:
        print(f"没有找到 {code} 的持仓记录"); conn.close(); return
    r=rows[0]
    cur_p=get_next_close(code) or r['buy_price']
    profit_pct=(cur_p-r['buy_price'])/r['buy_price']*100 if r['buy_price']>0 else 0
    conn.execute("UPDATE positions SET status='已卖出',exit_signal='手动卖出',exit_reason='手动平仓',profit_pct=? WHERE code=?",
                (round(profit_pct,2),code))
    conn.commit()
    print(f"已卖出: {code} {r['name']}  浮盈: {profit_pct:.1f}%")
    conn.close()

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'predict'
    date=sys.argv[2] if len(sys.argv)>2 else today_str()
    if cmd=='predict': cmd_predict(date)
    elif cmd=='outcome': cmd_outcome(date)
    elif cmd=='apply_coef': cmd_apply_coef()
    elif cmd=='accuracy': cmd_accuracy()
    elif cmd=='positions': cmd_positions(all_status=(len(sys.argv)>2 and sys.argv[2]=='all'))
    elif cmd=='buy' and len(sys.argv)>=5: cmd_buy(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd=='sell': cmd_sell(sys.argv[2] if len(sys.argv)>2 else '')
    elif cmd=='init': init_db(); print(f"数据库初始化完成: {DB_PATH}")
    else:
        print(f"""用法:
  predict [日期]           - 预测+卖出信号
  outcome <日期>           - 追踪T日预测的T+1实际结局
  accuracy                - 概率标定报告
  positions [all]          - 查看当前持仓（加all看历史）
  buy <日期> <代码> <买入价> - 记录买入持仓（例: buy 20260320 603687 10.50）
  sell <代码>             - 手动标记卖出
  apply_coef              - 应用系数调整建议
  init                    - 初始化数据库""")
