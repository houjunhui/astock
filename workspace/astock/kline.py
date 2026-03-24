"""
astock.kline
K线数据获取 - 使用BaoStock（稳定不限流）
AKShare仅用于涨停池
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__) or '.')

from datetime import datetime, timedelta
import baostock as bs
import akshare as ak
from market import ma, macd_current, rsi, vol_ma

# 缓存登录状态
_bs_conn = None

def _bs_login():
    """BaoStock登录（全局复用）"""
    global _bs_conn
    if _bs_conn is None:
        _bs_conn = bs.login()
    return _bs_conn


def _bs_logout():
    """BaoStock登出"""
    global _bs_conn
    if _bs_conn:
        bs.logout()
        _bs_conn = None


def code_to_baostock(code):
    """
    内部代码 → BaoStock格式
    603687 → sh.603687
    000001 → sz.000001
    """
    code = code.zfill(6)
    if code.startswith(('6', '9')):
        return f"sh.{code}"
    return f"sz.{code}"


def get_kline(code, days=120):
    """
    获取个股K线数据（使用BaoStock，不限流）。
    返回dict包含所有技术指标。
    """
    bs_code = code_to_baostock(code)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y-%m-%d")

    _bs_login()
    rs = bs.query_history_k_data_plus(
        bs_code,
        "date,code,open,high,low,close,volume,adjustflag",
        start_date=start_date,
        end_date=end_date,
        frequency="d"
    )

    if rs.error_msg != 'success':
        return None

    rows = []
    while rs.next():
        rows.append(rs.get_row_data())

    if len(rows) < 30:
        return None

    # 转float
    try:
        data = [{
            'date': r[0],
            'open': float(r[2]),
            'high': float(r[3]),
            'low': float(r[4]),
            'close': float(r[5]),
            'volume': float(r[6]),
            'adj': r[7],  # 1=不复权 2=前复权 3=后复权
        } for r in rows[-days:]]
    except (ValueError, IndexError):
        return None

    closes = [d['close'] for d in data]
    highs = [d['high'] for d in data]
    lows = [d['low'] for d in data]
    vols = [d['volume'] for d in data]

    ma20 = ma(closes, 20)
    ma60 = ma(closes, 60)
    dif, de, macd_val = macd_current(closes)
    rsi_val = rsi(closes)
    vol_ma20 = vol_ma(vols, 20)

    # 量比（相对20日均量）
    vr = None
    if vol_ma20:
        recent_vol_avg = sum(vols[-20:]) / 20
        vr = vol_ma20 / recent_vol_avg if recent_vol_avg else None

    cur = closes[-1]

    # 趋势
    if ma20 is None or ma60 is None:
        t = '下降通道'
    elif ma20 > ma60 * 1.02:
        t = '上升通道'
    elif ma20 < ma60 * 0.98:
        t = '下降通道'
    else:
        t = '震荡'

    # 缩放状态
    if vr is not None and vr < 0.5:
        vol_status = '极度缩量'
    elif vr is not None and vr < 0.8:
        vol_status = '温和缩量'
    elif vr is not None and vr < 1.2:
        vol_status = '温和放量'
    else:
        vol_status = '明显放量'

    macd_state = 'MACD多头' if (dif is not None and de is not None and dif > de) else 'MACD空头'
    price_vs_ma20 = 'MA20上方' if (ma20 and cur > ma20) else 'MA20下方'

    return {
        'dates': [d['date'] for d in data],
        'closes': closes,
        'highs': highs,
        'lows': lows,
        'vols': vols,
        'ma20': ma20,
        'ma60': ma60,
        'dif': dif,
        'de': de,
        'macd': macd_val,
        'rsi': rsi_val,
        'vr': vr,
        'trend': t,
        'vol_status': vol_status,
        'price_vs_ma20': price_vs_ma20,
        'macd_state': macd_state,
        'last_close': cur,
    }


def get_next_close(code):
    """获取最近收盘价（用于持仓浮盈计算）"""
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    bs_code = code_to_baostock(code.zfill(6))

    _bs_login()
    rs = bs.query_history_k_data_plus(
        bs_code,
        "date,close",
        start_date=start_date,
        end_date=end_date,
        frequency="d"
    )

    rows = []
    while rs.next():
        rows.append(rs.get_row_data())

    if rows:
        try:
            return float(rows[-1][1])
        except (ValueError, IndexError):
            pass
    return None
