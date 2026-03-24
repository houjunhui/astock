"""
astock.formatter
输出格式化 - 终端、飞书消息
"""

# ===================== 终端格式化 =====================

def fmt_predict_terminal(date, results, sector_raw, phase, phase_name, sentiment=''):
    """终端预测输出"""
    lines = [
        f"{'=' * 60}",
        f"  A股超短每日预测 v3.2  {date}",
        f"{'=' * 60}",
        f"",
        f"市场情绪: {sentiment if sentiment else phase_name}  涨停股: {len(results)}只",
        f"",
    ]

    # 板块效应
    if sector_raw:
        sorted_sectors = sorted(sector_raw.items(), key=lambda x: x[1]['total'], reverse=True)
        lines.append("  板块效应")
        lines.append("-" * 40)
        for ind, data in sorted_sectors:
            n = data['total']
            tag = '🚀板块联动' if n >= 3 else ('📌多股' if n == 2 else '')
            lines.append(f"  {ind[:12]:<12}{n:<6}{tag}")
        lines.append("")

    # 晋级分布
    gt30 = sum(1 for r in results if r['prediction']['jb_prob'] > 30)
    gt15 = sum(1 for r in results if 15 < r['prediction']['jb_prob'] <= 30)
    lt15 = len(results) - gt30 - gt15
    lines.append(f"晋级分布: >30%:{gt30}只 / 15-30%:{gt15}只 / <15%:{lt15}只")

    # 市场周期 & 熔断状态
    phases = set(r['prediction'].get('phase_used', '?') for r in results)
    cbroken = sum(1 for r in results if r['prediction'].get('circuit_broken'))
    if phases:
        lines.append(f"市场周期: {', '.join(sorted(phases))}" + (f" | 熔断触发:{cbroken}只" if cbroken else ""))
    lines.append("")

    return '\n'.join(lines)


def fmt_auction_mobile(results, sector_raw, phase="退潮", zt_yesterday_codes=None):
    """
    手机易读格式：分行输出，每行一个标的。
    zt_yesterday_codes: 昨日涨停股票代码集合，用于判断一字板形态
    """
    from auction import auction_ok, auction_tier, fmt_auction_tier
    yz_set = zt_yesterday_codes or set()

    lines = ["📋 明日竞价自检（重点6只）\n"]

    for r in results[:6]:
        p = r['prediction']
        n = sector_raw.get(r['industry'], {}).get('total', 0)
        sector_hot = (n >= 3)
        code = r['code']
        lb = r['lb']
        is_yz = code in yz_set
        last_close = r.get('kl', {}).get('last_close')

        ok_min, ok_max, vol, warn, stop_loss, take_profit, _ = auction_ok(
            code, r['name'], lb, p['jb_prob'],
            vr=r.get('vr'), sector_hot=sector_hot, phase=phase,
            auction_chng=p.get('auction_chng'),
            zt_yesterday=is_yz,
            last_close=last_close
        )

        sig = p.get('signal', '')
        auction_sig = p.get('auction_signal', '')
        lb_sig = p.get('lb_signal', '')
        sig_short = sig.split(',')[0] if sig else ''
        xx_prob = p.get('xx_prob', 0)
        dz_warn = p.get('dz_warn', '')
        is_yao = p.get('is_yao', False)
        yao_jj = p.get('jb_prob', 0)

        # === S/A/B/C 评级 ===
        tier_info = auction_tier(
            code=code, name=r['name'], lb=lb, jb_prob=p['jb_prob'],
            vr=r.get('vr'), auction_chng=p.get('auction_chng'),
            zt_yesterday=is_yz, phase=phase,
            dz_risks=p.get('dz_risks', []),
            ml_prob=p.get('ml_prob'),
            limit_up_suc_rate=r.get('zt_detail', {}).get('limit_up_suc_rate') if isinstance(r.get('zt_detail'), dict) else None
        )
        tier_tag = tier_info["tier_ext"]

        lines.append(f"● {r['name']}({code}) {tier_tag}")
        jb = p.get('jb_prob', 0)
        dz = p.get('dz_prob', 0)
        if is_yao:
            lines.append(f"  {lb}板 | 妖股晋级{yao_jj:.0f}% | 断板{dz:.0f}% | 竞价{ok_min}~{ok_max}% | {vol}")
        else:
            lines.append(f"  {lb}板 | 晋级{jb:.1f}% | 断板{dz:.0f}% | 竞价{ok_min}~{ok_max}% | {vol}")
        reason = r.get('reason', '')
        if reason:
            lines.append(f"  📌涨停原因: {reason}")
        if auction_sig and '缺失' not in auction_sig:
            lines.append(f"  🔍竞价修正: {auction_sig}")
        if lb_sig and '正常' not in lb_sig:
            lines.append(f"  ⚠️{lb_sig}")

        if stop_loss and take_profit:
            lines.append(f"  📍操作参考: 买竞价区间{ok_min}~{ok_max}% | 止损{stop_loss} | 持至涨停")
        # 悟道信号标签（①②③）
        wudao = p.get('wudao_signals', [])
        if wudao:
            lines.append(f"  🈯{' | '.join(wudao)}")
        lines.append(f"  {warn} | {sig_short}")
        lines.append("")

    # 次级
    others = [r for r in results[6:10]]
    if others:
        lines.append("📋 次级关注（按需参考）\n")
        for r in others:
            p = r['prediction']
            n = sector_raw.get(r['industry'], {}).get('total', 0)
            sector_hot = (n >= 3)
            code = r['code']
            is_yz = code in yz_set
            last_close = r.get('kl', {}).get('last_close')
            ok_min, ok_max, vol, warn, *_ = auction_ok(
                code, r['name'], r['lb'], p['jb_prob'],
                vr=r.get('vr'), sector_hot=sector_hot, phase=phase,
                auction_chng=p.get('auction_chng'),
                zt_yesterday=is_yz,
                last_close=last_close
            )
            lines.append(f"  {r['name']}({code}) {r['lb']}板 晋级{p.get('jb_prob',0):.0f}% 断板{p.get('dz_prob',0):.0f}% 竞价{ok_min}~{ok_max}% {vol}")

    return '\n'.join(lines)


def fmt_positions(conn):
    """持仓格式化"""
    rows = conn.execute(
        "SELECT * FROM positions WHERE status='持仓' ORDER BY created_at DESC"
    ).fetchall()
    if not rows:
        return "暂无持仓记录。"

    lines = [
        f"{'=' * 60}",
        f"  当前持仓",
        f"{'=' * 60}",
        f"{'代码':<8}{'名称':<10}{'买入日':<10}{'买入价':<10}{'现价':<10}{'浮盈%':<10}{'持仓天':<8}{'状态'}",
        f"{'-' * 70}",
    ]
    for r in rows:
        lines.append(
            f"{r['code']:<8}{r['name']:<10}{r['buy_date']:<10}"
            f"{r['buy_price']:<10.2f}{r['cur_price']:<10.2f}"
            f"{r['profit_pct']:<10.1f}{r['hold_days']:<8}{r['status']}"
        )
    return '\n'.join(lines)
