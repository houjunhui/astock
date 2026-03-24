"""
astock.predict
预测核心 - 晋级概率计算 + 信号生成
"""
from config import BASE_PROBS, DEFAULT_COEF, PHASE_BASE_DISCOUNT


def load_coef():
    """加载系数配置（可后续扩展为文件持久化）"""
    return dict(DEFAULT_COEF)


def calc_adj(trend, vr, dif, macd_val, rsi_val, cur, ma20, ma60, lb, coef=None):
    """
    计算综合调整系数。
    所有因子相乘得到总系数。
    """
    if coef is None:
        coef = load_coef()

    f = 1.0
    f *= coef.get(trend, 1.0)

    if vr is not None:
        if vr < 0.5:
            f *= coef.get('vr_<0.5', 1.0)
        elif vr < 0.8:
            f *= coef.get('vr_<0.8', 1.0)
        elif vr < 1.0:
            f *= coef.get('vr_<1.0', 1.0)
        elif vr < 1.5:
            f *= coef.get('vr_<1.5', 1.0)
        else:
            f *= coef.get('vr_>=1.5', 1.0)

    if rsi_val is not None:
        if rsi_val < 40:
            f *= coef.get('rsi_<40', 1.0)
        elif rsi_val < 50:
            f *= coef.get('rsi_<50', 1.0)
        elif rsi_val < 60:
            f *= coef.get('rsi_<60', 1.0)
        elif rsi_val < 70:
            f *= coef.get('rsi_<70', 1.0)
        else:
            f *= coef.get('rsi_>=70', 1.0)

    dif_de_cross = None
    if dif is not None and macd_val is not None:
        dif_de_cross = '底部金叉' if (dif > 0 and macd_val > 0) else ('顶部死叉' if (dif < 0 and macd_val < 0) else None)
    if dif_de_cross:
        f *= coef.get(dif_de_cross, 1.0)

    if cur is not None and ma20 is not None:
        f *= coef.get('ma20上方', 1.0) if cur > ma20 else coef.get('ma20下方', 1.0)

    lb_key = {1: '首板', 2: '2板', 3: '3板', 4: '4板', 5: '5板+'}.get(lb, '首板')
    f *= coef.get(lb_key, 1.0)

    return max(0.1, min(f, 3.0))


def build_signal(kl):
    """从K线指标生成信号描述列表"""
    signals = []
    if kl.get('trend') == '上升通道':
        signals.append('上升通道')
    elif kl.get('trend') == '下降通道':
        signals.append('下降通道')
    if kl.get('rsi'):
        if kl['rsi'] < 40:
            signals.append('RSI超卖')
        elif kl['rsi'] > 70:
            signals.append('RSI超买')
        elif kl['rsi'] < 50:
            signals.append('RSI偏弱')
    if kl.get('macd_state'):
        signals.append(kl['macd_state'])
    if kl.get('vol_status') in ('极度缩量', '温和缩量'):
        signals.append('缩量')
    if kl.get('vol_status') == '极度缩量':
        signals.append('极度缩量')
    if kl.get('price_vs_ma20') == 'MA20上方':
        signals.append('MA20上方')
    return signals


def calc_distribution(lb, adj):
    """
    计算N板/N+1板/N+2板的晋级概率分布。
    adj是综合调整系数。
    """
    next_lb = min(lb + 1, 5)
    next2_lb = min(lb + 2, 5)

    base_next = BASE_PROBS.get(next_lb, 0.10)
    base_next2 = BASE_PROBS.get(next2_lb, 0.05)

    # 调整后概率（有上限）
    p_continue = min(base_next * adj * 0.7, 0.80)
    p_jb = base_next * adj
    p_jb = min(p_jb, 0.70)

    return {
        1: round((1 - p_jb) * 100, 1),
        2: round(p_jb * 50, 1),
        3: round(p_jb * 30, 1),
        4: round(p_jb * 15, 1),
        5: round(p_jb * 5, 1),
    }


def predict_stock(code, lb, kl, coef_override=None, phase=None,
                  auction_chng=None, auction_bid=None):
    """
    给定股票代码、连板数、K线数据，返回预测结果dict。

    竞价修正（Item 1）：
        auction_chng: 竞价涨幅%（来自 quicktiny /auction 接口 changeRate 字段）
        auction_bid : 竞价封单额（万元），用于判断封单是否充足
        - 高开 > 6%：情绪过热，adj上调但次日炸板风险大
        - 低开 < 0%：直接断板，jb_prob → 0
        - 平开小幅高开（0~3%）：正常晋级概率
        - 竞价封单 < 500万：封单不足，风险系数×0.7

    高板位风控（Item 2）：
        3板+实际晋级率仅21.5%（vs 1板22.6%），
        原因：3板为分歧位，4板+为龙头孤岛。
        - 3板：晋级概率×0.75（+adj仍有效）
        - 4板+：晋级概率×0.50
    """
    coef = coef_override or load_coef()

    cur = kl.get('last_close')
    ma20 = kl.get('ma20')
    ma60 = kl.get('ma60')
    rsi_val = kl.get('rsi')
    vr = kl.get('vr')
    dif = kl.get('dif')
    macd_val = kl.get('macd')

    adj = calc_adj(
        kl.get('trend', '震荡'), vr, dif, macd_val, rsi_val,
        cur, ma20, ma60, lb, coef
    )

    base = BASE_PROBS.get(min(lb + 1, 5), 0.10)

    # 情绪周期折扣
    discount = 1.0
    if phase:
        discount = PHASE_BASE_DISCOUNT.get(phase, 1.0)

    # ===== Item 1: 竞价数据修正 =====
    auction_signal = ''
    if auction_chng is not None:
        if auction_chng < 0:
            # 低开：直接断板，晋级概率归零
            jb_prob = 0.0
            dz_prob = 0.0
            auction_signal = f'竞价低开{auction_chng:+.1f}%→断板!'
        elif auction_chng > 8:
            # 高开过热：晋级概率高但次日炸板风险极大
            # 只略微上调（情绪已过度）
            adj = min(adj * 1.1, 1.5)
            auction_signal = f'竞价高开{auction_chng:+.1f}%过热⚠️'
        elif auction_chng > 5:
            # 正常高开：轻微上调
            adj = min(adj * 1.15, 1.4)
            auction_signal = f'竞价高开{auction_chng:+.1f}%✅'
        else:
            auction_signal = f'竞价{auction_chng:+.1f}%'

        # 竞价封单不足修正
        if auction_bid is not None and auction_bid < 500:
            adj *= 0.75
            auction_signal += ' | 封单不足<500万⚠️'
    else:
        auction_signal = '竞价数据缺失'

    # ===== Item 2: 高板位风控系数 =====
    lb_risk = 1.0
    if lb >= 4:
        lb_risk = 0.50   # 4板+孤岛效应
        lb_signal = f'{lb}板高风险×{lb_risk}'
    elif lb == 3:
        lb_risk = 0.75  # 3板分歧位
        lb_signal = f'3板分岐×{lb_risk}'
    else:
        lb_signal = f'{lb}板正常'

    # 只有未被竞价否决的才继续计算
    if auction_chng is None or auction_chng >= 0:
        jb_prob = round(min(base * adj * discount * lb_risk, 0.70) * 100, 1)
        dz_prob = round(min(base * adj * 0.5 * discount * lb_risk, 0.30) * 100, 1)
    else:
        # 已被竞价修正归零，不再重复计算
        pass

    dist = calc_distribution(lb, adj)
    signal = ', '.join(build_signal(kl))

    return {
        'jb_prob': jb_prob,
        'dz_prob': dz_prob,
        'base_prob': round(base * 100, 1),
        'adj_factor': round(adj, 2),
        'discount': discount,
        'distribution': dist,
        'signal': signal,
        'auction_signal': auction_signal,
        'lb_signal': lb_signal,
        'auction_chng': auction_chng,
        'auction_bid': auction_bid,
    }
