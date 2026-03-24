"""
历史数据缓存管理器 v2
按数据类型分层存储，透明缓存。

目录结构：
  astock/cache/
    ladder/        涨停池，按日期
    kline/         日K（get_kline_hist），按 code_days_end.json
    minute/        分时（get_minute），按 code_ndays.json
    auction/       竞价快照，按 date_codes.json
"""

import os, json, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

WORKSPACE = Path("/home/gem/workspace/agent/workspace")
CACHE_DIR = WORKSPACE / "astock" / "cache"
CACHE_DIR.mkdir(exist_ok=True)

LADDER_DIR  = CACHE_DIR / "ladder"
KLINE_DIR   = CACHE_DIR / "kline"
MINUTE_DIR  = CACHE_DIR / "minute"
AUCTION_DIR = CACHE_DIR / "auction"

for _d in [LADDER_DIR, KLINE_DIR, MINUTE_DIR, AUCTION_DIR]:
    _d.mkdir(exist_ok=True)

MAX_RETRIES = 2

# ── 基础工具 ────────────────────────────────────────────────────

def _load(dir_path, key):
    fpath = dir_path / f"{key}.json"
    if fpath.exists():
        try:
            with open(fpath, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def _save(dir_path, key, data):
    fpath = dir_path / f"{key}.json"
    try:
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
    except Exception:
        pass

def _retry_call(func, retries=MAX_RETRIES, sleep=0.3):
    for attempt in range(retries):
        try:
            return func()
        except Exception:
            if attempt < retries - 1:
                time.sleep(sleep)
    return None

# ── 缓存包装器 ──────────────────────────────────────────────────

def _wrap_ladder(raw):
    """涨停池缓存"""
    def wrapper(date, retries=MAX_RETRIES):
        key = str(date).replace("-", "")
        cached = _load(LADDER_DIR, key)
        if cached is not None:
            return cached
        result = _retry_call(lambda: raw(date), retries=retries)
        if result:
            _save(LADDER_DIR, key, result)
        return result
    return wrapper


def _wrap_kline(raw):
    """日K缓存（区分 days 参数）"""
    def wrapper(code, days=30, end_date=None, retries=MAX_RETRIES):
        end_str = (str(end_date).replace("-", "") if end_date else "latest")
        key = f"{code}_{days}_{end_str}"
        cached = _load(KLINE_DIR, key)
        if cached is not None:
            return cached
        result = _retry_call(lambda: raw(code, days=days, end_date=end_date), retries=retries)
        if result:
            _save(KLINE_DIR, key, result)
        return result
    return wrapper


def _wrap_minute(raw):
    """分时线缓存（区分 ndays 参数）"""
    def wrapper(code, ndays=1, retries=MAX_RETRIES):
        key = f"{code}_{ndays}"
        cached = _load(MINUTE_DIR, key)
        if cached is not None:
            return cached
        result = _retry_call(lambda: raw(code, ndays=ndays), retries=retries)
        if result:
            _save(MINUTE_DIR, key, result)
        return result
    return wrapper


def _wrap_auction(raw):
    """竞价快照缓存（仅当天日期有效）"""
    def wrapper(codes, date=None, delay=0, retries=MAX_RETRIES):
        if not codes:
            return {}
        key = None
        if date:
            date_str = str(date).replace("-", "")
            codes_str = "_".join(sorted(set(codes)))[:60]
            key = f"{date_str}_{codes_str}"
            cached = _load(AUCTION_DIR, key)
            if cached is not None:
                return cached
        result = _retry_call(lambda: raw(list(codes), delay=delay), retries=retries)
        if result and key:
            _save(AUCTION_DIR, key, result)
        return result or {}
    return wrapper


# ── 激活缓存 ────────────────────────────────────────────────────

def apply_cache():
    """
    将缓存层注入 quicktiny 模块。
    调用后 get_ladder / get_kline_hist / get_minute / get_auction_for_codes
    自动走缓存。
    """
    from astock import quicktiny as qt
    qt.get_ladder              = _wrap_ladder(qt.get_ladder)
    qt.get_kline_hist          = _wrap_kline(qt.get_kline_hist)
    qt.get_minute              = _wrap_minute(qt.get_minute)
    qt.get_auction_for_codes   = _wrap_auction(qt.get_auction_for_codes)


def cache_stats():
    """缓存统计"""
    stats = {
        "ladder":  len(list(LADDER_DIR.glob("*.json"))),
        "kline":   len(list(KLINE_DIR.glob("*.json"))),
        "minute":  len(list(MINUTE_DIR.glob("*.json"))),
        "auction": len(list(AUCTION_DIR.glob("*.json"))),
    }
    total_size = sum(f.stat().st_size for f in CACHE_DIR.rglob("*.json"))
    print(f"【缓存统计】")
    print(f"  ladder  (涨停池): {stats['ladder']:3d} 个")
    print(f"  kline   (日K):    {stats['kline']:3d} 个")
    print(f"  minute  (分时):   {stats['minute']:3d} 个")
    print(f"  auction (竞价):   {stats['auction']:3d} 个")
    print(f"  总大小: {total_size / 1024 / 1024:.1f} MB")
    return stats


if __name__ == "__main__":
    apply_cache()
    cache_stats()
