"""
astock.quicktiny_kline
基于 QuickTiny HTTP API 的 K线获取（线程安全，每次请求独立session）
"""
import os
import requests
import time
from collections import deque
from market import ma, macd_current, rsi, vol_ma

# ===================== 限流器 =====================
_kline_calls = deque()
_KLINE_MIN_INTERVAL = 0.3  # 秒/次，30次/分钟

def _kline_wait():
    now = time.time()
    while _kline_calls and now - _kline_calls[0] > 60:
        _kline_calls.popleft()
    if len(_kline_calls) >= 30:
        sleep_time = 60 - (now - _kline_calls[0]) + 0.05
        if sleep_time > 0:
            time.sleep(sleep_time)
        _kline_wait()
    _kline_calls.append(time.time())


def _get_kline_from_parquet(code, days=120):
    """从本地parquet历史数据获取K线（无API调用）"""
    try:
        import pandas as pd, os
        single = os.path.join(os.path.dirname(__file__), "..", "data", "astock", "kline", "all_klines.parquet")
        if os.path.exists(single):
            df = pd.read_parquet(single)
            df = df[df["code"] == code]
        else:
            files = glob.glob(os.path.join(os.path.dirname(__file__), "..", "data", "astock", "kline", "batches", "batch_*.parquet"))
            if not files:
                return None
            dfs = [pd.read_parquet(f) for f in files]
            df = pd.concat(dfs, ignore_index=True)
            df = df[df["code"] == code]
        df = df[df["code"] == code].sort_values("date")
        if len(df) < 20:
            return None
        bars = df.tail(days).to_dict("records")
        closes = [float(b["close"]) for b in bars]
        highs = [float(b["high"]) for b in bars]
        lows = [float(b["low"]) for b in bars]
        vols = [float(b["volume"]) for b in bars]
        ma20 = ma(closes, 20)
        ma60 = ma(closes, 60)
        dif, de, macd_val = macd_current(closes)
        rsi_val = rsi(closes)
        vol_ma20 = vol_ma(vols, 20)
        result = {
            "dates": [str(b["date"]) for b in bars],
            "closes": closes,
            "highs": highs,
            "lows": lows,
            "vols": vols,
            "ma20": ma20,
            "ma60": ma60,
            "dif": dif,
            "de": de,
            "macd": macd_val,
            "rsi": rsi_val,
            "last_close": closes[-1],
        }
        if len(closes) >= 5:
            result["vr"] = round(sum(vols[-5:]) / (sum(vols[-10:-5]) + 1e-9), 4)
        result["trend"] = "上升" if closes[-1] > closes[-20] else "下降"
        result["vol_status"] = "放量" if vols[-1] > ma(vols, 20) * 1.2 else "缩量" if vols[-1] < ma(vols, 20) * 0.8 else "平量"
        result["macd_state"] = "多头" if dif > de else "空头"
        result["price_vs_ma20"] = round((closes[-1] - ma20) / ma20 * 100, 2) if ma20 else 0
        return result
    except Exception:
        return None


def get_kline(code, days=120):
    """
    获取个股K线+技术指标（线程安全，HTTP请求）。
    返回 dict（含所有技术指标），与 kline.py 的 get_kline() 接口兼容。
    """
    try:
        api_key = os.environ.get("LB_API_KEY", "")
        api_base = os.environ.get("LB_API_BASE", "https://stock.quicktiny.cn/api/openclaw")
        if not api_key:
            return None

        _kline_wait()
        headers = {"Authorization": f"Bearer {api_key}"}
        url = f"{api_base}/kline/{code}"
        params = {"days": min(days, 120)}
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code == 429:
            # API限流，直接用本地parquet
            return _get_kline_from_parquet(code, days)
        if r.status_code != 200:
            return None
        d = r.json()
        if not d.get("success"):
            return _get_kline_from_parquet(code, days)

        raw = d.get("data", [])
        if not isinstance(raw, list) or len(raw) < 20:
            return None

        bars = sorted(raw, key=lambda x: str(x.get("date", "")))

        try:
            closes = [float(b["close"]) for b in bars]
            highs = [float(b["high"]) for b in bars]
            lows = [float(b["low"]) for b in bars]
            vols = [float(b["volume"]) for b in bars]
        except (ValueError, KeyError):
            return None

        ma20 = ma(closes, 20)
        ma60 = ma(closes, 60)
        dif, de, macd_val = macd_current(closes)
        rsi_val = rsi(closes)
        vol_ma20 = vol_ma(vols, 20)

        # 量比（相对5日均量）
        vr = None
        if len(vols) >= 5:
            recent_avg = sum(vols[-5:]) / 5
            vr = vol_ma20 / recent_avg if (vol_ma20 and recent_avg > 0) else None

        cur = closes[-1] if closes else 0

        # 趋势
        if ma20 is None or ma60 is None:
            trend = "下降通道"
        elif ma20 > ma60 * 1.02:
            trend = "上升通道"
        elif ma20 < ma60 * 0.98:
            trend = "下降通道"
        else:
            trend = "震荡"

        # 量能状态
        if vr is not None and vr < 0.5:
            vol_status = "极度缩量"
        elif vr is not None and vr < 0.8:
            vol_status = "温和缩量"
        elif vr is not None and vr < 1.2:
            vol_status = "温和放量"
        else:
            vol_status = "明显放量"

        macd_state = "MACD多头" if (dif is not None and de is not None and dif > de) else "MACD空头"
        price_vs_ma20 = "MA20上方" if (ma20 and cur > ma20) else "MA20下方"

        return {
            "dates": [b["date"] for b in bars],
            "closes": closes,
            "highs": highs,
            "lows": lows,
            "vols": vols,
            "ma20": ma20,
            "ma60": ma60,
            "dif": dif,
            "de": de,
            "macd": macd_val,
            "rsi": rsi_val,
            "vr": vr,
            "trend": trend,
            "vol_status": vol_status,
            "price_vs_ma20": price_vs_ma20,
            "macd_state": macd_state,
            "last_close": cur,
        }
    except Exception:
        return None
