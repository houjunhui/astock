#!/usr/bin/env python3
"""
auto_buy.py - 竞价结束后自动执行买入
用法: python3 auto_buy.py [date]

逻辑:
1. 读取昨日ladder → 筛选有效候选
2. 获取今日9:25竞价数据
3. 跑 auction_tier 评级
4. B级及以上 → 自动计算仓位并记录持仓
5. 发送飞书报告
"""

import sys
import os
from datetime import datetime, date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astock.quicktiny import get_ladder, get_auction_for_codes, get_market_overview_fixed
from astock.position import (
    init_files, add_position, load_portfolio,
    calc_position, calc_stop_loss, calc_target
)
from astock.auction import auction_tier


CAPITAL = 1_000_000  # 100万模拟资金


def prev_trading_day(date_str):
    """简单取前一天"""
    d = datetime.strptime(date_str, "%Y%m%d")
    return (d - timedelta(days=1)).strftime("%Y%m%d")


def get_market_phase(date_str):
    """根据温度判断市场阶段"""
    try:
        mo = get_market_overview_fixed(date_str)
        temp = mo.get("market_temperature", 50)
        if temp >= 80:
            return "主升", temp
        elif temp >= 60:
            return "发酵", temp
        elif temp >= 40:
            return "启动", temp
        elif temp >= 20:
            return "退潮", temp
        elif temp >= 5:
            return "冰点", temp
        else:
            return "恐慌", temp
    except:
        return "主升", 50


def auto_buy(date_str):
    """
    自动执行买入
    返回: (买入记录列表, 报告文本)
    """
    today_str = date_str.replace("-", "")
    yday_str = prev_trading_day(today_str)

    # 1. 获取昨日ladder
    ladder_y = get_ladder(yday_str)
    y_stocks = {}
    for b in ladder_y.get("boards", []):
        for s in b.get("stocks", []):
            y_stocks[s["code"]] = {
                "level": b.get("level"),
                "name": s.get("name"),
                "industry": s.get("industry"),
                "limit_up_type": s.get("limit_up_type"),
                "limit_up_suc_rate": s.get("limit_up_suc_rate"),
            }

    # 2. 竞价数据
    ads = get_auction_for_codes(list(y_stocks.keys()), delay=0)

    # 3. 市场阶段
    phase, temp = get_market_phase(today_str)

    # 4. 加载已有持仓（避免重复买入）
    existing = load_portfolio()
    existing_codes = {p["code"] for p in existing if p.get("status") in ("持仓", "持仓中")}

    # 5. 遍历候选，评级
    candidates = []
    for code, yd in y_stocks.items():
        ad = ads.get(code, {})
        chg = ad.get("changeRate", 0)
        price = ad.get("price", 0)
        preClose = ad.get("preClose", 0)
        vr = ad.get("volumeRatio", 0)

        if chg <= 0:
            continue  # 竞价低开/平，跳过

        # 已在仓，跳过
        if code in existing_codes:
            continue

        lb = yd.get("level", 1)
        jb_prob = (yd.get("limit_up_suc_rate") or 0.5) * 100
        name = yd.get("name", code)

        # 评级
        tier_info = auction_tier(
            code=code, name=name, lb=lb, jb_prob=jb_prob,
            vr=vr, auction_chng=chg, phase=phase,
            zt_yesterday=(yd.get("limit_up_type") == "一字板"),
            limit_up_suc_rate=yd.get("limit_up_suc_rate")
        )

        candidates.append({
            "code": code, "name": name, "lb": lb,
            "chg": chg, "price": price, "vr": vr,
            "tier": tier_info["tier"],
            "position_pct": tier_info["position"],
            "tier_info": tier_info,
        })

    # 6. 排序：S>A>B>C
    tier_order = {"S": 0, "A": 1, "B": 2}
    candidates.sort(key=lambda x: tier_order.get(x["tier"], 3))

    # 7. 依次买入（直到资金用完或A级以上买完）
    buys = []
    used_pct = sum(float(p.get("capital_pct", 0)) for p in existing)
    max_total = 0.90 if phase in ("主升", "发酵") else 0.70

    for c in candidates:
        if c["tier"] == "C" or c["position_pct"] == 0:
            continue
        if used_pct >= max_total:
            break  # 资金用完

        tier = c["tier"]
        buy_price = c["price"]
        name = c["name"]
        code = c["code"]
        lb = c["lb"]
        chg = c["chg"]

        # 仓位：基础仓位 × 阶段系数，单只上限30%，总上限90%
        tier_pos = {"S": 1.0, "A": 0.50, "B": 0.30, "C": 0.0}.get(tier, 0)
        phase_factor = {"主升": 1.2, "发酵": 1.0, "启动": 0.85, "退潮": 0.50, "冰点": 0.30, "恐慌": 0.0}.get(phase, 0.7)
        raw_pct = tier_pos * phase_factor

        # 同只股票最高30%仓位
        raw_pct = min(raw_pct, 0.30)
        # 总仓位不超过上限，剩余资金
        remaining_pct = max_total - used_pct
        suggest_pct = min(raw_pct, remaining_pct)

        if suggest_pct < 0.05:
            continue  # 剩余资金不足5%

        suggest_amount = int(CAPITAL * suggest_pct / 100) * 100
        qty = int(suggest_amount / buy_price / 100) * 100
        lot_size = qty // 100

        if qty == 0:
            continue

        stop_loss = calc_stop_loss(buy_price)
        target = calc_target(buy_price)

        # 买入方式
        if chg <= 3:
            method = "竞价买入"
        elif chg <= 7:
            target_chg = chg * 0.7
            method = f"等回调至+{target_chg:.0f}%"
        else:
            method = f"等回调至+{chg*0.75:.0f}%"

        # 记录
        add_position(
            code=code, name=name,
            buy_price=buy_price, qty=lot_size * 100,
            capital_pct=suggest_pct,
            stop_loss=stop_loss, target_price=target,
            buy_method=method,
            notes=f"自动买入 | {phase} | 竞价+{chg:.2f}%"
        )

        buys.append({
            **c,
            "position_pct": suggest_pct,
            "buy_price": buy_price,
            "stop_loss": stop_loss,
            "target": target,
            "amount": suggest_amount,
            "lot_size": lot_size,
            "method": method,
        })

        used_pct += suggest_pct

    return buys, phase, temp, today_str


def format_report(buys, phase, temp, date_str):
    """格式化买入报告"""
    date_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    lines = [
        f"【🤖 自动模拟交易】{date_fmt} 09:31",
        f"市场阶段: {phase} | 温度: {temp:.1f}",
        "",
    ]

    if not buys:
        lines.append("无合格标的，未执行买入")
        return "\n".join(lines)

    total_pct = sum(b["position_pct"] for b in buys)
    total_amt = sum(b["amount"] for b in buys)
    lines.append(f"已自动买入: {len(buys)}只 | 总仓位: {total_pct*100:.0f}% = {total_amt/10000:.0f}万")
    lines.append("")

    for i, b in enumerate(buys, 1):
        lines.append(f"🟡{i} {b['name']}({b['code']}) {b['lb']}板")
        lines.append(f"   竞价+{b['chg']:.2f}% | 评级:{b['tier']}级 | 仓位:{b['position_pct']*100:.0f}%")
        lines.append(f"   买入: {b['buy_price']}元 ({b['lot_size']}手={b['amount']}元)")
        lines.append(f"   止损: {b['stop_loss']} | 目标: {b['target']}")
        lines.append(f"   方式: {b['method']}")
        lines.append("")

    lines.append("⚠️ 模拟交易，仅供验证策略使用")
    return "\n".join(lines)


if __name__ == "__main__":
    import os as _os
    _os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    date_arg = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    date_str = date_arg.replace("-", "")

    buys, phase, temp, date_str = auto_buy(date_arg)
    report = format_report(buys, phase, temp, date_str)
    print(report)

