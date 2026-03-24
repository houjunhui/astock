"""
板块轮动跟踪 v1.0
盘中监控板块涨停数量变化，判断板块是否退潮
"""

from collections import defaultdict
from astock.quicktiny import get_ladder, get_limit_stats


def get_sector_momentum(date):
    """
    获取板块动能快照
    返回: {sector_name: {"count": n, "stocks": [...]}, ...}
    """
    ladder = get_ladder(date)
    sectors = defaultdict(lambda: {"count": 0, "stocks": []})
    
    for board in ladder.get("boards", []):
        for stock in board.get("stocks", []):
            industry = stock.get("industry", "未知")
            sectors[industry]["count"] += 1
            sectors[industry]["stocks"].append({
                "code": stock.get("code"),
                "name": stock.get("name"),
                "level": board.get("level"),
                "change_rate": stock.get("change_rate"),
            })
    
    return dict(sectors)


def compare_sector_momentum(date_before, date_current):
    """
    对比两个日期的板块变化
    识别：强势板块、弱势板块、轮动方向
    """
    before = get_sector_momentum(date_before)
    current = get_sector_momentum(date_current)
    
    # 计算变化
    changes = {}
    all_sectors = set(before.keys()) | set(current.keys())
    
    for sector in all_sectors:
        bc = before.get(sector, {"count": 0})["count"]
        cc = current.get(sector, {"count": 0})["count"]
        diff = cc - bc
        
        if bc == 0 and cc > 0:
            status = "🆕新启动"
        elif bc > 0 and cc == 0:
            status = "⚠️退潮"
        elif diff > 0:
            status = "🔥强化"
        elif diff < 0:
            status = "📉弱化"
        else:
            status = "➡️维持"
        
        changes[sector] = {
            "before": bc,
            "current": cc,
            "diff": diff,
            "status": status,
        }
    
    # 排序：强化在前
    sorted_sectors = sorted(changes.items(), key=lambda x: -x[1]["diff"])
    
    return changes, sorted_sectors


def check_position_sector风险(positions, current_sectors):
    """
    检查持仓板块是否仍然强势
    positions: [{code, name, sector}, ...]
    current_sectors: get_sector_momentum() 返回值
    """
    alerts = []
    for pos in positions:
        sector = pos.get("sector", "")
        if sector in current_sectors:
            info = current_sectors[sector]
            if info["count"] == 0:
                alerts.append({
                    "code": pos["code"],
                    "name": pos["name"],
                    "sector": sector,
                    "alert": "⚠️ 板块已无涨停，跟随弱势板块，建议关注止损"
                })
            elif info["count"] <= 1:
                alerts.append({
                    "code": pos["code"],
                    "name": pos["name"],
                    "sector": sector,
                    "alert": f"⚡ 板块仅剩1只涨停，板块弱势，仅{pos['name']}独苗"
                })
    return alerts


def format_sector_report(current_sectors, sorted_changes, positions=None):
    """格式化板块报告"""
    # 取前5强化+前5弱化
    strong = [(s, d) for s, d in sorted_changes if d["diff"] > 0][:5]
    weak = [(s, d) for s, d in sorted_changes if d["diff"] < 0][:5]
    
    lines = ["📊 板块轮动监控\n" + "─" * 30]
    
    lines.append("\n🔥 强势板块（涨停数增加）：")
    if strong:
        for s, d in strong:
            info = current_sectors.get(s, {})
            names = [st["name"] for st in info.get("stocks", [])[:3]]
            lines.append(f"  {s}: {d['before']}→{d['current']} (+{d['diff']})")
            if names:
                lines.append(f"    代表: {','.join(names)}")
    else:
        lines.append("  无明显强势板块")
    
    lines.append("\n📉 弱势板块（涨停数减少）：")
    if weak:
        for s, d in weak:
            lines.append(f"  {s}: {d['before']}→{d['current']} ({d['diff']})")
    else:
        lines.append("  无明显弱势板块")
    
    return "\n".join(lines)
