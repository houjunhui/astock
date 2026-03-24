"""
astock.market_data
市场数据获取 - 涨停池、涨停原因、炸板池、跌停池（含延迟保护）
"""
import time, random, akshare as ak
from datetime import datetime


def _safe(fn, delay=0, **kwargs):
    """带随机延迟的API调用（防止限流），quicktiny已接管主要数据，默认0延迟"""
    if delay > 0:
        time.sleep(random.uniform(delay - 1, delay + 2))
    return fn(**kwargs)


def get_zt_pool(date):
    """
    获取涨停池（含连板数）- 全部使用 quicktiny，无 akshare 依赖。
    date: YYYYMMDD 格式
    返回 (zt_df, reasons_dict)
    reasons_dict: {代码: 涨停原因}
    """
    # quicktiny 用 YYYY-MM-DD 格式
    qt_date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    try:
        from quicktiny import get_zt_stocks
        qt_stocks = get_zt_stocks(qt_date)
    except Exception:
        return None, {}

    if not qt_stocks:
        return None, {}

    import pandas as pd
    rows = []
    reasons = {}
    for s in qt_stocks:
        code = str(s.get('code', '')).zfill(6)
        rows.append({
            '代码': code,
            '名称': s.get('name', ''),
            '连板数': s.get('level', 1),
            '行业': s.get('industry', '其他'),
        })
        reason = s.get('reason', '')
        if reason:
            reasons[code] = reason

    zt_df = pd.DataFrame(rows)
    return zt_df, reasons


def get_zbgc_pool(date):
    """获取炸板池 - 已移除 akshare，暂无 quicktiny 等效接口"""
    return None


def get_dtgc_pool(date):
    """获取跌停池 - 已移除 akshare，暂无 quicktiny 等效接口"""
    return None


def get_market_sentiment(date):
    """
    获取市场情绪数据 - 使用 quicktiny auction 接口。
    返回 dict: {涨停数, 跌停数, 炸板数, 炸板率, 跌停率}
    """
    # 涨停池（quicktiny ladder）
    zt_df, _ = get_zt_pool(date)
    zt_count = len(zt_df) if zt_df is not None else 0

    # 从 quicktiny auction 获取市场宽度数据
    qt_date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    dt_count, zbgc_count = 0, 0
    try:
        from astock.cache_manager import apply_cache
        apply_cache()
        from quicktiny import get_auction
        ad = get_auction(qt_date)
        mb = ad.get('marketBreadth', {}) if ad else {}
        dt_count = mb.get('limitDownCount', 0)
        # 炸板数用 auction 的 marketBreadth 分布估算
        dist = mb.get('distribution', [])
        for item in dist:
            label = item.get('label', '')
            cnt = item.get('count', 0)
            if '涨停' in label:
                zbgc_count = cnt  # approximation
    except Exception:
        pass

    # 炸板率
    total = zt_count + zbgc_count
    zbgc_rate = zbgc_count / total * 100 if total > 0 else 0
    total2 = zt_count + dt_count
    dt_rate = dt_count / total2 * 100 if total2 > 0 else 0

    return {
        '涨停数': zt_count,
        '跌停数': dt_count,
        '炸板数': zbgc_count,
        '炸板率': round(zbgc_rate, 1),
        '跌停率': round(dt_rate, 1),
    }
