"""
历史数据缓存管理器
透明缓存：调用时自动存/取，无需修改业务代码

缓存策略：
- ladder: 按日期缓存，KEY=date
- kline: 按(代码, 日期区间)缓存，KEY=code+days+end_date
- auction: 按日期+代码集合缓存，KEY=date+tuple(sorted(codes))

失效：不清空，保留全部历史（磁盘便宜）
"""

import os, json, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

WORKSPACE = Path("/home/gem/workspace/agent/workspace")
CACHE_DIR = WORKSPACE / "astock" / "cache"
CACHE_DIR.mkdir(exist_ok=True)

LADDER_DIR = CACHE_DIR / "ladder"
KLINE_DIR = CACHE_DIR / "kline"
AUCTION_DIR = CACHE_DIR / "auction"

for _d in [LADDER_DIR, KLINE_DIR, AUCTION_DIR]:
    _d.mkdir(exist_ok=True)

MAX_RETRIES = 2
KLINE_TIMEOUT = 5  # 秒

# ── 基础工具 ────────────────────────────────────────────────────

def _date_file(dir_path, key):
    """缓存路径：dir/KEY.json"""
    return dir_path / f"{key}.json"

def _load(dir_path, key):
    """读取缓存，失败返回None"""
    fpath = _date_file(dir_path, key)
    if fpath.exists():
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def _save(dir_path, key, data):
    """写入缓存，失败则静默"""
    fpath = _date_file(dir_path, key)
    try:
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
    except Exception:
        pass

# ── 带缓存的API包装 ──────────────────────────────────────────────

def cached_get_ladder(raw_func):
    """给 get_ladder 添加缓存包装"""
    def wrapper(date, retries=MAX_RETRIES):
        # 标准化日期
        date_str = str(date).replace("-", "")
        # 先查缓存
        cached = _load(LADDER_DIR, date_str)
        if cached is not None:
            return cached
        # 调用原始函数
        for attempt in range(retries):
            try:
                result = raw_func(date)
                if result:
                    _save(LADDER_DIR, date_str, result)
                return result
            except Exception:
                if attempt < retries - 1:
                    time.sleep(0.5)
        return None
    return wrapper


def cached_get_kline_hist(raw_func):
    """给 get_kline_hist 添加缓存包装"""
    def wrapper(code, days=30, end_date=None, retries=MAX_RETRIES):
        end_str = (str(end_date).replace("-", "") if end_date else "latest")
        cache_key = f"{code}_{days}_{end_str}"
        # 先查缓存
        cached = _load(KLINE_DIR, cache_key)
        if cached is not None:
            return cached
        # 调用原始函数
        for attempt in range(retries):
            try:
                result = raw_func(code, days=days, end_date=end_date)
                if result:
                    _save(KLINE_DIR, cache_key, result)
                return result
            except Exception:
                if attempt < retries - 1:
                    time.sleep(0.5)
        return None
    return wrapper


def cached_get_auction_for_codes(raw_func):
    """给 get_auction_for_codes 添加缓存包装"""
    def wrapper(codes, date=None, delay=0, retries=MAX_RETRIES):
        if not codes:
            return {}
        # date参数：如果是当天，则缓存key包含日期
        # 如果date=None，用codes组合作为key（实时数据不适合缓存）
        if date:
            date_str = str(date).replace("-", "")
            codes_key = "_".join(sorted(set(codes)))
            cache_key = f"{date_str}_{codes_key[:50]}"  # 限制长度
            cached = _load(AUCTION_DIR, cache_key)
            if cached is not None:
                return cached
        # 调用原始函数
        for attempt in range(retries):
            try:
                result = raw_func(list(codes), delay=delay)
                if result and date:
                    _save(AUCTION_DIR, cache_key, result)
                return result
            except Exception:
                if attempt < retries - 1:
                    time.sleep(0.3)
        return {}
    return wrapper


def apply_cache():
    """
    将缓存层应用到 quicktiny 模块。
    调用一次即可，后续所有 get_ladder/get_kline_hist/get_auction_for_codes
    自动走缓存。
    """
    from astock import quicktiny as qt

    qt.get_ladder = cached_get_ladder(qt.get_ladder)
    qt.get_kline_hist = cached_get_kline_hist(qt.get_kline_hist)
    qt.get_auction_for_codes = cached_get_auction_for_codes(qt.get_auction_for_codes)

    print(f"✅ 缓存已应用 → {CACHE_DIR}")
    print(f"   ladder: {len(list(LADDER_DIR.glob('*.json')))} 个日期")
    print(f"   kline:  {len(list(KLINE_DIR.glob('*.json')))} 个缓存")
    print(f"   auction: {len(list(AUCTION_DIR.glob('*.json')))} 个缓存")


def cache_stats():
    """显示缓存统计"""
    ladder_count = len(list(LADDER_DIR.glob("*.json")))
    kline_count = len(list(KLINE_DIR.glob("*.json")))
    auction_count = len(list(AUCTION_DIR.glob("*.json")))

    total_size = sum(
        f.stat().st_size
        for f in CACHE_DIR.rglob("*.json")
    )

    print(f"【缓存统计】")
    print(f"  ladder:  {ladder_count} 个日期")
    print(f"  kline:   {kline_count} 个缓存")
    print(f"  auction: {auction_count} 个缓存")
    print(f"  总大小:  {total_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    apply_cache()
    cache_stats()
