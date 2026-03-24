"""
astock.predict_calibrated
基于26228样本历史数据的三重校准预测模块

Item ①: 概率分档校准（jb_prob_raw → 真实晋级率）
Item ②: 续涨率建模（各区间实际续涨率）
Item ③: 断板预警（RSI>75为主要风险信号）
Item ④: Phase系数（从数据反推）
Item ⑤: 5板+妖股续涨模式
"""
import os, json
from config import BASE_PROBS, PHASE_BASE_DISCOUNT

# 加载校准表
_CAL_PATH = os.path.join(os.path.dirname(__file__), "calibration_table.json")
try:
    _CAL = json.load(open(_CAL_PATH)) if os.path.exists(_CAL_PATH) else {}
except Exception:
    _CAL = {}

PROB_CAL = _CAL.get("prob_cal", {})
XX_CAL = _CAL.get("xx_cal", {})
LB_CAL = _CAL.get("lb_cal", {})

# Item ①: 预测概率 → 实际晋级率查表
_JB_MAP = {
    (0, 5):   19.4,
    (5, 10):  19.9,
    (10, 15): 25.8,
    (15, 20): 39.2,
    (20, 25): 33.9,
    (25, 30): 34.3,
    (30, 100): 41.1,
}

# Item ②: 续涨率查表
_XX_MAP = {
    (0, 15):  63.0,   # 0-15%: 基准续涨率 ~63%
    (15, 20): 52.7,   # 15-20%: 续涨率略降
    (20, 100): 56.0,  # 20%+: 续涨率 ~56%
}

# Item ④: Phase真实晋级率（从26228样本反推）
_PHASE_ACTUAL = {
    # 样本中无法区分phase，用退潮期样本推断
    # 退潮期晋级率约20-25%，冰点更低
    "退潮": 0.55,  # 折扣55%（原80%太宽松）
    "冰点": 0.50,
    "启动": 0.75,
    "发酵": 0.85,
    "稳定": 0.80,
}

# Item ⑤: 5板+妖股参数（30样本：40%晋级/76%续涨/0%断板）
YAO_LB_THRESHOLD = 5
YAO_JJ_RATE = 0.40   # 5板+晋级率40%
YAO_XX_RATE = 0.60   # 5板+续涨率60%
YAO_DISCOUNT = 1.0   # 妖股不受普通规则约束


def calibrate_jb_prob(raw_jb, lb):
    """
    Item ①: 把模型原始输出(raw_jb)映射为真实历史晋级率
    同时受连板数(lb)影响：
    - 1板: 样本25607，真实晋级率22.6%
    - 2板: 样本355，真实晋级率22.3%
    - 3板: 样本209，真实晋级率21.5%（3板模型最准）
    - 4板: 样本25，真实晋级率12.0%（样本少）
    - 5板+: 40%晋级（妖股模式）
    """
    if lb >= YAO_LB_THRESHOLD:
        return YAO_JJ_RATE

    # 找对应区间
    for (lo, hi), actual in sorted(_JB_MAP.items()):
        if lo <= raw_jb < hi:
            return actual / 100.0
    return raw_jb / 100.0  # 默认


def calc_xx_prob(raw_jb, lb):
    """
    Item ②: 计算续涨概率（续涨=次日上涨但未涨停）
    按板位分层建模（板位越高续涨越难）：
    - 1板:  ~64%（首板后资金参与度高）
    - 2板:  ~58%（加速期，继续上涨概率仍高）
    - 3板:  ~50%（分岐位，续涨率下降）
    - 4板:  ~40%（高位，续涨率明显降低）
    - 5板+: ~60%（妖股特殊，不受此约束）
    """
    xx_map = {
        1: 0.64,
        2: 0.58,
        3: 0.50,
        4: 0.40,
        5: 0.60,  # 5板及以上走妖股续涨模式
    }
    return xx_map.get(lb, 0.55)


def check_dz_risk(rsi, vr, lb, auction_chng, trend, macd_state, zt_yesterday=False):
    """
    Item ③: 断板风险检测（量化规则）

    核心发现（26228样本统计）：
    - RSI>75（超买）: 断板率22.0% vs基准+6.5pp → 主要预警信号
    - vr<0.5（极度缩量）: 断板率8.7% vs基准-6.8pp → 缩量反而安全
    - 一字板高开（昨日ZT+今高开>5%）: 断板率>60% → 独立高危形态

    规则：任意触发即标注，返回格式"触发规则→量化结论"
    """
    risks = []

    # 一字板高开：独立高危形态
    if zt_yesterday and auction_chng is not None and auction_chng > 5:
        risks.append(f"一字板高开→断板概率>60%")

    # RSI超买
    if rsi is not None and rsi > 75:
        risks.append(f"RSI超买{rsi:.0f}→断板率+6.5pp")

    # 高板位+超买双重风险（量化）
    if lb >= 4 and rsi is not None and rsi > 70:
        risks.append(f"{lb}板+RSI{rsi:.0f}→双重压力")

    # 趋势+MACD共振
    if trend == "下降通道" and macd_state == "MACD空头":
        risks.append("下降通道+MACD空头→共振看空")
    elif trend == "下降通道":
        risks.append("下降通道→谨慎")
    elif macd_state == "MACD空头":
        risks.append("MACD空头→谨慎")

    # 低开否决
    if auction_chng is not None and auction_chng < 0:
        risks.append(f"竞价低开{auction_chng:+.1f}%→断板!")

    return risks


def predict_stock_v2(code, lb, kl, phase=None, auction_chng=None, auction_bid=None,
                     zt_yesterday=False,
                     research_signal=None,
                     dragon_tiger_signal=None,
                     sector_signal=0,
                     hotlist_signal=0,
                     zt_detail=None):
    """
    校准版预测函数 v3.2（predict_stock_v2）

    悟道信号（①~⑤）：
        research_signal: dict or None，研报数据 {rating_signal: 1/0/-1}
        dragon_tiger_signal: dict or None，龙虎榜数据 {signal: 1/0/-1, is_institutional: bool}
        sector_signal: int，板块四象限信号 1=强势 -1=弱势 0=中性
        hotlist_signal: int，热榜情绪 1=活跃 0=一般 -1=冷清
        zt_detail: dict or None，涨停详细数据 {reason_type, industry, limit_up_suc_rate, ...}

    机器学习信号（⑥）：
        使用 XGBoost 模型（26,228样本训练）预测晋级/续涨概率，
        作为独立加成信号叠加到 calibrated_jb 上。

    返回字段：
        calibrated_jb_prob: 校准后真实晋级概率（查表）
        calibrated_xx_prob: 校准后续涨概率
        ml_prob: XGBoost 模型预测概率
        ml_confidence: 置信度标签
        dz_risks: 断板风险列表
        is_yao: 是否妖股模式（5板+）
        raw_jb: 原始未校准晋级概率
    """
    coef = None  # 使用默认系数

    cur = kl.get("last_close")
    ma20 = kl.get("ma20")
    ma60 = kl.get("ma60")
    rsi_val = kl.get("rsi")
    vr = kl.get("vr")
    dif = kl.get("dif")
    macd_val = kl.get("macd")
    trend = kl.get("trend", "震荡")
    macd_state = kl.get("macd_state", "")

    # === 原有 adj 计算（保持一致）===
    adj = _calc_adj(trend, vr, dif, macd_val, rsi_val, cur, ma20, ma60, lb)

    # === 原有 base（修正后）===
    base = BASE_PROBS.get(min(lb + 1, 5), 0.10)

    # === Item 4: Phase系数（真实数据校准）===
    phase_discount = 1.0
    if phase:
        phase_discount = _PHASE_ACTUAL.get(phase, 0.80)

    # === Item 1: 竞价数据修正 + 一字板识别 ===
    # 一字板形态：昨日涨停 + 今日高开 → 断板概率极高
    # 量化规则：昨日ZT + 竞价涨幅>5% → 晋级概率降至<5%
    is_yz = zt_yesterday and auction_chng is not None and auction_chng > 5

    # 复苏/主升期：一字板是强势延续信号，不应压低概率
    strong_phase = (phase in ("复苏", "主升", "稳定")) if phase else False

    auction_signal = ""
    if is_yz:
        if strong_phase:
            # 复苏/主升期：一字板是正常延续，小幅加分
            adj = min(adj * 1.30, 1.5)
            auction_signal = f"✅一字板(昨ZT+今高开{auction_chng:+.1f}%)→强势延续!"
        else:
            # 退潮/冰点期：一字板高开是危险信号
            adj = 0.05
            auction_signal = f"⚠️一字板(昨ZT+今高开{auction_chng:+.1f}%)→断板风险极高!"
    elif auction_chng is not None:
        if auction_chng < 0:
            # 低开：晋级概率归零
            adj = 0
            auction_signal = f"竞价低开{auction_chng:+.1f}%→断板!"
        elif auction_chng > 8:
            # 过热高开：降低预期（竞价已充分反映上涨）
            adj = min(adj * 0.85, 0.6)
            auction_signal = f"竞价高开{auction_chng:+.1f}%过热⚠️"
        elif auction_chng > 5:
            # 温和高开：轻微上调
            adj = min(adj * 1.15, 1.4)
            auction_signal = f"竞价高开{auction_chng:+.1f}%✅"
        else:
            auction_signal = f"竞价{auction_chng:+.1f}%"

        if auction_bid is not None and auction_bid < 500:
            adj *= 0.75
            auction_signal += " | 封单不足<500万⚠️"
    else:
        auction_signal = "竞价数据缺失"

    # === Item 2: 高板位风控（已有）===
    lb_risk = 1.0
    lb_signal = ""
    if lb >= 4:
        lb_risk = 0.50
        lb_signal = f"{lb}板高风险×{lb_risk}"
    elif lb == 3:
        lb_risk = 0.75
        lb_signal = f"3板分岐×{lb_risk}"
    else:
        lb_signal = f"{lb}板正常"

    # === 计算 raw_jb_prob（未校准）===
    if auction_chng is None or auction_chng >= 0:
        raw_jb = min(base * adj * phase_discount * lb_risk, 0.70)
    else:
        raw_jb = 0

    raw_jb_pct = round(raw_jb * 100, 1)
    raw_dz_pct = round(raw_jb * 0.5 * 100, 1)

    # === Item ①+②: 动态分库校准（calibration_v2）===
    # 替换原来的 calibrate_jb_prob + calc_xx_prob 固定分桶
    try:
        from calibration_v2 import calibrate as dynamic_calibrate
        cal_result = dynamic_calibrate(raw_jb_pct, lb, phase=phase or "通用")
        calibrated_jb = cal_result["calibrated_jb"]
        calibrated_xx = cal_result["calibrated_xx"]
        calibrated_dp = cal_result.get("calibrated_dp", 1 - calibrated_jb - calibrated_xx)
        phase_used = cal_result["phase"]
        phase_ratio = cal_result.get("phase_ratio", 1.0)
        cal_source = cal_result.get("source", "")
    except Exception:
        # 降级：使用原始固定分桶
        calibrated_jb = calibrate_jb_prob(raw_jb_pct, lb)
        calibrated_xx = calc_xx_prob(raw_jb_pct, lb)
        calibrated_dp = 1 - calibrated_jb - calibrated_xx
        phase_used = phase or "通用"
        phase_ratio = 1.0
        cal_source = "原始分桶(降级)"

    # === Item ③: 断板风险检测 ===
    dz_risks = check_dz_risk(rsi_val, vr, lb, auction_chng, trend, macd_state, zt_yesterday)
    dz_warn = " | ".join(dz_risks) if dz_risks else ""

    # === 熔断机制：识别恐慌期 / 极端断板市场 ===
    # 如果近期样本（近30天）晋级/续涨率 < 65%，触发熔断
    circuit_broken = False
    if phase_ratio < 0.80:
        circuit_broken = True

    # === Item ⑤: 妖股模式 ===

    # === 变量安全初始化（所有路径必须定义）===
    ml_prob = None
    ml_confidence = ""
    is_yao = (lb >= YAO_LB_THRESHOLD)
    if is_yao:
        calibrated_jb = YAO_JJ_RATE
        calibrated_xx = YAO_XX_RATE
        lb_signal = f"妖股模式({lb}板)"

    # === 悟道信号加成（①~⑤）===
    # 应用在 calibrated_jb 上（已是0-1范围）
    wudao_signals = []
    if not is_yao:  # 妖股模式独立，不受加成干扰
        # ① 研报基本面加成
        if research_signal and research_signal.get("rating_signal"):
            rs = research_signal["rating_signal"]
            calibrated_jb += rs * 0.05  # 买入+5pp，减持-5pp
            wudao_signals.append(f"研报{research_signal.get('rating','')}")
        # ② 龙虎榜席位信号
        if dragon_tiger_signal and dragon_tiger_signal.get("signal"):
            ds = dragon_tiger_signal["signal"]
            calibrated_jb += ds * 0.08  # 机构净买入+8pp，机构净卖出-8pp
            seat_type = "机构" if dragon_tiger_signal.get("is_institutional") else "游资"
            wudao_signals.append(f"龙虎榜({seat_type}){ds:+d}")
        # ③ 板块四象限信号
        if sector_signal == 1:
            calibrated_jb += 0.05  # 强势板块+5pp
            wudao_signals.append("板块强势+5pp")
        elif sector_signal == -1:
            calibrated_jb -= 0.05  # 弱势板块-5pp
            wudao_signals.append("板块弱势-5pp")
        # ④ 资金流向（已降级，返回None跳过）
        # ⑤ 热榜情绪信号
        if hotlist_signal == 1:
            calibrated_jb += 0.03  # 市场活跃+3pp
            wudao_signals.append("市场热度高+3pp")
        elif hotlist_signal == -1:
            calibrated_jb -= 0.03  # 市场冷清-3pp
            wudao_signals.append("市场热度低-3pp")
        # ⑤' 涨停详细数据加成（历史封板率高→+5pp）
        if zt_detail:
            lusr = zt_detail.get("limit_up_suc_rate", 0)
            if lusr >= 0.95:
                calibrated_jb += 0.05
                wudao_signals.append(f"历史封板率{lusr:.0%}✅")
            elif lusr < 0.70:
                calibrated_jb -= 0.05
                wudao_signals.append(f"历史封板率{lusr:.0%}⚠️")

        # === ⑥ XGBoost ML 模型概率加成 ===
        ml_prob = None
        ml_confidence = ""
        try:
            from model_predict import predict_ml, ml_confidence_label
            kl_ml = dict(kl)
            kl_ml["jb_prob"] = raw_jb_pct
            ml_prob = predict_ml(code, lb, kl_ml)
            if ml_prob is not None:
                calibrated_jb = calibrated_jb * 0.80 + ml_prob * 0.20
                ml_confidence = ml_confidence_label(ml_prob)
                wudao_signals.append(f"ML({ml_prob:.0%}){ml_confidence}")

                # === 断板交叉验证（替代独立断板模型）===
                # ML认为续涨，但规则提示断板风险 → 矛盾，高风险
                ml_dp_prob = 1 - ml_prob
                rule_dp_estimate = calibrated_dp  # 从动态校准来
                inconsistency = abs(ml_dp_prob - rule_dp_estimate)
                if inconsistency > 0.30 and ml_dp_prob > 0.30:
                    # 矛盾超过30%且ML认为断板概率高
                    dz_risks.append(f"⚠️规则/ML断板信号矛盾({inconsistency:.0%})→高风险")
        except Exception:
            pass

        # clamp 到合理范围
        calibrated_jb = max(0.01, min(calibrated_jb, 0.95))

    # === 分布计算 ===
    dist = _calc_distribution(lb, adj)
    signal = ', '.join(_build_signal(kl))

    # === 断板率：与晋级率联动 ===
    # 核心逻辑：断板 = 未晋级。正常情况 dz = 1 - jb。
    # 一字板高开是独立高危形态，规则override（65%），同时压制jb到最低。
    # 妖股特殊基因，dz=0。
    if is_yao:
        jb_val = calibrated_jb
        dz_val = 0.0
    elif zt_yesterday and auction_chng is not None and auction_chng > 5:
        # 一字板高开：规则 override，jb 压到 5%（博弈空间极小）
        jb_val = 0.05
        dz_val = 0.65
    else:
        jb_val = calibrated_jb
        dz_val = max(0.0, 1.0 - jb_val)

    return {
        "jb_prob": round(jb_val * 100, 1),
        "xx_prob": 0,
        "dz_prob": round(dz_val * 100, 1),
        "base_prob": round(base * 100, 1),
        "adj_factor": round(adj, 2),
        "phase_discount": phase_discount,
        "distribution": dist,
        "signal": signal,
        # 悟道信号（①~⑤）
        "wudao_signals": wudao_signals,
        # ML模型信号（⑥）
        "ml_prob": ml_prob,
        "ml_confidence": ml_confidence,
        # 动态校准信息
        "phase_used": phase_used,
        "phase_ratio": phase_ratio,
        "cal_source": cal_source,
        # 熔断
        "circuit_broken": circuit_broken,
        # 断板交叉验证（已追加到 dz_risks）
        "dz_risks": dz_risks,
        "dz_warn": " | ".join(dz_risks) if dz_risks else dz_warn,
        "auction_signal": auction_signal,
        "lb_signal": lb_signal,
        "auction_chng": auction_chng,
        "auction_bid": auction_bid,
        "calibrated_jb_prob": round(calibrated_jb * 100, 1),
        "calibrated_xx_prob": round(calibrated_xx * 100, 1),
        "is_yao": is_yao,
        "raw_jb_prob": raw_jb_pct,
    }


# ============================================================
# 以下复制自 predict.py（保持内部逻辑一致）
# ============================================================

def _calc_adj(trend, vr, dif, macd_val, rsi_val, cur, ma20, ma60, lb):
    """
    计算综合调整系数（不含板位因子，板位由 lb_risk 单独处理）
    """
    adj = 1.0
    if trend == "上升通道":
        adj *= 1.25
    elif trend == "下降通道":
        adj *= 0.70
    else:  # 震荡
        adj *= 0.95

    if vr is not None:
        if vr > 2.0:
            adj *= 1.30
        elif vr > 1.5:
            adj *= 1.15
        elif vr < 0.5:
            adj *= 0.80
        elif vr < 0.8:
            adj *= 0.90

    if rsi_val is not None:
        if rsi_val > 80:
            adj *= 0.70
        elif rsi_val > 75:
            adj *= 0.80
        elif rsi_val < 30:
            adj *= 0.80
        elif rsi_val < 40:
            adj *= 0.90

    # macd_val 即 DIF-DE 值（MACD柱状图）
    if macd_val is not None:
        if macd_val > 0:
            adj *= 1.10
        else:
            adj *= 0.90

    # 板位因子已移至 lb_risk，避免双重折扣
    return round(adj, 2)


def _calc_distribution(lb, adj):
    """晋级续涨分布"""
    dist = {}
    next_lb = lb + 1
    next2_lb = lb + 2
    base_next = BASE_PROBS.get(min(next_lb, 5), 0.10)
    base_next2 = BASE_PROBS.get(min(next2_lb, 5), 0.05)
    p_continue = min(base_next * adj * 0.7, 0.80)
    p_jb = base_next * adj
    total = p_continue + p_jb + 0.05
    dist[1] = round((1 - total) * 100, 1)
    dist["N+1"] = round(p_jb * 100, 1)
    dist["继续"] = round(p_continue * 100, 1)
    return dist


def _build_signal(kl):
    """构建信号列表"""
    sigs = []
    if kl.get("trend") == "上升通道":
        sigs.append("上升通道")
    elif kl.get("trend") == "下降通道":
        sigs.append("下降通道")
    if kl.get("rsi"):
        if kl["rsi"] > 75:
            sigs.append("RSI超买")
        elif kl["rsi"] < 40:
            sigs.append("RSI超卖")
    if kl.get("macd_state") == "MACD多头":
        sigs.append("MACD多头")
    vol = kl.get("vol_status", "")
    if vol:
        sigs.append(vol)
    if kl.get("price_vs_ma20") == "MA20上方":
        sigs.append("MA20上方")
    return sigs
