#!/usr/bin/env python3
"""
scripts/backtest_dt_v4.py
龙虎榜资金效应回测：用baostock获取真实T+1价格
"""
import sys, os, json, glob
from collections import defaultdict
from datetime import datetime, timedelta
import baostock as bs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "astock", "cache", "quicktiny")

bs.login()

# ── 工具 ──

def get_trading_dates(n=15):
    files = glob.glob(os.path.join(CACHE_DIR, "dragon-tiger_*.json"))
    dates = sorted(set(f.split("_")[-1].replace(".json", "") for f in files), reverse=True)
    return [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates[:n]]


def load_dt(date_str):
    path = os.path.join(CACHE_DIR, f"dragon-tiger_{date_str.replace('-','')}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def bao_get_chg(code, date_str):
    """
    用 baostock 获取 date_str 次日的真实涨幅。
    date_str 格式 YYYYMMDD
    返回 (chg_pct, next_date_str) 或 None
    """
    # 找 date_str 的下一个交易日
    d = datetime.strptime(date_str, "%Y%m%d")
    end_d = d + timedelta(days=7)
    rs = bs.query_history_k_data_plus(
        code,
        "date,close,preclose",
        start_date=d.strftime("%Y-%m-%d"),
        end_date=end_d.strftime("%Y-%m-%d"),
        frequency="d", adjustflag="3"
    )
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if len(rows) < 2:
        return None
    # rows[0] = date_str 当天（或次一）
    # 找 T+1
    target_date = date_str
    idx = None
    for i, row in enumerate(rows):
        if row[0].replace("-", "") == target_date:
            idx = i + 1
            break
    if idx is None or idx >= len(rows):
        return None
    next_row = rows[idx]
    prev_row = rows[idx - 1]
    try:
        close_t = float(prev_row[1])
        close_next = float(next_row[1])
        preclose = float(prev_row[2])
        # 用 preclose 算 T 日涨幅
        chg_t = (close_t - preclose) / preclose * 100 if preclose > 0 else 0
        chg_next = (close_next - close_t) / close_t * 100 if close_t > 0 else 0
        return chg_next, next_row[0]
    except:
        return None


def run():
    dates = get_trading_dates(15)
    print(f"龙虎榜回测 v4：{dates[0]} ~ {dates[-1]}（{len(dates)}天）")
    print(f"用 baostock 取真实价格\n")

    buckets = defaultdict(list)

    for i, date in enumerate(dates):
        if i == len(dates) - 1:
            continue

        dt_list = load_dt(date)
        date_raw = date.replace("-", "")

        for entry in dt_list:
            code_raw = entry.get("stockCode", "")
            code_norm = code_raw.split(".")[-1].zfill(6) if code_raw else ""
            net_buy = entry.get("netBuy", 0) or 0

            if not code_norm or net_buy <= 0:
                continue

            # 拼 baostock 代码
            if code_norm.startswith("6"):
                bao_code = f"sh.{code_norm}"
            else:
                bao_code = f"sz.{code_norm}"

            result = bao_get_chg(bao_code, date_raw)
            if result is None:
                continue
            chg_next, actual_next = result

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

            buckets[bucket].append({
                "code": code_norm, "name": entry.get("stockName"),
                "net": net_buy, "chg": chg_next,
                "date": date, "actual": actual_next,
            })

        print(f"  [{i+1}/{len(dates)-1}] {date}: 处理{len(dt_list)}只龙虎榜, 有效{len([e for e in dt_list if e.get('netBuy',0)>0])}只")

    # ── 统计 ──
    print(f"\n{'档位':<14} {'样本':>6} {'均涨幅':>9} {'中位数':>9} {'最大涨幅':>9} {'最小涨幅':>9} {'>5%率':>8} {'红盘率':>9}")
    print("-" * 80)

    all_items = []
    for bucket in ["<3000万", "3000~6000万", "6000万~1亿", "1亿~2亿", "2亿+"]:
        items = buckets.get(bucket, [])
        n = len(items)
        if n == 0:
            continue
        pcts = [it["chg"] for it in items]
        avg = sum(pcts) / n
        s = sorted(pcts)
        median = s[n // 2]
        mx, mn = s[-1], s[0]
        gt5 = sum(1 for p in pcts if p > 5) / n * 100
        red = sum(1 for p in pcts if p > 0) / n * 100
        print(f"{bucket:<14} {n:>6} {avg:>8.1f}% {median:>8.1f}% {mx:>8.1f}% {mn:>8.1f}% {gt5:>7.1f}% {red:>8.1f}%")
        all_items.extend(items)

    if all_items:
        overall = [it["chg"] for it in all_items]
        print(f"{'全量':<14} {len(all_items):>6} {sum(overall)/len(overall):>8.1f}%")

    # ── 资金效应显著性 ──
    print("\n" + "=" * 60)
    print("资金效应检验")
    print("=" * 60)
    high = buckets["1亿~2亿"] + buckets["2亿+"]
    low = buckets["<3000万"]
    mid = buckets["3000~6000万"] + buckets["6000万~1亿"]

    if high and low:
        ha = sum(x["chg"] for x in high) / len(high)
        la = sum(x["chg"] for x in low) / len(low)
        print(f"  高档(1亿+) 均涨幅: {ha:+.2f}% (n={len(high)})")
        print(f"  低档(<3000万) 均涨幅: {la:+.2f}% (n={len(low)})")
        if mid:
            ma = sum(x["chg"] for x in mid) / len(mid)
            print(f"  中档(3000万~1亿) 均涨幅: {ma:+.2f}% (n={len(mid)})")
        diff = ha - la
        print(f"  差值(高-低): {diff:+.2f}%")
        if diff > 3:
            print("  ✅ 资金效应显著，大单持续买入对次日走势有明显正贡献")
        elif diff > 1:
            print("  ⚠️ 有一定正相关")
        elif diff > 0:
            print("  ⚠️ 弱正相关")
        else:
            print("  ❌ 资金效应不明显或负相关（注意：样本量有限）")

    # ── 最佳样本 ──
    print("\n" + "=" * 60)
    print("高资金档(2亿+) Top5 样本")
    print("=" * 60)
    top = sorted(buckets["2亿+"], key=lambda x: x["chg"], reverse=True)[:5]
    bot = sorted(buckets["2亿+"], key=lambda x: x["chg"])[:5]
    print("  涨幅最大：")
    for it in top:
        print(f"    {it['name']}({it['code']}) {it['date']}→{it['actual']} 净买入{it['net']:.0f}万 涨幅{it['chg']:+.1f}%")
    print("  涨幅最小：")
    for it in bot:
        print(f"    {it['name']}({it['code']}) {it['date']}→{it['actual']} 净买入{it['net']:.0f}万 涨幅{it['chg']:+.1f}%")

    bs.logout()


if __name__ == "__main__":
    run()
