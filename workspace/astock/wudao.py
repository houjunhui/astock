"""
astock.wudao
悟道技能接口封装 - 研报/龙虎榜/板块分析/智能热榜/涨停筛选

所有API调用走 LB_API_KEY / LB_API_BASE 环境变量
数据供预测系统使用
"""
import os, requests, json, time
from datetime import datetime

API_KEY = os.environ.get("LB_API_KEY", "")
API_BASE = os.environ.get("LB_API_BASE", "https://stock.quicktiny.cn/api/openclaw")
HEADERS = {"Authorization": f"Bearer {API_KEY}"}
TIMEOUT = 8  # 秒


def _get(endpoint, params=None):
    """统一GET，带超时保护"""
    url = f"{API_BASE}/{endpoint}"
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
        if r.status_code == 200:
            d = r.json()
            if d.get("success"):
                return d.get("data", d)
        return None
    except Exception:
        return None


# ============================================================
# ① 研报数据：单只股票最新评级/目标价
# ============================================================

def get_research_report(stock_code):
    """
    获取个股最新券商研报。
    返回: {
        rating: str,       # 评级（买入/增持/中性/减持）
        rating_change: str,# 评级变动
        target_price: float,  # 目标价上限
        target_price_l: float,# 目标价下限
        org: str,          # 机构简称
        publish_date: str, # 发布日期
        rating_signal: int  # 信号 1=利好 0=中性 -1=利空
    }
    """
    d = _get("research-reports", {"stockCode": stock_code, "pageSize": 3})
    if not d:
        return None

    reports = d.get("reports", []) if isinstance(d, dict) else []
    if not reports:
        return None

    # 取最新一份
    r = reports[0]
    rating = r.get("emRatingName", "")
    # 量化信号
    if rating in ("买入", "强烈推荐", "推荐", "优于大市", "超配"):
        signal = 1
    elif rating in ("增持", "谨慎推荐", "审慎推荐", "优于大势", "标配"):
        signal = 1
    elif rating in ("中性", "持有", "同步大市", "标配", "平配"):
        signal = 0
    else:
        signal = -1  # 减持/卖出/回避

    target = r.get("indvAimPriceT") or r.get("indvAimPriceL") or 0
    target_l = r.get("indvAimPriceL") or 0

    return {
        "rating": rating,
        "rating_change": r.get("ratingChange", ""),
        "target_price": float(target) if target else 0,
        "target_price_l": float(target_l) if target_l else 0,
        "org": r.get("orgSName", ""),
        "publish_date": r.get("publishDate", ""),
        "rating_signal": signal,
    }


# ============================================================
# ② 龙虎榜：判断席位性质（机构/游资）
# ============================================================

def get_dragontiger(stock_code, date=None):
    """
    获取个股近期龙虎榜数据，判断主力席位类型。

    返回: {
        is_on_list: bool,       # 是否上过龙虎榜
        net_buy: float,        # 净买入额（万元）
        total_buy: float,      # 总买入额
        total_sell: float,     # 总卖出额
        is_institutional: bool, # 是否有机构席位
        is_youcheng: bool,      # 是否有游资席位
        buy_seats: [str],      # 买方席位列表
        sell_seats: [str],     # 卖方席位列表
        reason: str,           # 上榜原因
        signal: int            # 1=机构净买入利好 0=普通 -1=机构净卖出利空
    }
    """
    params = {"stockCode": stock_code}
    if date:
        params["date"] = date
    d = _get("dragon-tiger", params)
    if not d:
        return None

    items = d if isinstance(d, list) else d.get("data", [])
    if not items:
        return None

    # 合并所有上榜记录
    total_net = 0
    total_buy = 0
    total_sell = 0
    institutional_buy = 0
    youcheng_names = []
    buy_seats = []
    sell_seats = []
    reasons = []

    for item in items:
        nb = item.get("netBuy", 0) or 0
        tb = item.get("totalBuy", 0) or 0
        ts = item.get("totalSell", 0) or 0
        total_net += nb
        total_buy += tb
        total_sell += ts
        reasons.append(item.get("reason", ""))

        for b in item.get("buyBranches", []):
            name = b.get("name", "")
            amt = b.get("buyAmt", 0) or 0
            if "机构" in name or "公募" in name or "基金" in name:
                institutional_buy += amt
            elif "营业部" in name or "证券" in name:
                youcheng_names.append(name)
            buy_seats.append(name)

        for s in item.get("sellBranches", []):
            sell_seats.append(s.get("name", ""))

    is_inst = institutional_buy > 0
    signal = 0
    if is_inst and total_net > 0:
        signal = 1  # 机构净买入
    elif is_inst and total_net < 0:
        signal = -1  # 机构净卖出

    return {
        "is_on_list": True,
        "net_buy": round(total_net / 10000, 1),  # 万元
        "total_buy": round(total_buy / 10000, 1),
        "total_sell": round(total_sell / 10000, 1),
        "is_institutional": is_inst,
        "is_youcheng": len(youcheng_names) > 0,
        "buy_seats": list(set(buy_seats)),
        "sell_seats": list(set(sell_seats)),
        "reason": " | ".join(set(r for r in reasons if r)),
        "signal": signal,
    }


# ============================================================
# ③ 板块分析：四象限判断（最强风口）
# ============================================================

_sector_cache = {"data": None, "ts": 0}


def get_sector_quadrants(period=60, strength_period=5, max_age=3600):
    """
    获取板块四象限（量价关系）。

    返回: {
        high_strong: [板块名, ...],   # 量价齐升（强势）
        high_weak: [板块名, ...],     # 价升量跌（背离）
        low_strong: [板块名, ...],     # 量增价跌（吸筹）
        low_weak: [板块名, ...],      # 量价齐跌（弱势）
        all_sectors: [{name, strength, period_change}, ...]
    }
    """
    global _sector_cache
    now = time.time()
    if _sector_cache["data"] and (now - _sector_cache["ts"]) < max_age:
        return _sector_cache["data"]

    d = _get("sector-analysis", {
        "source": "dongcai_concept",
        "period": period,
        "strengthPeriod": strength_period
    })
    if not d:
        return None

    qs = d.get("quadrants", {})
    all_s = d.get("allSectors", [])

    result = {
        "high_strong": [s["name"] for s in qs.get("highStrong", [])],
        "high_weak": [s["name"] for s in qs.get("highWeak", [])],
        "low_strong": [s["name"] for s in qs.get("lowStrong", [])],
        "low_weak": [s["name"] for s in qs.get("lowWeak", [])],
        "all_sectors": [
            {"name": s.get("name", ""), "strength": s.get("rank", 0)}
            for s in all_s if s.get("name")
        ],
        "hot_sectors": [s["name"] for s in qs.get("highStrong", [])],  # 简称
    }

    _sector_cache = {"data": result, "ts": now}
    return result


# 板块关键词 → 四象限标签映射（手工维护，减少误匹配）
_SECTOR_KEYWORDS = {
    # 强势板块关键词
    "强势": [
        "储能", "逆变器", "水力发电", "油气开采", "新能源", "电力", "光伏",
        "氢能", "风电", "核电", "充电桩", "电网", "智能电网", "虚拟电厂",
        "AI", "人工智能", "芯片", "半导体", "算力", "机器人",
        "商业航天", "低空经济", "eVTOL",
        "新材料", "碳纤维", "石墨烯",
        "生物医药", "创新药", "医疗器械",
        "稀土", "有色", "铜", "锂",
    ],
    # 弱势板块关键词
    "弱势": [
        "房地产", "建材", "家具", "纺织", "服装",
        "旅游", "餐饮", "酒店", "航空", "机场",
        "银行", "保险", "证券", "多元金融",
        "传媒", "互联网", "教育",
        "煤炭", "石油", "化工", "钢铁", "水泥",
        "工程机械", "重型机械",
    ]
}


def sector_hot_signal(industry_name, sector_data):
    """
    判断个股所属行业在四象限中的位置（模糊匹配）。
    返回: int  1=强势  0=中性  -1=弱势
    """
    if not sector_data or not industry_name:
        return 0
    name = industry_name.strip()
    if not name or name == "其他":
        return 0

    # 直接匹配（精确）
    if name in sector_data.get("high_strong", []):
        return 1
    if name in sector_data.get("low_weak", []):
        return -1

    # 关键词模糊匹配
    # 强势关键词
    for kw in _SECTOR_KEYWORDS["强势"]:
        if kw in name:
            return 1
    # 弱势关键词
    for kw in _SECTOR_KEYWORDS["弱势"]:
        if kw in name:
            return -1
    return 0


# ============================================================
# ④ 资金流向（全局，非个股）
# ============================================================

def get_market_capital_flow(date=None):
    """
    获取市场整体资金流向（如果有数据的话）。
    目前 quicktiny 此接口不稳定，返回 None 不影响主流程。
    """
    # 接口暂不稳定，降级处理
    return None


# ============================================================
# ⑤ 智能热榜：市场情绪温度计
# ============================================================

_hotlist_cache = {"data": None, "ts": 0}


def get_hotlist(topics_only=False, max_age=600):
    """
    获取智能热榜。

    topics_only=True: 只返回主题列表（用于判断热度集中度）
    """
    global _hotlist_cache
    now = time.time()
    if _hotlist_cache["data"] and (now - _hotlist_cache["ts"]) < max_age:
        return _hotlist_cache["data"]

    d = _get("hotlist", {"type": "financial"})
    if not d:
        return None

    # 兼容不同返回格式
    if isinstance(d, dict):
        themes = d.get("themes", [])
        total = d.get("totalNews", 0)
        nxt = d.get("nextUpdate", "")
    elif isinstance(d, list) and len(d) > 0:
        themes = d[0].get("themes", []) if isinstance(d[0], dict) else []
        total = d[0].get("totalNews", 0) if isinstance(d[0], dict) else 0
        nxt = ""
    else:
        themes = []
        total = 0
        nxt = ""

    result = {
        "themes": [
            {
                "title": t.get("title", ""),
                "hot_score": t.get("hotScore", 0),
                "news_count": t.get("newsCount", 0),
                "platforms": t.get("platforms", []),
                "summary": t.get("summary", ""),
            }
            for t in themes if isinstance(t, dict)
        ],
        "total_news": total,
        "next_update": nxt,
        "theme_count": len(themes),
    }

    _hotlist_cache = {"data": result, "ts": now}
    return result


def hotlist_sentiment_signal():
    """
    从热榜判断市场情绪。
    返回: {
        signal: int,   # 1=活跃(主题数>20且有高热度) 0=一般 -1=冷清
        total_themes: int,
        avg_hot_score: float,
        top_theme: str
    }
    """
    data = get_hotlist(topics_only=True)
    if not data:
        return {"signal": 0, "total_themes": 0, "avg_hot_score": 0, "top_theme": ""}

    themes = data.get("themes", [])
    n = len(themes)
    if n == 0:
        return {"signal": -1, "total_themes": 0, "avg_hot_score": 0, "top_theme": ""}

    avg_score = sum(t.get("hot_score", 0) for t in themes) / n if n > 0 else 0
    top = themes[0].get("title", "") if themes else ""

    # 判断标准：主题数>10 且平均热度>30 → 市场活跃
    signal = 1 if (n > 10 and avg_score > 30) else 0 if n > 3 else -1

    return {
        "signal": signal,
        "total_themes": n,
        "avg_hot_score": round(avg_score, 1),
        "top_theme": top,
    }


# ============================================================
# ⑥ 涨停筛选（含丰富字段）：批量获取今日涨停详细数据
# ============================================================

_zt_filter_cache = {"data": None, "date": None, "ts": 0}


def get_zt_filter_batch(date, min_continue=0, limit=100, max_age=300):
    """
    批量获取涨停股票详细数据（含行业/原因/封单/换手等）。
    用于替代/补充 ladder 接口。

    返回: {
        code: {
            reason_type: str,     # 涨停原因类型（绿电+氢能+央企）
            industry: str,        # 行业
            is_again_limit: bool, # 昨日是否涨停
            limit_up_suc_rate: float,  # 历史封板率
            turnover_rate: float,  # 换手率
            order_amount: float,   # 封单金额（元）
            jiuyangongshe_analysis: str,  # 九眼宫分析摘要
        }
    }
    """
    global _zt_filter_cache
    now = time.time()
    # 同一日期5分钟内用缓存
    if (_zt_filter_cache["data"]
            and _zt_filter_cache["date"] == date
            and (now - _zt_filter_cache["ts"]) < max_age):
        return _zt_filter_cache["data"]

    d = _get("limit-up/filter", {
        "date": date.replace("-", ""),
        "continueNumMin": min_continue,
        "limit": limit
    })
    if not d:
        return {}

    items = d.get("items", []) if isinstance(d, dict) else []
    result = {}
    for item in items:
        code = str(item.get("code", "")).zfill(6)
        result[code] = {
            "name": item.get("name", ""),
            "reason_type": item.get("reason_type", ""),
            "industry": item.get("industry", item.get("jiuyangongshe_category_name", "")),
            "is_again_limit": bool(item.get("is_again_limit")),
            "is_new_limit": bool(item.get("is_new")),
            "limit_up_suc_rate": float(item.get("limit_up_suc_rate") or 0),
            "turnover_rate": float(item.get("turnover_rate") or 0),
            "order_amount": float(item.get("order_amount") or 0),  # 元
            "jiuyangongshe_analysis": item.get("jiuyangongshe_analysis", "")[:200],
            "high_days": item.get("high_days", ""),
            "continue_num": item.get("continue_num", 0),
            "change_tag": item.get("change_tag", ""),
        }

    _zt_filter_cache = {"data": result, "date": date, "ts": now}
    return result


# ============================================================
# 批量信号获取（供 main.py 并发预测前一次性调用）
# ============================================================

def batch_load_signals(date):
    """
    一次性加载所有外部信号，供 run_predict 调用。
    返回 dict，供 predict_stock_v2 使用。
    """
    signals = {}

    # 1. 涨停筛选详细数据（5分钟缓存）
    zt_detail = get_zt_filter_batch(date, limit=100)
    signals["zt_detail"] = zt_detail

    # 2. 板块四象限（1小时缓存）
    sector_data = get_sector_quadrants(period=60, strength_period=5)
    signals["sector_data"] = sector_data or {}

    # 3. 热榜情绪（10分钟缓存）
    hot = hotlist_sentiment_signal()
    signals["hotlist"] = hot

    return signals
