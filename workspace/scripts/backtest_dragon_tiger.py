#!/usr/bin/env python3
"""
scripts/backtest_dragon_tiger.py
龙虎榜资金效应回测：净买入额 vs 次日涨幅

方法：
  对每个交易日 T，
  1. 取出 T 日龙虎榜，按净买入额分档
  2. 找到 T+1 日这些股票的涨幅
  3. 统计各档次日涨幅 > 5% / 涨停的比例
"""
import sys, os, json, glob
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "astock", "cache", "quicktiny")

def get_trading_dates(n=15):
    files = glob.glob(os.path.join(CACHE_DIR, "dragon-tiger_*.json"))
    dates = sorted(set(f.split("_")[-1].replace(".json", "") for f in files), reverse=True)
    return [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates[:n]]


def normalize(code):
    if not code:
        return ""
    c = code.split(".")[-1] if "." in code else code
    # 统一成 6 位
    return c.zfill(6)


def load_dt(date_str):
    path = os.path.join(CACHE_DIR, f"dragon-tiger_{date_str.replace('-','')}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_hs(date_str):
    path = os.path.join(CACHE_DIR, f"hot-sectors_{date_str.replace('-','')}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_price_chg(date_str, code):
    """从 hot-sectors 找次日(date_str)这只票的涨幅"""
    hs = load_hs(date_str)
    c = normalize(code)
    for sector in hs:
        for s in sector.get("stocks", []):
            if normalize(s.get("code", "")) == c:
                return s.get("changePercent", 0) or 0
    return None  # 未找到（未涨停/不在热点）


def run():
    dates = get_trading_dates(15)
    print(f"龙虎榜回测：{dates[0]} ~ {dates[-1]}（{len(dates)}个交易日）\n")

    # 合并相邻两天：T日龙虎榜 + T+1个股涨幅
    buckets_pct = defaultdict(list)   # netBuy档 → [次日涨幅%...]
    buckets_hit = defaultdict(list)   # netBuy档 → [是否涨停(>9.5%)...]

    STAT_LIMIT = 9.5

    for i, date in enumerate(dates):
        if i == len(dates) - 1:
            continue
        next_date = dates[i + 1]

        dt_list = load_dt(date)

        for entry in dt_list:
            code = entry.get("stockCode", "")
            net_buy = entry.get("netBuy", 0) or 0
            if not code or net_buy <= 0:
                continue

            chg = get_price_chg(next_date, code)
            if chg is None:
                continue  # 次日不在热点板块（不计入）

            # 分档
            if net_buy < 3000:
                bucket = "<3000万"
            elif net_buy < 6000:
                bucket = "3000~6000万"
            elif net_buy < 10000:
                bucket = "6000万~1亿"
            elif net_buy < 20000:
                bucket = "1亿~2亿"
            else:
                bucket = "2亿+"

            buckets_pct[bucket].append(chg)
            buckets_hit[bucket].append(1 if chg >= STAT_LIMIT else 0)

    # ── 输出结果 ──
    print(f"{"档位":<14} {"样本":>6} {"次日均涨幅":>10} {"涨幅中位数":>10} {"涨停率":>9} {">5%率":>9}")
    print("-" * 62)

    for bucket in ["<3000万", "3000~6000万", "6000万~1亿", "1亿~2亿", "2亿+"]:
        pcts = buckets_pct.get(bucket, [])
        hits = buckets_hit.get(bucket, [])
        n = len(pcts)
        if n == 0:
            continue
        avg_pct = sum(pcts) / n
        median_pct = sorted(pcts)[n // 2]
        hit_rate = sum(hits) / n * 100
        gt5_rate = sum(1 for p in pcts if p > 5) / n * 100
        print(f"{bucket:<14} {n:>6} {avg_pct:>9.1f}% {median_pct:>9.1f}% {hit_rate:>8.1f}% {gt5_rate:>8.1f}%")

    # ── 关键结论 ──
    print("\n" + "=" * 60)
    print("结论")
    print("=" * 60)
    all_pcts = buckets_pct.get("1亿~2亿", []) + buckets_pct.get("2亿+", [])
    low_pcts = buckets_pct.get("<3000万", [])
    if all_pcts and low_pcts:
        diff = sum(all_pcts) / len(all_pcts) - sum(low_pcts) / len(low_pcts)
        print(f"  高资金档(1亿+) vs 低档(<3000万) 次日涨幅差异: {diff:+.1f}%")
        if diff > 2:
            print(f"  → 资金效应显著，大单买入对次日走势有明显正贡献")
        else:
            print(f"  → 资金效应不明显，可能被市场整体走势稀释")


if __name__ == "__main__":
    run()
