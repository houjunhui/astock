"""
astock/market_sentiment.py
A股情绪周期分析模块

基于涨停数量、跌停数量、连板高度等指标判断当前市场情绪所处阶段，
并输出动态策略调整系数。
"""
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional
from collections import defaultdict

# 情绪周期阈值配置
THRESHOLDS = {
    'ice': {     # 冰点期
        'zt_max': 30,       # 涨停<30
        'dt_min': 20,       # 跌停>20（或涨停/跌停比<1）
        'lb_max': 3,        # 连板高度≤3板
        'coef': 0.4,        # 晋级概率系数
        'position': 0.2,    # 仓位上限
        'desc': '冰点期'
    },
    'start': {   # 启动期
        'zt_min': 20,
        'zt_max': 50,
        'dt_max': 15,
        'lb_min': 3,
        'lb_max': 5,
        'coef': 0.7,
        'position': 0.4,
        'desc': '启动期'
    },
    'ferment': { # 发酵期
        'zt_min': 40,
        'zt_max': 80,
        'dt_max': 10,
        'lb_min': 5,
        'coef': 1.0,
        'position': 0.6,
        'desc': '发酵期'
    },
    'peak': {    # 高潮期
        'zt_min': 60,
        'dt_max': 5,
        'lb_min': 5,
        'coef': 1.2,
        'position': 0.8,
        'desc': '高潮期'
    },
    'withdraw': {# 退潮期
        'zt_max': 30,
        'dt_min': 15,
        'coef': 0.5,
        'position': 0.2,
        'desc': '退潮期'
    }
}


def get_market_sentiment(conn: sqlite3.Connection, lookback: int = 5) -> Dict:
    """
    分析近N日市场情绪，返回当前所处阶段及详细指标
    
    返回:
        {
            'phase': str,           # 当前阶段
            'coef': float,          # 晋级概率调整系数
            'position_limit': float,# 仓位上限
            'zt_avg': float,        # 近N日涨停均值
            'zt_today': int,        # 今日涨停数
            'dt_today': int,        # 今日跌停数
            'zt_dt_ratio': float,   # 涨跌停比
            'lb_max': int,          # 最高连板数
            'signal': str,          # 情绪信号
            'advice': str,          # 策略建议
        }
    """
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 获取近N日涨停数据
    zt_rows = conn.execute("""
        SELECT date, COUNT(*) as cnt 
        FROM historical_zt 
        WHERE date >= date('now', '-20 days')
        GROUP BY date 
        ORDER BY date DESC
    """).fetchall()
    
    zt_dict = {r[0]: r[1] for r in zt_rows}
    dates = sorted(zt_dict.keys(), reverse=True)[:lookback]
    
    if not dates:
        return _default_sentiment()
    
    # 尝试获取今日/昨日跌停数据
    try:
        import akshare as ak
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
        dt_df = ak.stock_zt_pool_dtgc_em(date=yesterday)
        dt_today = len(dt_df) if dt_df is not None else 0
    except:
        dt_today = 0
    
    zt_today = zt_dict.get(dates[0], 0) if dates else 0
    zt_avg = sum(zt_dict.get(d, 0) for d in dates) / len(dates) if dates else 0
    
    # 计算涨跌停比
    zt_dt_ratio = zt_today / max(dt_today, 1)
    
    # 获取最高连板数（从historical_zt或predictions）
    try:
        lb_max = conn.execute("""
            SELECT MAX(lb) FROM historical_zt 
            WHERE date = ?
        """, (dates[0],)).fetchone()[0] or 2
    except:
        lb_max = 2
    
    # 判断阶段
    phase = _detect_phase(zt_avg, dt_today, lb_max, zt_dt_ratio)
    cfg = THRESHOLDS[phase]
    
    # 情绪信号
    signal = _get_signal(zt_dict, dates, dt_today)
    
    # 策略建议
    advice = _get_advice(phase, cfg)
    
    return {
        'phase': cfg['desc'],
        'phase_key': phase,
        'coef': cfg['coef'],
        'position_limit': cfg['position'],
        'zt_avg': round(zt_avg, 1),
        'zt_today': zt_today,
        'dt_today': dt_today,
        'zt_dt_ratio': round(zt_dt_ratio, 1),
        'lb_max': lb_max,
        'signal': signal,
        'advice': advice,
        'dates_used': dates
    }


def _detect_phase(zt_avg: float, dt: int, lb_max: int, zt_dt_ratio: float) -> str:
    """判断当前情绪阶段"""
    
    # 冰点：涨停少 + 跌停多
    if zt_avg < 30 and dt > 20:
        return 'ice'
    
    # 退潮：涨停<40 且 跌停>10（或涨跌停比<2）
    if zt_avg < 40 and dt > 10:
        return 'withdraw'
    
    # 高潮：涨停>60 + 跌停少
    if zt_avg >= 60 and dt <= 5:
        return 'peak'
    
    # 发酵：涨停40-60
    if zt_avg >= 40:
        return 'ferment'
    
    # 启动：涨停20-40
    if zt_avg >= 20:
        return 'start'
    
    # 默认退潮
    return 'withdraw'


def _get_signal(zt_dict: Dict, dates: list, dt: int) -> str:
    """生成情绪信号"""
    if len(dates) < 2:
        return '数据不足'
    
    zt_today = zt_dict.get(dates[0], 0)
    zt_yest = zt_dict.get(dates[1], 0)
    
    if zt_today > zt_yest * 1.5 and dt < 10:
        return '情绪回暖信号'
    elif zt_today < zt_yest * 0.6:
        return '情绪退潮信号'
    elif zt_today >= 50 and zt_today > zt_yest:
        return '情绪加速中'
    elif zt_today >= 60:
        return '高潮期'
    elif zt_today < 25:
        return '冰点附近'
    else:
        return '情绪平稳'


def _get_advice(phase: str, cfg: Dict) -> str:
    """根据阶段给出策略建议"""
    advices = {
        'ice': '控制仓位<20%，只做首板，严格止损-3%',
        'start': '仓位40%，1-2板为主，止损-5%',
        'ferment': '仓位60%，3板以下跟随，止损-7%',
        'peak': '仓位80%，高位龙头博弈，止损-10%',
        'withdraw': '仓位<20%，只做唯一活口，严格止损-3%'
    }
    return advices.get(phase, '')


def _default_sentiment() -> Dict:
    """默认返回值（数据不足时）"""
    return {
        'phase': '数据不足',
        'phase_key': 'withdraw',
        'coef': 0.5,
        'position_limit': 0.2,
        'zt_avg': 0,
        'zt_today': 0,
        'dt_today': 0,
        'zt_dt_ratio': 0,
        'lb_max': 0,
        'signal': '等待数据',
        'advice': '等待市场数据'
    }


def format_sentiment_report(sent: Dict) -> str:
    """格式化情绪报告"""
    lines = [
        "═" * 50,
        f"  市场情绪周期报告",
        "═" * 50,
        f"  当前阶段: 【{sent['phase']}】",
        f"  涨停均值: {sent['zt_avg']:.0f}只/天（近{sent.get('dates_used', [''])}日）",
        f"  今日涨停: {sent['zt_today']}只",
        f"  今日跌停: {sent['dt_today']}只",
        f"  涨跌停比: {sent['zt_dt_ratio']:.1f}",
        f"  最高连板: {sent['lb_max']}板",
        f"  情绪信号: {sent['signal']}",
        "─" * 50,
        f"  晋级系数: ×{sent['coef']:.1f}",
        f"  仓位上限: {sent['position_limit']*100:.0f}%",
        "─" * 50,
        f"  策略建议: {sent['advice']}",
        "═" * 50,
    ]
    return '\n'.join(lines)


if __name__ == '__main__':
    DB_PATH = '/home/gem/workspace/agent/workspace/data/astock/model/astock.db'
    conn = sqlite3.connect(DB_PATH)
    sent = get_market_sentiment(conn)
    conn.close()
    print(format_sentiment_report(sent))
