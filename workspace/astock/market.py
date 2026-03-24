"""
astock.market
交易日历 + 技术指标计算
"""
from datetime import datetime, timedelta
from config import PHASE_BASE_DISCOUNT


def date_from_str(s):
    return datetime.strptime(s, "%Y%m%d")


def today_str():
    return datetime.now().strftime("%Y%m%d")


def next_trading_day(date_str):
    """
    计算下一个实际交易日（跳过周末）。
    周5 → +3天 = 周一
    周6 → +2天 = 周一
    周7/1 → +1天 = 周二
    """
    d = date_from_str(date_str)
    wd = d.weekday()  # Mon=0, Fri=4, Sat=5, Sun=6
    if wd == 4:       # Friday
        nd = d + timedelta(days=3)
    elif wd == 5:     # Saturday
        nd = d + timedelta(days=2)
    elif wd == 6:     # Sunday
        nd = d + timedelta(days=1)
    else:
        nd = d + timedelta(days=1)
    return nd.strftime("%Y%m%d")


def prev_trading_day(date_str):
    """计算上一个实际交易日（跳过周末）。"""
    d = date_from_str(date_str)
    wd = d.weekday()
    if wd == 0:       # Monday
        pd = d - timedelta(days=3)
    elif wd == 6:     # Sunday
        pd = d - timedelta(days=2)
    else:
        pd = d - timedelta(days=1)
    return pd.strftime("%Y%m%d")


def is_trading_day(date_str):
    """简单判断：周一~周五为交易日（不含节假日，需配合实际数据）"""
    d = date_from_str(date_str)
    return d.weekday() < 5


# ===================== 技术指标 =====================

def ma(closes, n=20):
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def macd(closes, fast=12, slow=26, sig=9):
    """MACD指标，返回(dif历史序列, de历史序列, macd历史序列)，以及最新值"""
    if len(closes) < slow + sig:
        return None, None, None
    # 计算DIF历史序列
    dif_series = []
    ema_f = closes[0]
    ema_s = closes[0]
    k_f = 2 / (fast + 1)
    k_s = 2 / (slow + 1)
    for p in closes[1:]:
        ema_f = p * k_f + ema_f * (1 - k_f)
        ema_s = p * k_s + ema_s * (1 - k_s)
        dif_series.append(ema_f - ema_s)

    if len(dif_series) < sig:
        return None, None, None

    # 计算DEA信号线（DIF的EMA）
    de_series = []
    ema_de = dif_series[0]
    k_de = 2 / (sig + 1)
    for d in dif_series:
        ema_de = d * k_de + ema_de * (1 - k_de)
        de_series.append(ema_de)

    # MACD柱 = 2 * (DIF - DEA)
    macd_series = [2 * (dif_series[i] - de_series[i]) for i in range(len(dif_series))]

    return dif_series, de_series, macd_series


def macd_current(closes, fast=12, slow=26, sig=9):
    """返回最新单值 DIF, DEA, MACD（兼容旧接口）"""
    dif_s, de_s, macd_s = macd(closes, fast, slow, sig)
    if dif_s is None:
        return None, None, None
    return dif_s[-1], de_s[-1], macd_s[-1]




def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-n:]) / n
    al = sum(losses[-n:]) / n
    if al == 0:
        return 100
    return 100 - (100 / (1 + ag / al))


def vol_ma(vols, n=20):
    if len(vols) < n:
        return None
    return sum(vols[-n:]) / n


def trend(closes, ma20, ma60):
    if ma20 is None or ma60 is None:
        return '下降通道'
    if ma20 > ma60 * 1.02:
        return '上升通道'
    if ma20 < ma60 * 0.98:
        return '下降通道'
    return '震荡'


def phase_discount(phase):
    return PHASE_BASE_DISCOUNT.get(phase, 1.0)
