"""
每日完整策略报告生成器
整合：竞价选股 + 仓位计算 + 持仓监控 + 板块轮动 + 止损管理
"""

import sys; sys.path.insert(0, '.')
from astock.position import (
    init_files, add_position, update_positions,
    get_portfolio_status, format_position_report,
    calc_position, calc_stop_loss, calc_target,
    get_sector_momentum, compare_sector_momentum,
    format_sector_report
)
from astock.quicktiny import get_ladder, get_auction_for_codes


def generate_full_report(date_str, capital=1000000, phase="主升", market_temp=50):
    """
    生成每日完整策略报告
    """
    # 1. 获取昨日涨停池
    yday = prev_trading_day(date_str)
    ladder_y = get_ladder(yday)
    
    # 2. 建立昨日涨停查找表
    y_lookup = {}
    for b in ladder_y.get("boards", []):
        for s in b.get("stocks", []):
            y_lookup[s["code"]] = {
                "level": b.get("level"),
                "industry": s.get("industry"),
                "limit_up_type": s.get("limit_up_type"),
                "limit_up_suc_rate": s.get("limit_up_suc_rate"),
            }
    
    # 3. 昨日涨停股候选（含name）
    yday_codes = set(y_lookup.keys())
    yday_stocks = {}  # code -> {level, name, industry, ...}
    for b in ladder_y.get("boards", []):
        for s in b.get("stocks", []):
            yday_stocks[s["code"]] = {
                "level": b.get("level"),
                "name": s.get("name"),
                "industry": s.get("industry"),
                "limit_up_type": s.get("limit_up_type"),
                "limit_up_suc_rate": s.get("limit_up_suc_rate"),
            }
    
    # 4. 竞价数据
    ads = get_auction_for_codes(list(yday_stocks.keys()), delay=0)
    
    # 5. 有效候选：昨日涨停 且 竞价正涨幅
    # yday_stocks: code -> {level, name, industry, limit_up_type, limit_up_suc_rate}
    valid = []
    for code, yd in yday_stocks.items():
        ad = ads.get(code, {})
        chg = ad.get("changeRate", 0)
        if chg > 0:
            valid.append((code, yd, ad))
    
    # 6. 板块轮动
    changes, sorted_ch = compare_sector_momentum(yday, date_str)
    sector_info = get_sector_momentum(date_str)
    
    # 7. 持仓状态
    positions = update_positions()
    portfolio_status = get_portfolio_status(positions)
    
    # 8. 报告
    lines = []
    lines.append(f"{'='*40}")
    lines.append(f"【每日完整策略报告】 {date_str}")
    lines.append(f"{'='*40}")
    lines.append(f"市场阶段: {phase} | 温度: {market_temp} | 资金: {capital/10000:.0f}万")
    lines.append("")
    
    # 板块轮动
    lines.append("【板块轮动】")
    strong = [(s, d) for s, d in sorted_ch if d["diff"] > 0][:5]
    if strong:
        for s, d in strong:
            info = sector_info.get(s, {})
            names = [st["name"] for st in info.get("stocks", [])[:2]]
            lines.append(f"  🔥 {s}: {d['before']}→{d['current']} (+{d['diff']})")
            if names:
                lines.append(f"     代表: {','.join(names)}")
    else:
        lines.append("  无强势板块")
    
    # 持仓
    lines.append("")
    lines.append("【持仓状态】")
    lines.append(format_position_report(positions))
    lines.append(f"汇总: {portfolio_status['total']}只 | 盈{portfolio_status['winning']} | 亏{portfolio_status['losing']} | 警告{portfolio_status['warning']} | 总体{portfolio_status['total_pnl']:+.2f}%")
    
    # 操作建议（来自竞价评级）
    lines.append("")
    lines.append("【今日操作】")
    if not valid:
        lines.append("  无合格标的，观望")
    else:
        # 取前3只B级
        for i, (code, yd, ad) in enumerate(valid[:3]):
            chg = ad.get("changeRate", 0)
            buy_px = ad.get("price", 0)
            lb = yd.get("level", 1)
            tier = "B"
            
            stop = calc_stop_loss(buy_px)
            target = calc_target(buy_px)
            result = calc_position(tier=tier, capital=capital, phase=phase,
                                  existing_positions=positions)
            
            name = yd.get("name", code)
            if buy_px > 0 and chg > 0:
                # 买入方式
                if chg <= 3:
                    method = "竞价买入"
                elif chg <= 7:
                    target_chg = chg * 0.7
                    method = f"等回调+{target_chg:.0f}%（{round(ad.get("preClose",0)*(1+target_chg/100),2)}元）"
                else:
                    target_chg = chg * 0.75
                    method = f"等回调+{target_chg:.0f}%，超+{chg+2:.0f}%放弃"
                
                lines.append(f"  🟡{i+1} {name}({code}) {lb}板 竞价+{chg:.1f}%")
                lines.append(f"     仓位: {result['suggest_capital_pct']*100:.0f}% {result['suggest_amount']:.0f}元 ({result['lot_size']}手)")
                lines.append(f"     止损: {stop} | 目标: {target}")
                lines.append(f"     买入: {method}")
    
    # 熔断提示
    if portfolio_status.get("warning", 0) > 0:
        lines.append("")
        lines.append("🚨 持仓警告！请检查持仓状态，及时止损！")
    
    return "\n".join(lines)


def prev_trading_day(date_str):
    """简单取前一天（实际应查交易日历）"""
    from datetime import datetime, timedelta
    d = datetime.strptime(date_str, "%Y%m%d")
    return (d - timedelta(days=1)).strftime("%Y%m%d")
