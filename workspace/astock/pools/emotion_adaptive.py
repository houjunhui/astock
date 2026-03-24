"""
情绪自适应评分 - 多维度加权情绪评估
替代简单的 5 级分类（主升/发酵/分歧/退潮/冰点）
"""
import sys; sys.path.insert(0, '/home/gem/workspace/agent/workspace')
from astock.quicktiny import get_market_overview_fixed, get_ladder
import json

def calc_emotion_adaptive(date_str, params=None):
    """
    多维度情绪评分（0-100）
    返回: (total_score, phase, details)
    """
    if params is None:
        from astock.strategy_params import get_params
        params = get_params()
    
    ea = params.get("emotion_adaptive", {})
    if not ea.get("enabled", False):
        # fallback 到简单判断
        return calc_emotion_simple(date_str)
    
    # 获取各维度数据
    try:
        mo = get_market_overview_fixed(date_str)
    except:
        mo = {}
    
    qt_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    try:
        ladder = get_ladder(date_str.replace("-", ""))
        zt_count = sum(len(b.get("stocks", [])) for b in ladder.get("boards", [])) if ladder else 0
        max_lb = max((b["level"] for b in ladder.get("boards", [])), default=1) if ladder else 1
    except:
        zt_count = mo.get("zt_count", 0)
        max_lb = 1
    
    # 各维度评分
    # 1. 指数（沪指涨跌幅）
    index_chg = abs(mo.get("sh_index_chg", 0))
    index_score = min(index_chg * 5, 30)  # 涨1%→5分，封顶30
    
    # 2. 涨停家数（0-100映射到0-25分）
    zt_score = min(zt_count / 2, 25)  # 50家涨停→25分封顶
    
    # 3. 跌停家数（跌停越多越差）
    dt_count = mo.get("limit_down_count", 0)
    dt_score = max(0, 15 - dt_count * 0.5)  # 0家跌停→15分，30家→0分
    
    # 4. 炸板率（越低越好）
    broken_rate = mo.get("broken_rate", 0) or 0
    broken_score = max(0, 15 - broken_rate * 0.3)  # 0%炸板→15分，50%→0分
    
    # 5. 连板高度（高度越高情绪越强）
    board_score = min(max_lb * 3, 15)  # 5板→15分
    
    total = index_score + zt_score + dt_score + broken_score + board_score
    total = min(total, 100)
    
    # 相位映射
    if total >= 80:
        phase = "主升"
    elif total >= 60:
        phase = "发酵"
    elif total >= 40:
        phase = "分歧"
    elif total >= 20:
        phase = "退潮"
    else:
        phase = "冰点"
    
    details = {
        "index_score": round(index_score, 1),
        "zt_score": round(zt_score, 1),
        "dt_score": round(dt_score, 1),
        "broken_score": round(broken_score, 1),
        "board_score": round(board_score, 1),
        "zt_count": zt_count,
        "max_lb": max_lb,
        "broken_rate": broken_rate,
    }
    
    return round(total, 1), phase, details

def calc_emotion_simple(date_str):
    """简单fallback"""
    try:
        mo = get_market_overview_fixed(date_str)
        temp = mo.get("market_temperature", 50)
        zt = mo.get("zt_count", 0)
        broken = mo.get("broken_rate", 0)
        if temp >= 80 and zt >= 30 and broken < 20:
            return temp, "主升", {}
        elif temp >= 60:
            return temp, "发酵", {}
        elif broken >= 35 or zt < 15:
            return temp, "退潮", {}
        elif zt <= 5 or temp < 10:
            return temp, "冰点", {}
        return temp, "分歧", {}
    except:
        return 50, "分歧", {}

if __name__ == "__main__":
    score, phase, details = calc_emotion_adaptive("20260324")
    print(f"情绪: {phase} {score}度")
    for k, v in details.items():
        print(f"  {k}: {v}")
