#!/usr/bin/env python3
"""
scripts/quicktiny_cache.py
缓存 quicktiny 过去 N 个交易日的核心数据，用于回测。

接口：
  1. /hot-sectors      每日热点板块 + AI个股分析
  2. /dragon-tiger    龙虎榜（营业部席位明细）
  3. /limit-events     封板/炸板事件流
  4. /anomalies        异动检测
  5. /capital-flow     资金流向（个股 + 板块）
  6. /briefings        每日简报（morning/midday/closing/evening）

用法：
  python3 scripts/quicktiny_cache.py [days=15]
  python3 scripts/quicktiny_cache.py 30
"""
import sys, os, json, time
from datetime import datetime, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from astock.quicktiny import (
    _get, _wait_for_slot,
    get_hot_sectors, get_dragon_tiger,
    get_limit_events, get_anomalies,
    get_capital_flow_v2, get_briefings,
    get_trading_calendar,
)

CACHE_DIR = os.path.join(_ROOT, "astock", "cache", "quicktiny")
os.makedirs(CACHE_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def get_past_trading_days(n=15):
    """获取过去 n 个交易日的日期列表（YYYY-MM-DD）"""
    dates = []
    d = datetime.now()
    while len(dates) < n:
        d -= timedelta(days=1)
        date_str = d.strftime("%Y%m%d")
        result = get_trading_calendar(date_str)
        if result and result.get("isTradingDay"):
            dates.append(d.strftime("%Y-%m-%d"))
    return dates


def save_json(name, date, data):
    """保存 JSON 到缓存目录"""
    path = os.path.join(CACHE_DIR, f"{name}_{date}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def load_json(name, date):
    """读取缓存"""
    path = os.path.join(CACHE_DIR, f"{name}_{date}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def api_call(name, fn, *args, max_retries=3, delay=2, **kwargs):
    """带限流和重试的 API 调用"""
    _wait_for_slot()
    last_err = None
    for attempt in range(max_retries):
        try:
            result = fn(*args, **kwargs)
            return result
        except Exception as e:
            last_err = e
            time.sleep(delay * (attempt + 1))
    print(f"  ⚠️ {name} 调用失败（{max_retries}次）: {last_err}")
    return None


# ═══════════════════════════════════════════════════════════
# 各接口抓取函数
# ═══════════════════════════════════════════════════════════

def fetch_hot_sectors(date):
    """热点板块 + AI个股分析"""
    raw = api_call("hot-sectors", get_hot_sectors, date)
    if not raw:
        return None
    # 精简：只保留核心字段
    simplified = []
    for sector in raw:
        stocks = []
        for s in sector.get("stocks", []):
            stocks.append({
                "code": s.get("code"),
                "name": s.get("name"),
                "changePercent": s.get("changePercent"),
                "continueNum": s.get("continueNum"),
                "highDays": s.get("highDays"),
                "reasonType": s.get("reasonType"),
                "changeTag": s.get("changeTag"),
                "isSt": s.get("isSt"),
            })
        simplified.append({
            "code": sector.get("code"),
            "name": sector.get("name"),
            "changePercent": sector.get("changePercent"),
            "limitUpNum": sector.get("limitUpNum"),
            "continuousPlateNum": sector.get("continuousPlateNum"),
            "highBoard": sector.get("highBoard"),
            "days": sector.get("days"),
            "stocks": stocks,
        })
    return simplified


def fetch_dragon_tiger(date):
    """龙虎榜"""
    raw = api_call("dragon-tiger", get_dragon_tiger, date, page_size=100)
    if not raw:
        return None
    simplified = []
    for entry in raw:
        buy = [{"name": b["name"][:20], "netAmt": b.get("netAmt", 0)}
               for b in (entry.get("buyBranches") or [])[:5]]
        sell = [{"name": b["name"][:20], "netAmt": b.get("netAmt", 0)}
                for b in (entry.get("sellBranches") or [])[:5]]
        simplified.append({
            "stockCode": entry.get("stockCode"),
            "stockName": entry.get("stockName"),
            "close": entry.get("close"),
            "chgPct": entry.get("chgPct"),
            "reason": entry.get("reason"),
            "netBuy": entry.get("netBuy"),
            "totalBuy": entry.get("totalBuy"),
            "totalSell": entry.get("totalSell"),
            "buyBranches": buy,
            "sellBranches": sell,
        })
    return simplified


def fetch_limit_events(date):
    """封板/炸板事件流"""
    raw = api_call("limit-events", get_limit_events, "limit_up", limit=200)
    if raw is None:
        return None
    simplified = []
    for e in raw:
        simplified.append({
            "code": e.get("code"),
            "name": e.get("name"),
            "type": e.get("type"),
            "orderAmount": e.get("orderAmount"),
            "turnover": e.get("turnover"),
            "time": e.get("time"),
        })
    return {"date": date, "events": simplified, "total": len(simplified)}


def fetch_anomalies(date):
    """异动检测"""
    raw = api_call("anomalies", get_anomalies, date=date)
    return raw if raw else None


def fetch_capital_flow(date):
    """资金流向（个股 + 板块）"""
    stock = api_call("capital-flow(stock)", get_capital_flow_v2,
                     flow_type="stock", date=date, limit=50)
    sector = api_call("capital-flow(sector)", get_capital_flow_v2,
                      flow_type="sector", date=date, limit=30)
    return {"stock": stock, "sector": sector}


def fetch_briefings(date):
    """每日简报"""
    results = {}
    for btype in ["morning", "midday", "closing", "evening"]:
        raw = api_call(f"briefings({btype})", get_briefings, date, btype)
        if raw:
            # 保留摘要字段，去掉冗长的AI分析文本
            cleaned = []
            for item in (raw if isinstance(raw, list) else [raw]):
                cleaned.append({
                    "date": item.get("date"),
                    "type": item.get("type"),
                    "title": item.get("title"),
                    "summary": item.get("summary", "")[:500],
                })
            results[btype] = cleaned
    return results if results else None


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════

def run(days=15):
    print(f"=" * 60)
    print(f"  quicktiny 回测数据缓存 · 最近 {days} 个交易日")
    print(f"  缓存目录: {CACHE_DIR}")
    print(f"=" * 60)

    trading_dates = get_past_trading_days(days)
    print(f"  交易日: {trading_dates[0]} ~ {trading_dates[-1]}（共{len(trading_dates)}天）")
    print()

    fetchers = [
        ("hot-sectors",   fetch_hot_sectors),
        ("dragon-tiger",  fetch_dragon_tiger),
        ("limit-events",  fetch_limit_events),
        ("anomalies",     fetch_anomalies),
        ("capital-flow",  fetch_capital_flow),
        ("briefings",     fetch_briefings),
    ]

    for i, date in enumerate(trading_dates):
        date_short = date.replace("-", "")
        print(f"[{i+1}/{len(trading_dates)}] {date}", end="", flush=True)

        for name, fetcher in fetchers:
            cached = load_json(name, date_short)
            if cached is not None:
                print(f" ✓", end="")
                continue
            try:
                data = fetcher(date)
                if data is not None:
                    save_json(name, date_short, data)
                    print(f" ✓", end="")
                else:
                    print(f" ○", end="")
            except Exception as e:
                print(f" ✗({e})", end="")
        print()

    print()
    print("  缓存统计：")
    for name, _ in fetchers:
        count = len([f for f in os.listdir(CACHE_DIR) if f.startswith(name + "_")])
        print(f"    {name}: {count} 天")

    print()
    print("  ✅ 完成！缓存文件位于：")
    print(f"  {CACHE_DIR}")


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    run(days)
