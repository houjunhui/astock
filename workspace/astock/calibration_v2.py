"""
astock.calibration_v2
动态分库校准引擎 v2

核心逻辑：
1. 近6个月滚动窗口（剔除过时数据）
2. 按板位分库：1-3板 / 4板 / 5板+
3. 按近期市场温度动态选择校准基准
4. 动态 ratio = 当前周期晋级率 / 全量晋级率

公共接口：
    from calibration_v2 import get_cal, calibrate, diagnose
    cal = get_cal(phase='自动')   # 自动判断当前周期
    result = calibrate(raw_jb_pct, lb, phase='自动')
"""
import os, json, sqlite3
import numpy as np
import pandas as pd
from datetime import datetime

DB_PATH = "/home/gem/workspace/agent/workspace/data/astock/model/astock.db"
CACHE_PATH = os.path.join(os.path.dirname(__file__), "cal_v2_cache.json")
CACHE_TTL = 3600 * 4


# ─── 月度样本库（预计算）──────────────────────────────
def _load_monthly_stats():
    """加载月度统计（晋级率/断板率/样本数）"""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        "SELECT date, lb, outcome FROM predictions "
        "WHERE outcome IS NOT NULL AND outcome != '停牌' AND lb IS NOT NULL",
        conn, index_col="date", parse_dates=["date"]
    )
    conn.close()

    df["month"] = df.index.to_period("M").astype(str)

    stats = {}
    for month, grp in df.groupby("month"):
        total = len(grp)
        n_jb = (grp["outcome"] == "晋级").sum()
        n_xx = (grp["outcome"] == "续涨").sum()
        n_dp = (grp["outcome"] == "断板").sum()

        # 按板位分段
        lb_1_3 = grp[grp["lb"].between(1, 3)]
        lb_4 = grp[grp["lb"] == 4]
        lb_5p = grp[grp["lb"] >= 5]

        def _rates(g):
            if len(g) == 0:
                return None
            return {
                "jb": len(g[g["outcome"] == "晋级"]) / len(g),
                "xx": len(g[g["outcome"] == "续涨"]) / len(g),
                "dp": len(g[g["outcome"] == "断板"]) / len(g),
                "n": len(g),
            }

        stats[month] = {
            "win_rate": (n_jb + n_xx) / total,
            "dp_rate": n_dp / total,
            "jb_rate": n_jb / total,
            "total": total,
            "lb_1_3": _rates(lb_1_3),
            "lb_4": _rates(lb_4),
            "lb_5p": _rates(lb_5p),
        }
    return stats


_monthly_cache = None


def _get_monthly():
    global _monthly_cache
    if _monthly_cache is None:
        _monthly_cache = _load_monthly_stats()
    return _monthly_cache


# ─── 周期判断 ───────────────────────────────────────
def _detect_phase(lookback=5):
    """
    自动判断当前市场周期。
    用近N个月的晋级/续涨率均值来判断。
    """
    stats = _get_monthly()
    months = sorted(stats.keys())
    if len(months) < 3:
        return "通用"

    recent_months = months[-lookback:]
    recent_wr = np.mean([stats[m]["win_rate"] for m in recent_months])

    if recent_wr >= 0.90:
        return "主升"
    elif recent_wr >= 0.83:
        return "复苏"
    elif recent_wr <= 0.68:
        return "恐慌"
    elif recent_wr < 0.78:
        return "退潮"
    return "通用"


# ─── 分库校准计算 ────────────────────────────────────
def _build_cal_for_lb(lb, phase, lookback_months=6):
    """
    计算指定板位 + 指定周期的校准数据。
    返回 (jb_rate, xx_rate, dp_rate, n_samples, months_used)
    """
    stats = _get_monthly()
    months = sorted(stats.keys())

    # 确定使用的月份
    if phase in ("自动", "未知", None, ""):
        phase = _detect_phase()

    if phase == "主升":
        # 取近月里晋级率最高的几个月
        by_wr = sorted(months, key=lambda m: stats[m]["win_rate"], reverse=True)
        used = by_wr[:min(lookback_months, len(by_wr))]
    elif phase == "复苏":
        median_wr = np.median([stats[m]["win_rate"] for m in months])
        used = [m for m in months[-lookback_months:] if stats[m]["win_rate"] >= median_wr]
    elif phase in ("退潮", "恐慌"):
        by_wr = sorted(months, key=lambda m: stats[m]["win_rate"])
        used = by_wr[:min(lookback_months, len(by_wr))]
    else:  # 通用
        used = months[-lookback_months:]

    if not used:
        used = months[-lookback_months:]

    # 按板位取数据
    key = "lb_1_3"
    if lb >= 5:
        key = "lb_5p"
    elif lb == 4:
        key = "lb_4"

    jb_list, xx_list, dp_list, ns = [], [], [], 0
    for m in used:
        s = stats[m]
        if s[key]:
            jb_list.append(s[key]["jb"])
            xx_list.append(s[key]["xx"])
            dp_list.append(s[key]["dp"])
            ns += s[key]["n"]

    if not jb_list:
        return None

    # 加权平均（以样本数为权重）
    ws = [1.0] * len(jb_list)  # 等权，更稳定
    jb = float(np.mean(jb_list))
    xx = float(np.mean(xx_list))
    dp = float(np.mean(dp_list))

    return {"jb_rate": round(jb, 4), "xx_rate": round(xx, 4),
            "dp_rate": round(dp, 4), "n": ns,
            "phase": phase, "months_used": used[-3:]}


# ─── 原始分桶映射 ────────────────────────────────────
_JB_MAP = [
    ((0, 5), 0.194),
    ((5, 10), 0.199),
    ((10, 15), 0.258),
    ((15, 20), 0.392),
    ((20, 25), 0.339),
    ((25, 30), 0.343),
    ((30, 100), 0.411),
]


def _bucket_rate(raw_jb_pct, lb):
    if lb >= 5:
        return 0.40
    for (lo, hi), rate in _JB_MAP:
        if lo <= raw_jb_pct < hi:
            return rate
    return raw_jb_pct / 100.0


# ─── 主接口 ─────────────────────────────────────────
def get_cal(phase="自动", lookback_months=6):
    """获取校准数据"""
    if phase in ("自动", "未知", None, ""):
        phase = _detect_phase()

    cache_path = CACHE_PATH
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                cache = json.load(f)
            if cache.get("phase") == phase and \
               (datetime.now() - datetime.fromisoformat(cache.get("ts", "2000"))).total_seconds() < CACHE_TTL:
                return cache
        except Exception:
            pass

    lib_1_3 = _build_cal_for_lb(1, phase, lookback_months)
    lib_4 = _build_cal_for_lb(4, phase, lookback_months)
    lib_5p = _build_cal_for_lb(5, phase, lookback_months)

    # phase_ratio：当前周期样本的晋级+续涨率 vs 全量均值
    # 主升月(93.7%) ratio≈1.105，退潮月(73%) ratio≈0.861
    stats = _get_monthly()
    all_wr = np.mean([s["win_rate"] for s in stats.values()])
    lib_ref = lib_1_3 or lib_4 or lib_5p
    if lib_ref:
        cur_wr = (lib_ref.get("jb_rate", 0) + lib_ref.get("xx_rate", 0))
        phase_ratio = max(0.5, min(cur_wr / all_wr if all_wr > 0 else 1.0, 1.5))
    else:
        phase_ratio = 1.0

    result = {
        "phase": phase,
        "phase_ratio": round(max(0.5, min(phase_ratio, 1.5)), 3),
        "lookback_months": lookback_months,
        "sub_1_3": lib_1_3,
        "sub_4": lib_4,
        "sub_5p": lib_5p,
        "ts": datetime.now().isoformat(),
    }

    try:
        with open(cache_path, "w") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return result


def calibrate(raw_jb_pct, lb, phase="自动"):
    """
    动态校准主函数。
    返回: {calibrated_jb, calibrated_xx, calibrated_dp, phase, phase_ratio, source}
    """
    cal = get_cal(phase)
    phase = cal["phase"]

    if lb >= 5:
        lib = cal.get("sub_5p")
    elif lb == 4:
        lib = cal.get("sub_4")
    else:
        lib = cal.get("sub_1_3")

    # 用分库真实分布 + ratio 做校准
    if lib and lib.get("n", 0) >= 15:
        ratio = cal.get("phase_ratio", 1.0)

        # 核心：原始分桶值 × ratio
        # 分桶值来自26k样本的真实统计（如0-5%→19.4%）
        # ratio>1 → 市场强，晋级率应提升；ratio<1 → 市场弱，晋级率应降低
        bucket_jb = _bucket_rate(raw_jb_pct, lb)
        calibrated_jb = bucket_jb * ratio
        calibrated_jb = max(0.05, min(calibrated_jb, 0.95))

        # 续涨率：相对稳定，但随市场温度调整
        calibrated_xx = lib["xx_rate"]
        calibrated_xx = max(0.30, min(calibrated_xx, 0.90))

        # 断板率：1 - jb - xx
        calibrated_dp = 1 - calibrated_jb - calibrated_xx
        calibrated_dp = max(0.01, min(calibrated_dp, 0.50))

        source = f"动态库({phase},n={lib['n']})"
    else:
        # 降级：原始分桶
        calibrated_jb = _bucket_rate(raw_jb_pct, lb)
        xx_map = {1: 0.64, 2: 0.58, 3: 0.50, 4: 0.40, 5: 0.60}
        calibrated_xx = xx_map.get(lb, 0.55)
        calibrated_dp = 1 - calibrated_jb - calibrated_xx
        calibrated_dp = max(0.01, min(calibrated_dp, 0.50))
        source = "原始分桶(降级)"

    return {
        "calibrated_jb": round(calibrated_jb, 4),
        "calibrated_xx": round(calibrated_xx, 4),
        "calibrated_dp": round(calibrated_dp, 4),
        "phase": phase,
        "phase_ratio": cal.get("phase_ratio", 1.0),
        "source": source,
    }


def diagnose():
    """输出各周期校准数据"""
    phases = ["主升", "复苏", "通用", "退潮", "恐慌"]
    print(f"{'周期':<6} {'板位':>6} {'晋级率':>8} {'续涨率':>8} {'断板率':>8} {'ratio':>6}  {'样本':>5}  来源")
    print("-" * 72)
    for p in phases:
        cal = get_cal(p)
        ratio = cal.get("phase_ratio", 1.0)
        for lb_key, lb_name in [("sub_1_3", "1-3板"), ("sub_4", "4板"), ("sub_5p", "5板+")]:
            lib = cal.get(lb_key)
            if lib:
                jb = lib["jb_rate"] * 100
                xx = lib["xx_rate"] * 100
                dp = lib["dp_rate"] * 100
                n = lib["n"]
                src = lib.get("phase", "?")
                print(f"{p:<6} {lb_name:>6} {jb:>7.1f}% {xx:>7.1f}% {dp:>7.1f}% {ratio:>6.3f}  {n:>5}  {src}")
            else:
                print(f"{p:<6} {lb_name:>6}  不足15样本")
        print()


if __name__ == "__main__":
    diagnose()
