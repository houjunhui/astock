"""
astock.auction
竞价条件判断 v2 - 量化规则版

核心原则：
- 竞价区间由"今日实际竞价涨幅" + "昨日是否涨停" 直接决定
- 警告信息量化：触发条件 → 结论 → 操作
- 输出：区间 + 量能要求 + 止损止盈参考价
"""
import math

def calc_auction_range(lb, jb_prob, vr=None, sector_hot=False, phase="退潮",
                       auction_chng=None, zt_yesterday=False, last_close=None):
    """
    综合多因素计算竞价区间。

    核心变化：区间由 auction_chng 直接决定，而非由 jb_prob 反推。
    昨日涨停 + 今日高开 >5% = 一字板形态 → 独立规则

    返回 (ok_min, ok_max, vol_req, warning, stop_loss, take_profit, reasoning)
    """
    reasoning = []
    warnings = []
    stop_loss, take_profit = None, None

    # === 一字板形态 ===
    if zt_yesterday and auction_chng is not None and auction_chng > 5:
        ok_min, ok_max = 0.0, 2.0
        vol_req = "平量/缩量"
        warnings.append(f"一字板高开{auction_chng:+.1f}%→断板>60%")
        if last_close:
            stop_loss = round(last_close * 0.98, 2)
            take_profit = round(last_close * 1.05, 2)
        reasoning.append("一字板形态：仅接受平开或小幅高开")
        return ok_min, ok_max, vol_req, " | ".join(warnings), stop_loss, take_profit, "; ".join(reasoning)

    # === 正常形态：基础区间由板位决定 ===
    if lb >= 5:
        base_min, base_max = 0.5, 3.0
        reasoning.append("5板+，控盘区")
    elif lb == 4:
        base_min, base_max = 1.0, 4.0
        reasoning.append("4板，高位控盘")
    elif lb == 3:
        base_min, base_max = 1.5, 4.5
        reasoning.append("3板，关键分歧位")
    elif lb == 2:
        base_min, base_max = 2.0, 5.0
        reasoning.append("2板，加速期")
    else:
        base_min, base_max = 2.5, 5.5
        reasoning.append("1板，首板观察")

    # ---- 今日竞价涨幅偏离调整 ----
    if auction_chng is not None:
        mid = (base_min + base_max) / 2
        if auction_chng < base_min - 1.0:
            warnings.append(f"竞价{auction_chng:+.1f}%低于预期{base_min:.0f}%→情绪偏弱")
        elif auction_chng > base_max + 2.0:
            warnings.append(f"竞价{auction_chng:+.1f}%高于预期{base_max:.0f}%→过热慎入")

    # ---- 晋级概率调整 ----
    prob = jb_prob
    if prob >= 35:
        adj = -0.5
        reasoning.append(f"高概率{prob:.0f}%→收窄")
    elif prob >= 25:
        adj = 0.0
        reasoning.append(f"中高概率{prob:.0f}%→标准")
    elif prob >= 18:
        adj = +0.3
        reasoning.append(f"中概率{prob:.0f}%→略宽")
    else:
        adj = +0.5
        reasoning.append(f"低概率{prob:.0f}%→从宽")
    base_min += adj
    base_max += adj

    # ---- 量比调整 ----
    vol_req = "平量/缩量"
    if vr is not None:
        if vr < 0.5:
            base_min = max(0.5, base_min - 1.0)
            base_max = base_max - 0.5
            vol_req = "极度缩量优先"
            reasoning.append(f"VR={vr:.2f}极度缩量→控盘信号")
        elif vr < 0.8:
            base_min = max(1.0, base_min - 0.5)
            base_max = base_max - 0.3
            vol_req = "缩量/平量"
            reasoning.append(f"VR={vr:.2f}缩量→控盘偏好")
        elif vr > 2.0:
            base_min = base_min + 0.5
            base_max = base_max + 0.5
            vol_req = "需明显放量"
            reasoning.append(f"VR={vr:.2f}放量→区间上调")
        elif vr > 1.5:
            base_min = base_min + 0.3
            base_max = base_max + 0.3
            vol_req = "温和放量"
            reasoning.append(f"VR={vr:.2f}温和放量→量能支撑")

    # ---- 板块联动 ----
    if sector_hot:
        base_min = max(1.0, base_min - 0.3)
        base_max = base_max - 0.2
        reasoning.append("🚀板块联动→热度缓冲")

    # ---- 市场阶段 ----
    if phase == "退潮":
        base_max = base_max - 0.5
        reasoning.append("退潮期→防御优先")
    elif phase == "冰点":
        base_max = base_max - 0.3
        reasoning.append("冰点期→极度防御")
    elif phase == "发酵":
        base_min = base_min - 0.3
        base_max = base_max + 0.3
        reasoning.append("发酵期→进攻窗口")

    # ---- 最终（四舍五入，避免Python banker's rounding）----
    ok_min = max(0.0, math.floor(base_min * 2 + 0.5) / 2)
    ok_max = max(ok_min + 1.0, math.floor(base_max * 2 + 0.5) / 2)
    ok_max = min(ok_max, 8.0)

    # ---- 量化警告 ----
    if lb >= 4:
        if ok_max >= 4:
            warnings.append(f"⚠️{lb}板：竞价>{ok_max:.0f}%→获利盘砸盘风险")
        if ok_min <= 1:
            warnings.append(f"竞价<{ok_min:.0f}%→情绪弱接力不足")
    elif lb == 3:
        warnings.append("⚠️3板分歧：竞价>4%易炸板")
    if jb_prob < 15:
        warnings.append(f"晋级{jb_prob:.0f}%偏低：需{ok_min:.0f}~{ok_max:.0f}%强势竞价")
    if phase in ("退潮", "冰点"):
        warnings.append(f"⚠️{phase}期：控仓")

    # ---- 止盈止损参考 ----
    if last_close:
        zt_price = round(last_close * 1.10, 2)
        stop_loss = round(zt_price * (1 - ok_max / 100) * 0.97, 2)
        take_profit = zt_price

    if not warnings:
        if sector_hot:
            warnings.append("✅板块联动+情绪支持")
        elif jb_prob >= 25:
            warnings.append("✅晋级概率良好")
        else:
            warnings.append("观察")

    return ok_min, ok_max, vol_req, " | ".join(warnings), stop_loss, take_profit, "; ".join(reasoning)


def auction_ok(code, name, lb, jb_prob, vr=None, sector_hot=False, phase="退潮",
               auction_chng=None, zt_yesterday=False, last_close=None):
    """
    生成竞价观察条件。
    返回 (ok_min, ok_max, vol_req, warning, stop_loss, take_profit, reasoning)
    """
    return calc_auction_range(lb, jb_prob, vr, sector_hot, phase,
                               auction_chng, zt_yesterday, last_close)


# ============================================================
# S/A/B/C 竞价评级（方案新增）
# ============================================================

def auction_tier(code, name, lb, jb_prob, vr=None, auction_chng=None,
                 zt_yesterday=False, phase="退潮", dz_risks=None,
                 ml_prob=None, limit_up_suc_rate=None, turnover=None):
    """
    竞价标的评级 S/A/B/C，直接绑定仓位。

    评估维度（5个）：
        D1 竞价偏离：实际竞价 vs 板块均值的偏离
        D2 量能（VR）：动态合格线（按板位）
        D3 断板风险：断板风险列表
        D4 ML置信度：XGBoost概率
        D5 历史封板率：limit_up_suc_rate

    返回：
        tier: 'S'/'A'/'B'/'C'
        position: 建议仓位（百分比）
        veto_reasons: 否决原因列表（空=可入场）
        details: {dim: (ok/susp/warn) for each dimension}
    """
    dz_risks = dz_risks or []
    details = {}
    veto_reasons = []
    warnings = []

    # ---- D1: 一字板形态（联合封板率判断）----
    # 一字板高开是否危险，看封板率：封板率高=市场认可，封板率低=抛压大致命
    if zt_yesterday and auction_chng is not None and auction_chng > 5:
        # 封板率<70%：市场不认可，高开是主力诱多 → warn
        # 封板率70-85%：有一定认可 → susp（严格控仓）
        # 封板率≥85%：强势封板，高开合理 → ok
        suc_rate = limit_up_suc_rate if limit_up_suc_rate is not None else 0.5
        if suc_rate < 0.70:
            details["D1_一字板"] = "warn"
            veto_reasons.append(f"一字板高开{auction_chng:.0f}%→封板率{suc_rate:.0%}<70%→抛压大致命")
        elif suc_rate < 0.85:
            details["D1_一字板"] = "susp"
            warnings.append(f"一字板高开：封板率{suc_rate:.0%}<85%，严格控仓")
        else:
            details["D1_一字板"] = "ok"

    # ---- D1: 竞价偏离（无板块均值数据，用绝对阈值）----
    if auction_chng is not None:
        if auction_chng < 0:
            details["D1_竞价"] = "warn"
            veto_reasons.append(f"竞价低开{auction_chng:+.1f}%→断板!")
        elif auction_chng > 8:
            details["D1_竞价"] = "susp"
            warnings.append(f"竞价高开{auction_chng:.0f}%→过热慎入")
        elif auction_chng > 5:
            details["D1_竞价"] = "ok"
        else:
            details["D1_竞价"] = "ok"
    else:
        details["D1_竞价"] = "susp"
        warnings.append("竞价数据缺失")

    # ---- D2: VR量能（动态阈值按板位+盘口）----
    if vr is not None:
        if lb >= 5:
            vr_threshold = 0.80
        elif lb >= 3:
            vr_threshold = 1.00
        elif lb >= 1:
            vr_threshold = 1.20
        else:
            vr_threshold = 1.00

        if vr < vr_threshold:
            details["D2_VR"] = "warn"
            veto_reasons.append(f"VR={vr:.2f}<{vr_threshold}量能不足")
        elif vr < vr_threshold * 1.5:
            details["D2_VR"] = "ok"
        else:
            details["D2_VR"] = "ok"  # 放量也正常
    else:
        details["D2_VR"] = "susp"

    # ---- D3: 断板风险列表 ----
    high_risk_keywords = ["一字板高开", "RSI超买", "下降通道", "MACD空头", "断板>"]
    severe_risk = [r for r in dz_risks if any(k in r for k in ["一字板高开", "下降通道+MACD", "断板>60"])]
    moderate_risk = [r for r in dz_risks if any(k in r for k in high_risk_keywords) and not any(k in r for k in severe_risk)]

    if severe_risk:
        details["D3_断板风险"] = "warn"
        veto_reasons.extend(severe_risk)
    elif moderate_risk:
        details["D3_断板风险"] = "susp"
        warnings.extend(moderate_risk)
    else:
        details["D3_断板风险"] = "ok"

    # ---- D4: ML置信度（按板位分级）----
    # 板位越高，ML置信度要求越高（高板位容错低）
    if ml_prob is not None:
        if lb >= 4:
            ml_thresh = 0.60   # 4板+：维持60%
        elif lb >= 3:
            ml_thresh = 0.55   # 3板：55%
        else:
            ml_thresh = 0.50   # 1-2板：50%（下调，避免2板误杀）
        
        if ml_prob >= 0.80:
            details["D4_ML"] = "ok"
        elif ml_prob >= ml_thresh:
            details["D4_ML"] = "susp"
        else:
            details["D4_ML"] = "warn"
            veto_reasons.append(f"ML概率{ml_prob:.0%}<{ml_thresh:.0%}→置信度不足")
    else:
        details["D4_ML"] = "susp"

    # ---- D5: 历史封板率 ----
    if limit_up_suc_rate is not None:
        if limit_up_suc_rate >= 0.90:
            details["D5_封板率"] = "ok"
        elif limit_up_suc_rate >= 0.70:
            details["D5_封板率"] = "susp"
        else:
            details["D5_封板率"] = "warn"
            warnings.append(f"历史封板率{limit_up_suc_rate:.0%}<70%")
    else:
        details["D5_封板率"] = "susp"

    # ---- 综合评级（提前计算，用于后续判断）----
    warn_count = sum(1 for v in details.values() if v == "warn")
    susp_count = sum(1 for v in details.values() if v == "susp")

    # ── S级严格条件：3板+，竞价0-3%，封板率≥85%，换手≥2%，量比≥5，warn=0，susp≤1 ──
    is_strict_s = (
        lb >= 3
        and auction_chng is not None
        and 0 <= auction_chng <= 3
        and limit_up_suc_rate is not None
        and limit_up_suc_rate >= 0.85
        and not any("RSI" in r or "超买" in r for r in dz_risks)
        and turnover is not None
        and turnover >= 2.0
        and vr is not None
        and vr >= 5.0
        and warn_count == 0
        and susp_count <= 1
    )

    if veto_reasons or warn_count >= 2:
        tier = "C"
        position = 0  # 放弃
    elif is_strict_s:
        tier = "S"
        position = 1.00  # 严格S级
    elif warn_count == 1 or susp_count >= 2:
        tier = "B"
        position = 0.30  # 轻仓
    elif susp_count == 1:
        tier = "A"
        position = 0.50
    else:
        tier = "B"
        position = 0.30

    # 市场阶段加成（仅调整A级/B级的phase因子，不改仓位上限）
    if phase in ("退潮", "恐慌") and tier in ("S", "A"):
        tier = f"{tier}*"
    elif phase == "主升" and tier in ("S", "A"):
        tier = f"{tier}+"

    # ── 退潮期高位板强制否决 ──
    if phase in ("退潮", "恐慌") and lb >= 3 and tier not in ("C",):
        tier = "C"
        position = 0
        veto_reasons.append(f"退潮期{lb}板→晋级率低→放弃")

    return {
        "tier": tier.rstrip("+-"),
        "tier_ext": tier,  # 带修饰符
        "position": round(position, 2),
        "veto_reasons": veto_reasons,
        "warnings": warnings,
        "details": details,
    }


def fmt_auction_tier(tier_info, code, name):
    """格式化评级输出"""
    t = tier_info["tier_ext"]
    pos = tier_info["position"]
    veto = tier_info["veto_reasons"]
    warns = tier_info["warnings"]

    if t.startswith("C"):
        label = f"🔴C级(放弃)"
    elif t.startswith("B"):
        label = f"🟡B级(轻仓{pos:.0%})"
    elif t.startswith("A"):
        label = f"🟡A级(半仓{pos:.0%})"
    elif t.startswith("S"):
        label = f"🟢S级(正常{pos:.0%})"
    else:
        label = f"⚪{t}级"

    lines = [f"  {label} | {code} {name}"]
    for v in veto[:2]:
        lines.append(f"    🔇{v}")
    for w in warns[:2]:
        lines.append(f"    ⚠️{w}")

    return "\n".join(lines)
