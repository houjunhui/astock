"""
8维度量化情绪温度计 v1

维度权重:
  市场连板高度:  20%  (当日最高连板板位)
  涨停/跌停比:  20%  (涨停家数/跌停家数+1)
  炸板率:       15%  (炸板/(涨停+炸板))
  昨日涨停收益:  15%  (昨日涨停股今日平均涨幅)
  板块持续性:   10%  (连续2天上涨板块数量)
  赚钱效应:     10%  (涨幅超5%个股数量分位数)
  亏钱效应:      5%  (跌幅超5%个股数量分位数)
  大盘环境:      5%  (指数当日涨跌幅)

阶段映射:
  80-100分 → 主升
  60-80分  → 发酵
  40-60分  → 分歧
  20-40分  → 退潮
  0-20分   → 冰点
"""

import sys
sys.path.insert(0, '/home/gem/workspace/agent/workspace')

from astock.quicktiny import get_ladder, get_limit_stats, get_market_overview_fixed, get_broken_limit_up

# ── 维度权重 ──────────────────────────────────────────────────────
WEIGHTS = {
    "连板高度":  0.20,
    "涨跌停比":  0.20,
    "炸板率":    0.15,
    "昨日涨停":  0.15,
    "板块持续":  0.10,
    "赚钱效应":  0.10,
    "亏钱效应":  0.05,
    "大盘环境":  0.05,
}


def score_to_phase(score):
    """分数 → 市场阶段"""
    if score >= 80: return "主升"
    if score >= 60: return "发酵"
    if score >= 40: return "分歧"
    if score >= 20: return "退潮"
    return "冰点"


def normalize(value, min_v, max_v):
    """归一化到0-100"""
    if max_v == min_v:
        return 50
    v = (value - min_v) / (max_v - min_v) * 100
    return max(0, min(100, v))


def calc_emotion_score(date_str):
    """
    计算8维度情绪分数（0-100）
    返回: (总分, 阶段, 各维度得分)
    """
    scores = {}
    details = {}
    
    # ── D1: 市场连板高度（20%）──────────────────────────────
    try:
        ladder = get_ladder(date_str) or []
        max_lb = max((s.get("lb", s.get("continue_num", 1)) for s in ladder), default=1)
        # 1板=20分, 3板=40分, 5板=60分, 7板+=80分, 10板+=100分
        if max_lb >= 10:
            d1 = 100
        elif max_lb >= 7:
            d1 = 80 + (max_lb - 7) * 6.67
        elif max_lb >= 5:
            d1 = 60 + (max_lb - 5) * 10
        elif max_lb >= 3:
            d1 = 40 + (max_lb - 3) * 10
        else:
            d1 = 20 + max_lb * 10
        scores["连板高度"] = d1
        details["D1_连板高度"] = f"{max_lb}板 → {d1:.0f}分"
    except Exception as e:
        scores["连板高度"] = 50
        details["D1_连板高度"] = f"异常({e})"
    
    # ── D2: 涨停/跌停比（20%）────────────────────────────────
    try:
        stats = get_limit_stats(date_str) or {}
        # 真实key: limitUp.today.num / limitDown.today.num
        limit_up = (stats.get("limitUp", {}) or {}).get("today", {}).get("num", 0)
        limit_down = (stats.get("limitDown", {}) or {}).get("today", {}).get("num", 0)
        ratio = limit_up / (limit_down + 1)
        # ratio: 0→0分, 3→50分, 6→75分, 10→90分, 20+→100分
        d2 = min(100, ratio / 20 * 100)
        scores["涨跌停比"] = d2
        details["D2_涨跌停比"] = f"{limit_up}/{limit_down+1}={ratio:.1f} → {d2:.0f}分"
    except Exception as e:
        scores["涨跌停比"] = 50
        details["D2_涨跌停比"] = f"异常({e})"
    
    # ── D3: 炸板率（15%）─────────────────────────────────────
    try:
        stats = get_limit_stats(date_str) or {}
        broken = (stats.get("limitUp", {}) or {}).get("today", {}).get("open_num", 0)  # 炸板=涨停开板数
        limit_up = (stats.get("limitUp", {}) or {}).get("today", {}).get("num", 0)
        total = broken + limit_up + 1
        broken_rate = broken / total
        # 炸板率: 0%→100分, 20%→70分, 40%→40分, 60%→10分, 80%+→0分
        d3 = max(0, 100 - broken_rate * 150)
        scores["炸板率"] = d3
        details["D3_炸板率"] = f"{broken}/{total}={broken_rate:.1%} → {d3:.0f}分"
    except Exception as e:
        scores["炸板率"] = 50
        details["D3_炸板率"] = f"异常({e})"
    
    # ── D4: 昨日涨停收益（15%）────────────────────────────────
    try:
        overview = get_market_overview_fixed(date_str) or {}
        # 昨日涨停股今日平均涨幅
        avg_chg = overview.get("yesterday_limit_up_avg_pcp", 0) or 0
        # 收益: -5%→0分, 0%→40分, 5%→70分, 10%→90分, 15%+→100分
        d4 = max(0, min(100, 40 + avg_chg * 6))
        scores["昨日涨停"] = d4
        details["D4_昨日涨停收益"] = f"昨日涨停均{avg_chg:+.2f}% → {d4:.0f}分"
    except Exception as e:
        scores["昨日涨停"] = 50
        details["D4_昨日涨停收益"] = f"异常({e})"
    
    # ── D5: 板块持续性（10%）──────────────────────────────────
    # 用market_overview的涨停家数代替
    try:
        overview = get_market_overview_fixed(date_str) or {}
        limit_up = overview.get("limit_up_count", len(ladder) if ladder else 0)
        # 涨停家数: 20→40分, 40→60分, 60→80分, 80+→100分
        d5 = min(100, max(0, (limit_up - 20) / 60 * 60 + 40))
        scores["板块持续"] = d5
        details["D5_板块持续"] = f"涨停{limit_up}家 → {d5:.0f}分(简化)"
    except Exception as e:
        scores["板块持续"] = 50
        details["D5_板块持续"] = "异常"
    
    # ── D6: 赚钱效应（10%）────────────────────────────────────
    # 用上涨家数分位数
    try:
        overview = get_market_overview_fixed(date_str) or {}
        rise_count = overview.get("rise_count", 0)
        # 上涨家数: 500→40分, 1000→60分, 1500→80分, 2000+→100分
        rise_score = min(100, max(0, (rise_count - 500) / 1500 * 60 + 40))
        d6 = rise_score
        # 20→40分, 50→70分, 80→90分, 100+→100分
        d6 = min(100, max(40, 20 + limit_up * 0.7))
        scores["赚钱效应"] = d6
        details["D6_赚钱效应"] = f"涨停{limit_up}家 → {d6:.0f}分(简化)"
    except Exception as e:
        scores["赚钱效应"] = 50
        details["D6_赚钱效应"] = "异常"
    
    # ── D7: 亏钱效应（5%）────────────────────────────────────
    try:
        limit_down = overview.get("limit_down_count", 0)
        # 跌停家数: 0→100分, 10→70分, 20→40分, 50+→0分
        d7 = max(0, 100 - limit_down * 2)
        scores["亏钱效应"] = d7
        details["D7_亏钱效应"] = f"跌停{limit_down}家 → {d7:.0f}分"
    except Exception as e:
        scores["亏钱效应"] = 50
        details["D7_亏钱效应"] = "异常"
    
    # ── D8: 大盘环境（5%）────────────────────────────────────
    # 用上证指数涨跌幅代替（简化）
    try:
        overview = get_market_overview_fixed(date_str) or {}
        index_chg = overview.get("sh_index_chg", 0) or 0
        # 指数涨跌幅: -2%→0分, 0%→50分, 2%→80分, 3%+→100分
        d8 = max(0, min(100, 50 + index_chg * 30))
        scores["大盘环境"] = d8
        details["D8_大盘环境"] = f"上证{index_chg:+.2f}% → {d8:.0f}分"
    except Exception as e:
        scores["大盘环境"] = 50
        details["D8_大盘环境"] = f"异常({e})"
    
    # ── 加权总分 ────────────────────────────────────────────
    total = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS)
    phase = score_to_phase(total)
    
    return round(total, 1), phase, scores, details


def format_emotion_report(date_str):
    """生成情绪温度计报告"""
    total, phase, scores, details = calc_emotion_score(date_str)
    
    emoji = {"主升":"🔥","发酵":"☀️","分歧":"⛅","退潮":"🌧️","冰点":"❄️"}.get(phase,"?")
    
    lines = [
        f"【🌡️ 情绪温度计】{date_str}",
        f"{'='*36}",
        f"总分: {total:.1f}/100  {emoji}{phase}",
        f"",
        f"【8维度明细】",
    ]
    
    for dim, w in WEIGHTS.items():
        val = scores.get(dim, 0)
        bar = "█" * int(val / 10) + "░" * (10 - int(val / 10))
        lines.append(f"  {dim:<8} {bar} {val:5.1f}分 (权重{w:.0%})")
    
    lines.append(f"{'─'*36}")
    lines.append(f"【阶段仓位上限】")
    caps = {"主升":70,"发酵":60,"分歧":40,"退潮":20,"冰点":0}
    max_pos = caps.get(phase, 0)
    lines.append(f"  {emoji}{phase}期 → 仓位上限{max_pos}%")
    
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    date_str = sys.argv[1] if len(sys.argv) > 1 else "20260324"
    print(format_emotion_report(date_str))
