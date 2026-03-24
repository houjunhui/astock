#!/usr/bin/env python3
"""
astock.backtest
全量历史回测 - 向量化版本
"""
import os, sys, json, sqlite3
import pandas as pd
import numpy as np
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from predict import predict_stock


def load_all_klines():
    """加载K线，去重，按code+date排序"""
    import glob
    files = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "../data/astock/kline/batches/batch_*.parquet")))
    dfs = []
    for f in files:
        df = pd.read_parquet(f)
        dfs.append(df)
    kl = pd.concat(dfs, ignore_index=True)
    kl["date"] = pd.to_datetime(kl["date"]).dt.strftime("%Y%m%d")
    # 去重（同一code+date只保留一条，取最后一条即最新）
    kl = kl.drop_duplicates(subset=["code", "date"], keep="last")
    kl = kl.sort_values(["code", "date"]).reset_index(drop=True)
    print(f"[数据] {len(kl):,}行, {kl['date'].min()} ~ {kl['date'].max()}, {kl['code'].nunique()}只")
    return kl


def precompute_zt_by_date(kl):
    """
    预计算每日涨停股（用前收盘计算涨幅）
    返回: {date_str: set_of_zt_codes}
    """
    print("[预计算] 每日涨停股...")
    kl = kl.copy()
    # 计算前收盘
    kl["prev_close"] = kl.groupby("code")["close"].shift(1)
    # 涨跌幅
    kl["pct_chg"] = (kl["close"] - kl["prev_close"]) / kl["prev_close"] * 100
    # 涨停价（主板10%，创业板/科创板20%）
    kl["is_cyb"] = kl["code"].astype(str).str.startswith(("300", "688", "301"))
    kl["zt_price"] = kl["prev_close"] * np.where(kl["is_cyb"], 1.20, 1.10)
    # 涨停判定：最高价>=涨停价*99.9% 且 涨幅>=9.5%
    kl["is_zt"] = (kl["high"] >= kl["zt_price"] * 0.999) & (kl["pct_chg"] >= 9.5)

    # 按日期聚合涨停股
    zt_by_date = {}
    zt_rows = kl[kl["is_zt"]].groupby("date")["code"].apply(set).to_dict()
    for d, codes in zt_rows.items():
        zt_by_date[d] = codes

    dates = sorted(zt_by_date.keys())
    print(f"[预计算] 完成 {len(zt_by_date)} 个交易日, 涨停日均 {sum(len(v) for v in zt_by_date.values())/len(zt_by_date):.0f} 只")
    return zt_by_date


def get_stock_klines(kl, code):
    """获取某只股票全量K线（已排序）"""
    df = kl[kl["code"] == str(code).zfill(6)].sort_values("date")
    return df


def calc_indicators_from_df(df_tail):
    """从DataFrame片段计算技术指标"""
    if len(df_tail) < 30:
        return None
    closes = df_tail["close"].values.astype(float)
    highs = df_tail["high"].values.astype(float)
    lows = df_tail["low"].values.astype(float)
    vols = df_tail["volume"].values.astype(float) if "volume" in df_tail.columns else np.zeros(len(df_tail))

    from market import ma, macd_current, rsi, vol_ma
    ma20 = ma(list(closes), 20)
    ma60 = ma(list(closes), 60)
    dif, de, _ = macd_current(list(closes))
    rsi_val = rsi(list(closes))
    vol_ma20 = vol_ma(list(vols), 20)
    vr = None
    if len(vols) >= 5 and vol_ma20:
        recent_avg = np.mean(vols[-5:])
        vr = vol_ma20 / recent_avg

    cur = float(closes[-1])
    if ma20 is None or ma60 is None:
        trend = "下降通道"
    elif ma20 > ma60 * 1.02:
        trend = "上升通道"
    elif ma20 < ma60 * 0.98:
        trend = "下降通道"
    else:
        trend = "震荡"

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
        "closes": list(closes),
        "ma20": ma20, "ma60": ma60,
        "rsi": rsi_val, "vr": vr,
        "trend": trend, "vol_status": vol_status,
        "price_vs_ma20": price_vs_ma20,
        "macd_state": macd_state,
        "last_close": cur,
    }


def run_backtest(start_date="20240101", end_date="20260320", limit_samples=50000):
    print(f"[回测] {start_date} ~ {end_date}")
    kl = load_all_klines()
    zt_by_date = precompute_zt_by_date(kl)

    all_dates = sorted(zt_by_date.keys())
    test_dates = [d for d in all_dates if start_date <= d <= end_date]
    print(f"[回测] {len(test_dates)} 个有涨停的交易日")

    samples = []
    zt_history = {}  # {date: {code: lb}}

    for idx, date_str in enumerate(test_dates):
        if idx % 50 == 0:
            print(f"  进度 {idx}/{len(test_dates)} ... 样本{samples}")

        # 获取当日涨停codes
        zt_today_codes = zt_by_date.get(date_str, set())
        if not zt_today_codes:
            continue

        # 找前一日涨停用于算连板
        prev_date = test_dates[idx - 1] if idx > 0 else None
        prev_zt = zt_history.get(prev_date, {}) if prev_date else {}

        # 找次日（用于判定结局）
        next_date = test_dates[idx + 1] if idx + 1 < len(test_dates) else None
        next_zt = zt_by_date.get(next_date, set()) if next_date else set()

        for code in list(zt_today_codes)[:100]:  # 每日期每个票
            stock_kl = get_stock_klines(kl, code)
            # 获取历史K线（到date前为止，至少30条）
            hist = stock_kl[stock_kl["date"] < date_str].tail(120)
            if len(hist) < 30:
                continue

            # 计算连板数
            if prev_date and code in prev_zt:
                lb = prev_zt[code] + 1
            else:
                lb = 1

            # 技术指标
            kl_data = calc_indicators_from_df(hist)
            if kl_data is None:
                continue

            # 预测
            pred = predict_stock(code, lb, kl_data, phase="退潮")

            # 实际结局
            actual_jj = code in next_zt

            samples.append({
                "date": date_str,
                "code": str(code).zfill(6),
                "lb": lb,
                "pred_jb": pred.get("jb_prob", 0),
                "pred_dz": pred.get("dz_prob", 0),
                "rsi": kl_data.get("rsi") or 0,
                "vr": kl_data.get("vr") or 0,
                "trend": kl_data.get("trend", ""),
                "actual_jj": actual_jj,
            })

            if len(samples) >= limit_samples:
                print(f"  样本达到上限 {limit_samples}，停止")
                break

        # 更新连板历史
        new_lb = {}
        for code in zt_today_codes:
            if prev_date and code in prev_zt:
                new_lb[code] = prev_zt[code] + 1
            else:
                new_lb[code] = 1
        zt_history[date_str] = new_lb

        if len(samples) >= limit_samples:
            break

    print(f"\n[完成] {len(samples)} 样本")
    return samples


def analyze(samples):
    if not samples:
        print("无样本"); return

    df = pd.DataFrame(samples)
    print(f"\n{'='*60}")
    print(f"  历史回测（{len(df)} 样本 | {df['date'].min()} ~ {df['date'].max()}）")
    print(f"{'='*60}")

    total = len(df)
    actual_rate = df["actual_jj"].mean() * 100
    print(f"  总体实际晋级率: {actual_rate:.1f}%")

    print(f"\n  【按预测概率分档】")
    bins = [(0, 10), (10, 20), (20, 30), (30, 100)]
    print(f"  {'区间':^10} {'样本':^6} {'预测均':^8} {'实际率':^10} {'偏差':^8}")
    print(f"  {'-'*10} {'-'*6} {'-'*8} {'-'*10} {'-'*8}")
    for lo, hi in bins:
        mask = (df["pred_jb"] >= lo) & (df["pred_jb"] < hi)
        sub = df[mask]
        if len(sub) < 5:
            continue
        n, avg_p = len(sub), sub["pred_jb"].mean()
        actual = sub["actual_jj"].mean() * 100
        bias = actual - avg_p
        print(f"  {lo:>3}-{hi:>3}%     {n:>5}   {avg_p:>6.1f}%   {actual:>8.1f}%   {bias:>+6.1f}pp")

    print(f"\n  【按连板数分档】")
    print(f"  {'板位':^6} {'样本':^6} {'预测均':^8} {'实际率':^10} {'偏差':^8}")
    print(f"  {'-'*6} {'-'*6} {'-'*8} {'-'*10} {'-'*8}")
    for lb in sorted(df["lb"].unique()):
        sub = df[df["lb"] == lb]
        if len(sub) < 3:
            continue
        n, avg_p = len(sub), sub["pred_jb"].mean()
        actual = sub["actual_jj"].mean() * 100
        bias = actual - avg_p
        print(f"  {lb}板     {n:>5}   {avg_p:>6.1f}%   {actual:>8.1f}%   {bias:>+6.1f}pp")

    # 保存
    out = os.path.join(os.path.dirname(__file__), "../data/astock/backtest_results.csv")
    df.to_csv(out, index=False)
    print(f"\n  详细结果: {out}")

    summary = {
        "total": len(df),
        "date_range": [df["date"].min(), df["date"].max()],
        "actual_jj_rate": round(actual_rate, 2),
        "by_prob": {},
        "by_lb": {},
    }
    for lo, hi in bins:
        mask = (df["pred_jb"] >= lo) & (df["pred_jb"] < hi)
        sub = df[mask]
        if len(sub) >= 5:
            summary["by_prob"][f"{lo}_{hi}"] = {
                "n": len(sub),
                "avg_pred": round(float(sub["pred_jb"].mean()), 2),
                "actual_rate": round(float(sub["actual_jj"].mean() * 100), 2),
            }
    for lb in sorted(df["lb"].unique()):
        sub = df[df["lb"] == lb]
        if len(sub) >= 3:
            summary["by_lb"][str(lb)] = {
                "n": len(sub),
                "avg_pred": round(float(sub["pred_jb"].mean()), 2),
                "actual_rate": round(float(sub["actual_jj"].mean() * 100), 2),
            }
    sout = os.path.join(os.path.dirname(__file__), "../data/astock/backtest_summary.json")
    with open(sout, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"  统计摘要: {sout}")


if __name__ == "__main__":
    s = sys.argv[1] if len(sys.argv) > 1 else "20240101"
    e = sys.argv[2] if len(sys.argv) > 2 else "20260320"
    samples = run_backtest(s, e)
    analyze(samples)
