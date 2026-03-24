#!/usr/bin/env python3
"""
astock_auction_full.py
完整竞价选股流程 v2 - 全维度整合

整合模块:
  1. quicktiny    → 涨停梯队 + 竞价数据 + 市场概况 + 板块排行
  2. market.py    → RSI / MACD / MA / VR 技术指标
  3. predict_calibrated → predict_stock_v2 (ML概率 + 断板风险)
  4. auction.py   → auction_tier (五维评级)

输出：S/A/B/C 级选股 + 仓位建议 + 详细信号拆解
用法: python scripts/astock_auction_full.py [date]
"""
import sys, os, time
# ─── 工作空间根目录 ─────────────────────────────────────
_WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # workspace/
sys.path.insert(0, _WS)

from collections import defaultdict

# ─── 环境变量加载（直接用绝对路径）───────────────────────
_DOTENV = os.path.join(_WS, ".env")
if os.path.exists(_DOTENV):
    with open(_DOTENV) as f:
        for line in f:
            line = line.strip()
            if line.startswith("export ") and "=" in line:
                k, v = line[7:].split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")  # 去掉首尾引号
                os.environ[k] = v

from astock.quicktiny import (
    get_ladder, get_auction_for_codes, get_market_overview_fixed,
    get_concept_ranking, get_limit_stats, get_limit_down, get_broken_limit_up,
    get_kline_ohlcv
)
from astock.market import rsi as calc_rsi, macd_current, ma, vol_ma
from astock.predict_calibrated import (
    predict_stock_v2, check_dz_risk,
    calibrate_jb_prob, calc_xx_prob
)
from astock.auction import auction_tier

TODAY = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y-%m-%d")
TODAY_RAW = TODAY.replace("-", "")  # YYYYMMDD

# ═══════════════════════════════════════════════════════════
# STEP 1: 市场概况 + 阶段判断
# ═══════════════════════════════════════════════════════════
print("=" * 95)
print(f"【市场概况】  {TODAY}")

overview = get_market_overview_fixed(TODAY)
stats    = get_limit_stats(TODAY_RAW)
market_temp = overview.get("market_temperature", 0)
zt_count    = overview.get("limit_up_count", 0)
dt_count    = overview.get("limit_down_count", 0)
zb_count    = overview.get("limit_up_broken_count", 0)
zb_ratio    = overview.get("limit_up_broken_ratio", 0)
dt_ratio    = dt_count / max(zt_count, 1)

print(f"  涨停: {zt_count}  跌停: {dt_count}  炸板: {zb_count}({zb_ratio:.1%})  温度: {market_temp:.1f}")

# 市场阶段判断（退潮/冰点/发酵/主升）
if market_temp < 15 or dt_ratio > 2.0:
    phase = "退潮"
    phase_emoji = "⚠️"
elif market_temp < 22:
    phase = "冰点"
    phase_emoji = "🧊"
elif market_temp > 45:
    phase = "主升"
    phase_emoji = "🚀"
else:
    phase = "发酵"
    phase_emoji = "🔥"

print(f"  阶段: {phase_emoji} {phase}")
print()

# ═══════════════════════════════════════════════════════════
# STEP 2: 今日涨停梯队
# ═══════════════════════════════════════════════════════════
print("=" * 95)
print(f"【涨停梯队】")

ladder = get_ladder(TODAY_RAW)
if not ladder:
    print("  ⚠️ 今日无涨停数据（或非交易日）")
    sys.exit(0)

zt_all = []
for b in ladder["boards"]:
    lv = b.get("level", 1)
    for s in b.get("stocks", []):
        zt_all.append({
            "code": s.get("code", ""),
            "name": s.get("name", ""),
            "level": lv,
            "industry": s.get("industry", ""),
            "reason": str(s.get("jiuyangongshe_analysis", "") or "")[:80],
            "limit_up_type": s.get("limit_up_type", ""),   # 一字板/换手板/T字板
            "limit_up_suc_rate": s.get("limit_up_suc_rate"),
            "order_amount": s.get("order_amount", 0),
            "turnover_rate": s.get("turnover_rate", 0),
            "is_again_limit": s.get("is_again_limit", 0),
            "continue_num": s.get("continue_num", lv),
            # 额外字段
            "jiuyangongshe_analysis": str(s.get("jiuyangongshe_analysis", "") or ""),
        })

print(f"  共 {len(zt_all)} 只")
for b in ladder["boards"]:
    lv = b.get("level", 1)
    names = [s["name"] for s in b.get("stocks", [])]
    print(f"  {lv}板: {', '.join(names)}")
print()

# ═══════════════════════════════════════════════════════════
# STEP 3: 板块热度（板块联动信号）
# ═══════════════════════════════════════════════════════════
print("=" * 95)
print("【板块热度】")

# 统计各板块涨停数量
sector_count = defaultdict(int)
for s in zt_all:
    if s.get("industry"):
        sector_count[s["industry"]] += 1

# 取前10热门板块
sector_hot = {}
if sector_count:
    sorted_sectors = sorted(sector_count.items(), key=lambda x: x[1], reverse=True)
    hot_sectors = {k: v for k, v in sorted_sectors[:5] if v >= 2}
    print(f"  热门板块: {dict(sorted_sectors[:8])}")
    for sec, cnt in hot_sectors.items():
        print(f"  🔥 {sec}: {cnt}只涨停")
    for s in zt_all:
        s["sector_hot"] = sector_count.get(s["industry"], 0) >= 2
else:
    for s in zt_all:
        s["sector_hot"] = False
print()

# ═══════════════════════════════════════════════════════════
# STEP 4: 竞价数据批量拉取
# ═══════════════════════════════════════════════════════════
print("=" * 95)
print("【竞价数据拉取】")
all_codes = [s["code"] for s in zt_all]
auction_data = get_auction_for_codes(all_codes, delay=0.3)
print(f"  获取: {len(auction_data)}/{len(all_codes)} 只")
print()

# ═══════════════════════════════════════════════════════════
# STEP 5: 技术指标 + ML预测（逐股计算）
# ═══════════════════════════════════════════════════════════
print("=" * 95)
print("【技术指标 + ML预测】")
print(f"  {'代码':<8} {'名称':<10} {'RSI':>5} {'VR':>5} {'趋势':<8} {'MACD':>6} {'校准晋级':>7} {'ML概率':>6} {'断板风险'}")
print(f"  {'-'*90}")

stock_results = []

for s in zt_all:
    code = s["code"]
    lb   = s["level"]
    ad   = auction_data.get(code, {})
    auction_chng = ad.get("changeRate", 0)
    vr_auction   = ad.get("volumeRatio", 1.0)
    zt_yesterday = s["limit_up_type"] == "一字板"

    # ── K线数据 ──
    dates, opens, highs, lows, closes, vols = get_kline_ohlcv(code, days=60)

    # ── 技术指标 ──
    if len(closes) >= 20:
        ma20_val = ma(closes, 20)
        ma60_val = ma(closes, 60)
        dif_val, de_val, macd_val = macd_current(closes)
        rsi_val  = calc_rsi(closes)
        vol_ma20 = vol_ma(vols, 20)
        recent_vol = sum(vols[-5:]) / 5 if vols else 0
        vr_kline  = vol_ma20 / recent_vol if (vol_ma20 and recent_vol) else 1.0

        # 趋势判断
        if ma20_val and ma60_val:
            if ma20_val > ma60_val * 1.02:
                trend = "上升通道"
            elif ma20_val < ma60_val * 0.98:
                trend = "下降通道"
            else:
                trend = "震荡"
        else:
            trend = "震荡"

        # MACD状态
        if dif_val is not None and de_val is not None:
            macd_state = "MACD多头" if dif_val > de_val else "MACD空头"
        else:
            macd_state = ""
    else:
        ma20_val = ma60_val = dif_val = macd_val = rsi_val = vr_kline = None
        trend = macd_state = "数据不足"

    # ── 构建 kl dict ──
    kl = {
        "last_close": closes[-1] if closes else 0,
        "ma20": ma20_val,
        "ma60": ma60_val,
        "rsi": rsi_val,
        "vr":  vr_kline if vr_kline else 1.0,
        "dif": dif_val,
        "macd": macd_val,
        "trend": trend,
        "macd_state": macd_state,
    }

    # ── ML预测（predict_stock_v2） ──
    try:
        pred = predict_stock_v2(
            code=code, lb=lb, kl=kl,
            phase=phase,
            auction_chng=auction_chng,
            zt_yesterday=zt_yesterday,
        )
        ml_prob       = pred.get("ml_prob")
        calibrated_jb = pred.get("calibrated_jb_prob", 0)
        raw_jb_pct   = pred.get("raw_jb_pct", 0)
        dz_risks     = pred.get("dz_risks", [])
        raw_dz_pct   = pred.get("raw_dz_pct", 0)
    except Exception as e:
        calibrated_jb = (s.get("limit_up_suc_rate") or 0.5) * 100  # 转百分比
        ml_prob = None
        dz_risks = []
        raw_jb_pct = calibrated_jb

    # ── check_dz_risk 补充（独立函数） ──
    try:
        extra_risks = check_dz_risk(
            rsi=rsi_val, vr=vr_kline, lb=lb,
            auction_chng=auction_chng,
            trend=trend, macd_state=macd_state,
            zt_yesterday=zt_yesterday
        )
        # 合并去重
        all_risks = list({r: r for r in dz_risks + extra_risks}.values())
    except Exception:
        all_risks = dz_risks

    # ── 校准后jb_prob用于评级（D4替代）── calibrated_jb_prob 已是百分比形式（40.8=40.8%）────
    jb_prob_for_tier = calibrated_jb if calibrated_jb else (s.get("limit_up_suc_rate") or 0.5) * 100

    # ── 打印技术指标行 ──
    rsi_str = f"{rsi_val:.0f}" if rsi_val else "N/A"
    vr_str  = f"{vr_kline:.2f}" if vr_kline else "N/A"
    dif_str = f"{dif_val:.3f}" if dif_val is not None else "N/A"
    cal_jb_str = f"{calibrated_jb:.1f}%" if calibrated_jb else "N/A"
    ml_str  = f"{ml_prob:.0%}" if ml_prob else "N/A"
    risk_str = all_risks[0][:25] if all_risks else ""
    flag = "⚠️" if all_risks else "  "
    print(f"  {flag}{code:<8} {s['name']:<10} {rsi_str:>5} {vr_str:>5} {trend:<8} {dif_str:>6} {cal_jb_str:>8} {ml_str:>7} {risk_str}")

    stock_results.append({
        **s,
        "auction_chng": auction_chng,
        "vr_auction":   vr_auction,
        "vr_kline":     vr_kline if vr_kline else 1.0,
        "rsi":          rsi_val,
        "dif":          dif_val,
        "macd_state":   macd_state,
        "trend":        trend,
        "ma20":         ma20_val,
        "ma60":         ma60_val,
        "kl":           kl,
        "ml_prob":      ml_prob,
        "calibrated_jb": calibrated_jb,
        "dz_risks":     all_risks,
        "jb_prob":      jb_prob_for_tier,
        "zt_yesterday": zt_yesterday,
    })

print()

# ═══════════════════════════════════════════════════════════
# STEP 6: 五维评级（完整版）
# ═══════════════════════════════════════════════════════════
print("=" * 95)
print(f"【竞价选股评级】— 阶段:{phase}  温度:{market_temp:.1f}")
print()

tier_order = {"S": 0, "A": 1, "B": 2, "C": 3}
final_results = []

for r in stock_results:
    tier_info = auction_tier(
        code=r["code"],
        name=r["name"],
        lb=r["level"],
        jb_prob=r["jb_prob"],
        vr=r["vr_auction"] if r["vr_auction"] else 1.0,
        auction_chng=r["auction_chng"],
        zt_yesterday=r["zt_yesterday"],
        phase=phase,
        dz_risks=r["dz_risks"],
        ml_prob=r["ml_prob"],
        limit_up_suc_rate=r["limit_up_suc_rate"],
    )
    final_results.append({**r, **tier_info})

final_results.sort(key=lambda x: (tier_order.get(x["tier"], 99), -x["position"]))

S_list = [r for r in final_results if r["tier"] == "S"]
A_list = [r for r in final_results if r["tier"] == "A"]
B_list = [r for r in final_results if r["tier"] == "B"]
C_list = [r for r in final_results if r["tier"] == "C"]

def show_list(tag, emoji, items):
    if not items:
        print(f"  {emoji} {tag}: 无")
        return
    print(f"  {emoji} {tag}（{len(items)}只）")
    print(f"  {'代码':<8} {'名称':<10} {'板位':<5} {'竞价':>7} {'VR':>5} {'仓位':<5} {'晋级校准':>7} {'断板风险'}")
    print(f"  {'-'*85}")
    for r in items:
        lvl = f"{r['level']}板"
        veto = r["veto_reasons"]
        warn = r["warnings"]
        signal = (veto or warn)
        signal_str = signal[0][:28] if signal else "✅正常"
        chg = r["auction_chng"]
        cal_jb = f"{r['calibrated_jb']:.1f}%" if r.get('calibrated_jb') else "N/A"
        flag = "⚠️" if veto or any("⚠️" in s for s in signal) else ""
        print(f"  {flag}{r['code']:<8} {r['name']:<10} {lvl:<5} {chg:>+6.2f}% {r['vr_auction']:>5.1f} {r['position']:>5.0%} {cal_jb:>8} {signal_str}")
    print()

show_list("S级 重点关注", "🟢", S_list)
show_list("A级 半仓", "🟡", A_list)
show_list("B级 轻仓30%", "🟡", B_list)
show_list("C级 放弃", "🔴", C_list)

# ═══════════════════════════════════════════════════════════
# STEP 7: 高VR + 断板预警汇总
# ═══════════════════════════════════════════════════════════
print("=" * 95)
print("【高VR预警 + 断板风险详情】")
print()

for r in final_results:
    chg = r["auction_chng"]
    vr  = r["vr_auction"]
    risks = r["dz_risks"]
    if not risks:
        continue
    print(f"  ⚠️ {r['code']} {r['name']:<10} {chg:>+6.2f}% VR={vr:.1f}")
    for risk in risks:
        print(f"      → {risk}")
    print()

# 高VR异常（无断板风险但VR爆表）
print("【高VR异常（无断板风险但VR>10）】")
for r in final_results:
    if r["vr_auction"] > 10 and not r["dz_risks"]:
        chg = r["auction_chng"]
        vr  = r["vr_auction"]
        reason = "VR极端异常，对倒嫌疑" if vr > 15 else "VR偏高，小心"
        print(f"  ⚠️ {r['code']} {r['name']:<10} 竞价{chg:>+6.2f}% VR={vr:.1f} | {reason}")
print()

# ═══════════════════════════════════════════════════════════
# STEP 8: 操作建议汇总
# ═══════════════════════════════════════════════════════════
print("=" * 95)
print(f"【操作建议】  {TODAY}  {phase_emoji}{phase}")
print()

# 按仓位排序（排除C级）
actionable = [r for r in final_results if r["tier"] != "C"]
if not actionable:
    print("  ⚠️ 市场无合格标的，观望为主")
else:
    total_position = sum(r["position"] for r in actionable)
    print(f"  可操作标的: {len(actionable)} 只 | 总建议仓位: ≤{total_position:.0%}")
    print()
    print(f"  {'优先级':<4} {'代码':<8} {'名称':<10} {'板位':<5} {'竞价':>7} {'仓位':<6} {'核心逻辑'}")
    print(f"  {'-'*85}")
    for i, r in enumerate(actionable, 1):
        tier = r["tier_ext"]
        cal_jb = f"{r['calibrated_jb']:.1f}%" if r.get('calibrated_jb') else "N/A"
        reason_raw = (r["veto_reasons"] or r["warnings"] or ["稳健"])[0]
        reason = reason_raw[:30]
        lvl = f"{r['level']}板"
        flag = "🔴" if tier.startswith("C") else ("🟡" if tier.startswith("B") else "🟢")
        print(f"  {flag}{i:<3} {r['code']:<8} {r['name']:<10} {lvl:<5} {r['auction_chng']:>+6.2f}% {r['position']:>5.0%}  {reason}")

print()
print("=" * 95)
print(f"说明: ML概率={ml_str} → XGBoost模型输出 | 晋级校准=历史晋级率校准 | VR=竞价量比")
print(f"      断板风险=RSI>75/一字板高开/下降通道+MACD空头等多维度信号")
print("=" * 95)
