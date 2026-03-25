#!/usr/bin/env python3
"""
scripts/backtest_quicktiny.py
用15天缓存数据做回测分析

测试维度：
1. 龙虎榜净买入 vs 次日涨幅
2. 涨停封单强度 vs 晋级率
3. 热点板块效应 vs 个股表现
4. 炸板事件 vs 次日断板率
"""
import sys, os, json, glob
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "astock", "cache", "quicktiny")

# ═══════════════════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════════════════

def load_date(date_str):
    """加载某日的所有缓存数据"""
    d = date_str.replace("-", "")
    files = {
        "hot-sectors":  f"hot-sectors_{d}.json",
        "dragon-tiger": f"dragon-tiger_{d}.json",
        "limit-events": f"limit-events_{d}.json",
        "capital-flow": f"capital-flow_{d}.json",
    }
    result = {}
    for name, fname in files.items():
        path = os.path.join(CACHE_DIR, fname)
        try:
            with open(path, encoding="utf-8") as f:
                result[name] = json.load(f)
        except:
            result[name] = None
    return result


def get_trading_dates(n=15):
    """从缓存文件名推断交易日列表"""
    files = glob.glob(os.path.join(CACHE_DIR, "hot-sectors_*.json"))
    dates = sorted(set(
        f.split("_")[-1].replace(".json", "")
        for f in files
    ), reverse=True)[:n]
    return [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates]


# ═══════════════════════════════════════════════════════════
# 回测1：龙虎榜净买入 → 次日涨幅
# ═══════════════════════════════════════════════════════════

def backtest_dragon_tiger_netbuy():
    """
    假设：龙虎榜净买入额越大，次日涨幅越高
    分档：0~5000万 / 5000万~1亿 / 1亿~2亿 / 2亿+
    看次日(次日涨停 or 涨幅>5%)比例
    """
    dates = get_trading_dates(15)
    buckets = defaultdict(list)  # netBuy → [次日涨幅...]

    # 用 hot-sectors 的涨停池作为"次日是否涨停"的对照
    for i, date in enumerate(dates):
        if i == len(dates) - 1:
            continue
        next_date = dates[i + 1]

        dt_data = load_date(date)
        hs_data = load_date(next_date)

        dragon_tigers = dt_data.get("dragon-tiger") or []
        hot_sectors = hs_data.get("hot-sectors") or []

        # 建立次日涨停股池
        limit_up_next = set()
        for sector in hot_sectors:
            for s in sector.get("stocks", []):
                if s.get("changeTag") == "涨停" or s.get("changePercent", 0) >= 9.9:
                    limit_up_next.add(s.get("code"))

        for entry in dragon_tigers:
            code = entry.get("stockCode")
            net_buy = entry.get("netBuy", 0) or 0
            if not code or net_buy <= 0:
                continue
            chg_pct = (entry.get("chgPct") or 0) * 100
            next_day_limit_up = code in limit_up_next

            # 分档
            if net_buy < 5000:
                bucket = "<5000万"
            elif net_buy < 10000:
                bucket = "5000万~1亿"
            elif net_buy < 20000:
                bucket = "1亿~2亿"
            else:
                bucket = "2亿+"

            buckets[bucket].append({
                "code": code, "name": entry.get("stockName"),
                "net_buy": net_buy, "chg_pct": chg_pct,
                "next_limit_up": next_day_limit_up,
            })

    print("\n" + "=" * 60)
    print("回测1：龙虎榜净买入额 → 次日涨停率")
    print("=" * 60)
    print(f"{'档位':<12} {'样本数':>6} {'次日涨停率':>10} {'平均净买入':>12}")
    print("-" * 44)
    for bucket in ["<5000万", "5000万~1亿", "1亿~2亿", "2亿+"]:
        items = buckets.get(bucket, [])
        n = len(items)
        if n == 0:
            continue
        limit_up_rate = sum(1 for it in items if it["next_limit_up"]) / n * 100
        avg_net = sum(it["net_buy"] for it in items) / n
        print(f"{bucket:<12} {n:>6} {limit_up_rate:>9.1f}% {avg_net:>10.0f}万")

    return buckets


# ═══════════════════════════════════════════════════════════
# 回测2：热点板块效应
# ═══════════════════════════════════════════════════════════

def backtest_sector_effect():
    """
    假设：若某板块当日涨停股≥3只，次日板块有溢价
    统计：强势板块（涨停≥3）次日涨停股占比 vs 弱势板块
    """
    dates = get_trading_dates(15)

    strong_sector_next = []   # 强势板块次日涨停股
    weak_sector_next = []     # 弱势板块次日涨停股

    for i, date in enumerate(dates):
        if i == len(dates) - 1:
            continue
        next_date = dates[i + 1]

        hs_data = load_date(date)
        hs_next = load_date(next_date)

        # 当日各板块涨停数
        sector_limitup = {}
        for sector in (hs_data.get("hot-sectors") or []):
            sname = sector.get("name", "")
            cnt = sum(1 for s in sector.get("stocks", [])
                      if s.get("changeTag") == "涨停" or s.get("changePercent", 0) >= 9.9)
            if cnt > 0:
                sector_limitup[sname] = cnt

        # 次日各板块涨停数
        sector_next_limitup = defaultdict(int)
        for sector in (hs_next.get("hot-sectors") or []):
            sname = sector.get("name", "")
            cnt = sum(1 for s in sector.get("stocks", [])
                      if s.get("changeTag") == "涨停" or s.get("changePercent", 0) >= 9.9)
            sector_next_limitup[sname] = cnt

        # 统计
        sector_map = {s.get("name"): s for s in (hs_data.get("hot-sectors") or [])}
        for sname, cnt in sector_limitup.items():
            sector_info = sector_map.get(sname, {})
            total_stocks = len(sector_info.get("stocks") or [])
            if total_stocks == 0:
                continue
            next_cnt = sector_next_limitup.get(sname, 0)
            ratio = next_cnt / total_stocks if total_stocks > 0 else 0
            if cnt >= 3:
                strong_sector_next.append(ratio)
            elif cnt <= 1:
                weak_sector_next.append(ratio)

    print("\n" + "=" * 60)
    print("回测2：热点板块涨停家数 → 次日板块延续率")
    print("=" * 60)
    print(f"{'板块类型':<12} {'样本板块数':>8} {'次日涨停股均占比':>16}")
    print("-" * 40)
    if strong_sector_next:
        avg = sum(strong_sector_next) / len(strong_sector_next) * 100
        print(f"{'强势(≥3板)':<12} {len(strong_sector_next):>8} {avg:>14.1f}%")
    if weak_sector_next:
        avg = sum(weak_sector_next) / len(weak_sector_next) * 100
        print(f"{'弱势(≤1板)':<12} {len(weak_sector_next):>8} {avg:>14.1f}%")


# ═══════════════════════════════════════════════════════════
# 回测3：封板事件与断板率
# ═══════════════════════════════════════════════════════════

def backtest_limit_events():
    """
    假设：当日炸板（LIMIT_BACK）次数多的股票，次日渐容易断板
    统计：LIMIT_BACK事件 → 次日是否涨停
    """
    dates = get_trading_dates(15)

    broke_today = defaultdict(int)   # code → 炸板次数
    broke_code_next_up = 0
    broke_code_next_total = 0
    normal_code_next_up = 0
    normal_code_next_total = 0

    for i, date in enumerate(dates):
        if i == len(dates) - 1:
            continue
        next_date = dates[i + 1]

        ev_data = load_date(date)
        hs_next = load_date(next_date)

        events = ev_data.get("limit-events", {}) or {}
        event_list = events.get("events", []) if isinstance(events, dict) else (events or [])

        # 当日炸板股
        today_broke = set()
        for e in event_list:
            if e.get("type") == "LIMIT_BACK":
                today_broke.add(e.get("code"))

        # 次日涨停池
        next_limitup = set()
        for sector in (hs_next.get("hot-sectors") or []):
            for s in sector.get("stocks", []):
                if s.get("changeTag") == "涨停" or s.get("changePercent", 0) >= 9.9:
                    next_limitup.add(s.get("code"))

        # 建立当日涨停池（用于判断断板）
        today_limitup = set()
        for sector in (ev_data.get("hot-sectors") or []):
            for s in sector.get("stocks", []):
                if s.get("changeTag") == "涨停" or s.get("changePercent", 0) >= 9.9:
                    today_limitup.add(s.get("code"))

        # 炸板群：今日涨停 且 当日有炸板
        for code in today_broke:
            if code in today_limitup:
                broke_code_next_total += 1
                broke_code_next_up += 1 if code in next_limitup else 0

        # 正常群：今日涨停 且 无炸板
        normal_today = today_limitup - today_broke
        for code in normal_today:
            normal_code_next_total += 1
            normal_code_next_up += 1 if code in next_limitup else 0

    print("\n" + "=" * 60)
    print("回测3：炸板事件 → 次日晋级率")
    print("=" * 60)
    print(f"{'类型':<12} {'样本数':>8} {'次日涨停率':>10}")
    print("-" * 34)
    if broke_code_next_total > 0:
        rate = broke_code_next_up / broke_code_next_total * 100
        print(f"{'炸板群':<12} {broke_code_next_total:>8} {rate:>9.1f}%")
    if normal_code_next_total > 0:
        rate = normal_code_next_up / normal_code_next_total * 100
        print(f"{'未炸板群':<12} {normal_code_next_total:>8} {rate:>9.1f}%")


# ═══════════════════════════════════════════════════════════
# 回测4：连板数 → 晋级率（核心基准回测）
# ═══════════════════════════════════════════════════════════

def backtest_continuous_boards():
    """
    用hot-sectors数据统计各连板数的晋级率（横向对比昨日涨停池）
    注意：这只能统计"同日跨板位"的晋级，不能做真正T+1追踪
    """
    dates = get_trading_dates(15)
    buckets = defaultdict(list)  # continueNum → [次日是否涨停]

    for i, date in enumerate(dates):
        if i == len(dates) - 1:
            continue
        next_date = dates[i + 1]

        hs_data = load_date(date)
        hs_next = load_date(next_date)

        # 次日涨停池
        next_up = set()
        for sector in (hs_next.get("hot-sectors") or []):
            for s in sector.get("stocks", []):
                if s.get("changeTag") == "涨停" or s.get("changePercent", 0) >= 9.9:
                    next_up.add(s.get("code"))

        for sector in (hs_data.get("hot-sectors") or []):
            for s in sector.get("stocks", []):
                code = s.get("code")
                cont_num = s.get("continueNum", 0) or 0
                next_up_flag = code in next_up
                buckets[cont_num].append(next_up_flag)

    print("\n" + "=" * 60)
    print("回测4：连板数 → 次日涨停晋级率（跨15天hot-sectors）")
    print("=" * 60)
    print(f"{'连板数':>6} {'样本数':>8} {'晋级率':>10} {'备注':>20}")
    print("-" * 48)
    for cont in sorted(buckets.keys()):
        items = buckets[cont]
        n = len(items)
        if n < 3:
            continue
        rate = sum(items) / n * 100
        note = "✅ 有效样本" if n >= 10 else "⚠️ 样本偏少"
        print(f"{cont:>6}板 {n:>8} {rate:>9.1f}% {note:>20}")


# ═══════════════════════════════════════════════════════════
# 主程序
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    dates = get_trading_dates(15)
    print(f"回测数据：{dates[0]} ~ {dates[-1]}（共{len(dates)}个交易日）")

    backtest_dragon_tiger_netbuy()
    backtest_sector_effect()
    backtest_limit_events()
    backtest_continuous_boards()

    print("\n说明：")
    print("  回测1：龙虎榜买方席位净买入额分档，看次日涨停率差异")
    print("  回测2：强势板块(当日涨停≥3)次日延续效应")
    print("  回测3：炸板事件 vs 未炸板，次次日涨停率对比")
    print("  回测4：各连板数基准晋级率（参考基准）")
    print("  ⚠️ hot-sectors为收盘后快照，与实际涨停池有口径差异")
