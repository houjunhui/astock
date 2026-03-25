"""
astock.quicktiny
QuickTiny API 数据源 - A股超短主要数据接口
LB_API_KEY / LB_API_BASE 从环境变量读取
"""
import os, requests, time, json
from datetime import datetime

# ─── .env 自动加载（兼容直接 import 场景）────────────────────
def _load_env():
    """自动从工作空间 .env 加载环境变量（若尚未设置）"""
    if os.environ.get("LB_API_KEY"):
        return  # 已有值，不覆盖
    ws_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(ws_env):
        with open(ws_env) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export ") and "=" in line:
                    k, v = line[7:].split("=", 1)
                    if k.strip() not in os.environ:
                        os.environ[k.strip()] = v.strip().strip('"').strip("'")

_load_env()

# API 配置
API_KEY = os.environ.get("LB_API_KEY", "")
API_BASE = os.environ.get("LB_API_BASE", "https://stock.quicktiny.cn/api/openclaw")

HEADERS = {"Authorization": f"Bearer {API_KEY}"}

# ===================== 限流器 =====================
from collections import deque
_call_times = deque()  # 滑动窗口：记录最近60秒内的请求时间戳
_CALLS_PER_MIN = 30

def _wait_for_slot():
    """滑动窗口限流：确保最近60秒内调用次数 < 30"""
    now = time.time()
    # 清除60秒前的旧记录
    while _call_times and now - _call_times[0] > 60:
        _call_times.popleft()
    if len(_call_times) >= _CALLS_PER_MIN:
        # 最旧的请求将在 (60 - elapsed) 秒后过期
        elapsed = now - _call_times[0]
        sleep_time = 60 - elapsed + 0.05
        time.sleep(sleep_time)
        _wait_for_slot()  # 重新检查（递归直到有槽位）
    _call_times.append(time.time())


def _get(endpoint, params=None, retries=2):
    """通用 GET 请求，带重试"""
    url = f"{API_BASE}/{endpoint}"
    # 小延迟防止瞬时超限（不在wait_for_slot里等，_get内部微调）
    time.sleep(0.05)
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=10)
            if r.status_code == 429:
                retry_after = int(r.headers.get("retryAfterMs", 60000)) / 1000
                time.sleep(retry_after)
                continue
            if r.status_code != 200:
                return None
            data = r.json()
            return data if data.get("success") else None
        except Exception as e:
            if attempt == retries:
                return None
            time.sleep(1)
    return None


# ===================== 核心接口 =====================


def get_ladder(date):
    """
    涨停梯队（核心接口）。
    date: YYYYMMDD 或 YYYY-MM-DD
    返回 {total_stocks, boards: [{level, stocks: [{...}]}, ...]}
    """
    date_str = str(date).replace("-", "")
    d = _get("ladder", {"date": date_str})
    if not d or "data" not in d:
        return None

    try:
        data = d["data"]
        # 新格式：data.dates[0].boards（2026-03-23起）
        if isinstance(data.get("dates"), list) and len(data["dates"]) > 0:
            board_data = data["dates"][0]
            return {
                "total_stocks": board_data.get("totalStocks", 0),
                "boards": board_data.get("boards", []),
            }
        # 旧格式：data.stocks
        elif isinstance(data.get("stocks"), list):
            return {
                "total_stocks": len(data["stocks"]),
                "boards": data.get("boards", [{"level": 1, "stocks": data["stocks"]}]),
            }
    except (IndexError, KeyError):
        pass
    return None


def get_zt_stocks(date):
    """
    获取涨停股列表（来自ladder接口）。
    返回 [{code, name, level(板位), industry, reason, limit_up_time,
           turnover_rate, order_amount, limit_up_type, limit_up_suc_rate}, ...]
    """
    ladder = get_ladder(date)
    if not ladder:
        return []

    stocks = []
    for board in ladder["boards"]:
        level = board.get("level", 1)
        for s in board.get("stocks", []):
            stocks.append({
                "code": s.get("code", ""),
                "name": s.get("name", ""),
                "level": level,
                "industry": s.get("industry", ""),
                "reason": s.get("reason_type") or (str(s.get("jiuyangongshe_analysis") or "")[:50]),
                "first_limit_up_time": s.get("first_limit_up_time", ""),
                "last_limit_up_time": s.get("last_limit_up_time", ""),
                "turnover_rate": s.get("turnover_rate", 0),
                "order_amount": s.get("order_amount", 0),
                "limit_up_type": s.get("limit_up_type", ""),  # 一字板/换手板/T字板
                "limit_up_suc_rate": s.get("limit_up_suc_rate"),  # 历史晋级率
                "continue_num": s.get("continue_num", level),
                "high_days": s.get("high_days", ""),
                "change_rate": s.get("change_rate", 0),
                "currency_value": s.get("currency_value", 0),
            })
    return stocks


def get_kline_hist(code, days=30, end_date=None):
    """
    获取历史日K线（来自kline接口）。
    code: 6位股票代码，如 '603687'
    返回 [{date, open, high, low, close, volume, amount, pct_chg}, ...]
    """
    params = {"days": days}
    if end_date:
        params["endDate"] = str(end_date).replace("-", "")
    d = _get(f"kline/{code}", params)
    if not d or "data" not in d:
        return []
    raw = d["data"]
    if isinstance(raw, list):
        bars = raw
    else:
        bars = raw.get("klines", [])
    # 按日期升序
    bars = sorted(bars, key=lambda x: str(x.get("date", "")))
    return bars


def get_kline_ohlcv(code, days=60):
    """
    获取简化OHLCV数据（用于技术指标计算）。
    返回 (dates, opens, highs, lows, closes, vols)
    """
    bars = get_kline_hist(code, days=days)
    if not bars:
        return [], [], [], [], [], []
    try:
        dates = [b["date"] for b in bars]
        opens = [float(b["open"]) for b in bars]
        highs = [float(b["high"]) for b in bars]
        lows = [float(b["low"]) for b in bars]
        closes = [float(b["close"]) for b in bars]
        vols = [float(b["volume"]) for b in bars]
        return dates, opens, highs, lows, closes, vols
    except (ValueError, KeyError):
        return [], [], [], [], [], []


def get_minute(code, ndays=1):
    """
    获取分时数据。
    code: 6位股票代码，如 '603687'
    返回 [(datetime, open, high, low, close, volume, amount, avg_price), ...]
    """
    d = _get(f"minute/{code}", {"ndays": ndays})
    if not d or "data" not in d:
        return []
    raw = d["data"]
    inner = raw.get("data", {})
    # 实际结构: {"rc":0, "rt":10, ..., "data":{"code":"603687","trends":[...]}}
    # inner 已经是 {"code":..., "trends": [...]}
    if isinstance(inner, dict):
        trends_list = inner.get("trends", [])
    elif isinstance(inner, list):
        trends_list = inner
    else:
        trends_list = []
    if not trends_list:
        return []
    result = []
    for line in trends_list:
        if isinstance(line, list):
            line = ",".join(str(x) for x in line)
        if not line.strip():
            continue
        parts = line.split(",")
        if len(parts) < 7:
            continue
        try:
            dt = datetime.strptime(parts[0], "%Y-%m-%d %H:%M")
            open_ = float(parts[1])
            high = float(parts[2])
            low = float(parts[3])
            close = float(parts[4])
            vol = float(parts[5])
            amount = float(parts[6])
            avg = float(parts[7]) if len(parts) > 7 else close
            result.append((dt, open_, high, low, close, vol, amount, avg))
        except (ValueError, IndexError):
            continue
    return result


def get_market_overview(date):
    """
    市场概况。
    返回 {date, ztCount, dtCount, zbgCount, marketTemp, indices: [...]}
    """
    date_str = str(date).replace("-", "")
    d = _get("market-overview", {"date": date_str})
    if not d or "data" not in d:
        return {}
    raw = d["data"]
    if isinstance(raw, list) and len(raw) > 0:
        return raw[0]
    return raw


def get_limit_up_filter(date, continue_num_min=2, limit=50):
    """
    涨停筛选。
    返回 [{code, name, level, industry, reason, ...}, ...]
    """
    date_str = str(date).replace("-", "")
    d = _get("limit-up/filter", {
        "date": date_str,
        "continueNumMin": continue_num_min,
        "limit": limit,
    })
    if not d or "data" not in d:
        return []
    raw = d["data"]
    items = raw if isinstance(raw, list) else raw.get("items", [])
    return items


def get_anomalies(date):
    """
    异动检测。
    返回 [{code, name, type, changeRate, volumeChangeRate, ...}, ...]
    """
    date_str = str(date).replace("-", "")
    d = _get("anomalies", {"date": date_str})
    if not d or "data" not in d:
        return []
    raw = d["data"]
    if isinstance(raw, list):
        return raw
    return raw.get("items", [])


def get_capital_flow(date, flow_type="market"):
    """
    资金流向。
    flow_type: market / stock / sector / hsgt
    """
    date_str = str(date).replace("-", "")
    d = _get("capital-flow", {"date": date_str, "flowType": flow_type})
    if not d:
        return None
    return d.get("data", {})


def get_concept_ranking(date, limit=30):
    """
    概念排行。
    返回 [{name, rank, changeRate, stockCount, ...}, ...]
    """
    date_str = str(date).replace("-", "")
    d = _get("concepts/ranking", {"date": date_str, "limit": limit})
    if not d or "data" not in d:
        return []
    raw = d["data"]
    if isinstance(raw, list):
        return raw
    return raw.get("items", [])


def get_sector_analysis(source="dongcai_concept", period=60, strength_period=5):
    """
    板块分析四象限。
    返回 [{sectorName, momentum, strength, quadrant, ...}, ...]
    """
    d = _get("sector-analysis", {
        "source": source,
        "period": period,
        "strengthPeriod": strength_period,
    })
    if not d or "data" not in d:
        return []
    raw = d["data"]
    if isinstance(raw, list):
        return raw
    return raw.get("items", [])


def get_research_reports(keyword=None, stock_code=None, page=1, page_size=10):
    """
    研报数据。
    """
    params = {"page": page, "pageSize": page_size}
    if keyword:
        params["keyword"] = keyword
    if stock_code:
        params["stockCode"] = stock_code
    d = _get("research-reports", params)
    if not d:
        return []
    raw = d.get("data", {})
    if isinstance(raw, list):
        return raw
    return raw.get("items", [])


def get_auction(date):
    """
    竞价数据快照（9:15/9:20/9:25三档）。
    返回 {time, upList: [{name, code, auctionChgPct, orderAmount}, ...],
           downList: [...]}
    """
    date_str = str(date).replace("-", "")
    d = _get("auction", {"date": date_str})
    if not d or "data" not in d:
        return {}
    raw = d["data"]
    if isinstance(raw, list) and len(raw) > 0:
        return raw[0]
    return raw


def get_rank(rtype="gainers", market="all", limit=20):
    """
    股票排行。
    """
    d = _get("rank", {"type": rtype, "market": market, "limit": limit})
    if not d or "data" not in d:
        return []
    raw = d["data"]
    if isinstance(raw, list):
        return raw
    return raw.get("items", [])


def get_limit_up_premium(start_date, end_date, min_count=3):
    """
    涨停溢价分析（历史晋级率回测用）。
    """
    d = _get("limit-up/premium", {
        "startDate": str(start_date).replace("-", ""),
        "endDate": str(end_date).replace("-", ""),
        "minLimitUpCount": min_count,
    })
    if not d:
        return []
    return d.get("data", {}).get("items", [])


def get_trading_calendar(date):
    """交易日历（检查某天是否为交易日）"""
    date_str = str(date).replace("-", "")
    d = _get("trading-calendar", {"date": date_str})
    if not d or "data" not in d:
        return None
    return d["data"]


# ===================== 便捷封装 =====================


def is_trading_day(date=None):
    """检查是否为交易日"""
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    result = get_trading_calendar(date)
    if result is None:
        return None  # 未知
    return result.get("isTradingDay", False) or result.get("is_trading_day", False)


def get_zt_with_indicators(date):
    """
    获取涨停股列表，并计算技术指标。
    返回 [{code, name, level, industry, reason, limit_up_time,
           turnover_rate, order_amount, limit_up_type,
           close, vr, rsi, dif, macd, trend, ...}, ...]
    """
    from market import ma, macd_current, rsi as rsi_calc, vol_ma

    stocks = get_zt_stocks(date)
    result = []
    for s in stocks:
        code = s["code"]
        bars = get_kline_hist(code, days=60)
        if len(bars) < 20:
            result.append({**s, "close": 0, "vr": None, "rsi": None,
                           "dif": None, "macd": None, "trend": "数据不足"})
            continue

        closes = [float(b["close"]) for b in bars]
        vols = [float(b["volume"]) for b in bars]
        ma20 = ma(closes, 20)
        ma60 = ma(closes, 60)
        dif, de, macd_val = macd_current(closes)
        rsi_val = rsi_calc(closes)
        vol_ma20 = vol_ma(vols, 20)
        recent_vol = sum(vols[-5:]) / 5 if vols else 0
        vr = vol_ma20 / recent_vol if (vol_ma20 and recent_vol) else None

        cur = closes[-1]

        if ma20 is None or ma60 is None:
            trend = "下降通道"
        elif ma20 > ma60 * 1.02:
            trend = "上升通道"
        elif ma20 < ma60 * 0.98:
            trend = "下降通道"
        else:
            trend = "震荡"

        result.append({
            **s,
            "close": cur,
            "vr": vr,
            "rsi": rsi_val,
            "dif": dif,
            "macd": macd_val,
            "trend": trend,
            "ma20": ma20,
            "ma60": ma60,
            "last_date": bars[-1]["date"] if bars else None,
        })

    return result


def get_auction_for_codes(codes, delay=0):
    """
    批量获取竞价数据（使用codes批量参数，每批最多30个）。
    codes: str列表，如 ['600396', '603687']
    返回: {code: {changeRate, volumeRatio, turnoverRate, price, preClose}, ...}
    """
    if not codes:
        return {}
    result = {}
    BATCH = 30  # 每批最多30个代码
    for i in range(0, len(codes), BATCH):
        batch = codes[i:i+BATCH]
        codes_param = ",".join(batch)
        d = _get("auction", {"codes": codes_param})
        if not d or "data" not in d:
            continue
        raw = d["data"]
        items = raw.get("items", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
        for item in items:
            code = item.get("code", "")
            if code:
                result[code] = {
                    "changeRate": item.get("changeRate", 0),
                    "volumeRatio": item.get("volumeRatio", 1.0),
                    "turnoverRate": item.get("turnoverRate", 0),
                    "price": item.get("price", 0),
                    "preClose": item.get("preClose", 0),
                }
        if delay > 0 and i + BATCH < len(codes):
            time.sleep(delay)
    return result


# ===================== 新接口：涨跌停统计/炸板池/跌停池 =====================

def get_limit_stats(date):
    """
    涨跌停实时统计。
    返回: {limitUp{today{num,history_num,rate,open_num}, yesterday{...}},
          limitDown{today{num,history_num,rate,open_num}, yesterday{...}},
          tradeStatus{id,name}}
    """
    date_str = str(date).replace("-", "")
    d = _get("limit-stats", {"date": date_str})
    if not d or "data" not in d:
        return {}
    return d["data"]


def get_limit_down(date):
    """
    跌停池。
    返回: {stocks:[{code,name,changePercent,price,reasonType},...], total:int}
    """
    date_str = str(date).replace("-", "")
    d = _get("limit-down", {"date": date_str})
    if not d or "data" not in d:
        return {}
    raw = d["data"]
    return {
        "stocks": raw.get("stocks", []) if isinstance(raw, dict) else [],
        "total": raw.get("total", 0) if isinstance(raw, dict) else 0,
    }


def get_broken_limit_up(date):
    """
    炸板池（盘中曾触板但未封住）。
    返回: {stocks:[{code,name,changePercent,price,limitUpSucRate,reasonType},...], total:int}
    """
    date_str = str(date).replace("-", "")
    d = _get("broken-limit-up", {"date": date_str})
    if not d or "data" not in d:
        return {}
    raw = d["data"]
    return {
        "stocks": raw.get("stocks", []) if isinstance(raw, dict) else [],
        "total": raw.get("total", 0) if isinstance(raw, dict) else 0,
    }


def get_market_overview_fixed(date):
    """
    市场概况（修复日期格式）。
    返回: {rise_count, fall_count, limit_up_count, limit_down_count,
           limit_up_broken_count, limit_up_broken_ratio, market_temperature}
    """
    # 日期格式优先 YYYY-MM-DD
    date_str = str(date)
    if len(date_str) == 8:
        date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    d = _get("market-overview", {"date": date_str})
    if not d or "data" not in d:
        return {}
    return d["data"]


# ═══════════════════════════════════════════════════════════
# 新增接口（来自官方文档，2026-03-25 接入）
# ═══════════════════════════════════════════════════════════

def get_search(query, limit=20):
    """
    股票搜索。
    按名称、代码、行业、拼音搜索。
    返回: [{ts_code, symbol, name, area, industry, market, list_status, list_date}, ...]
    """
    d = _get("search", {"query": query, "limit": limit})
    if not d or "data" not in d:
        return []
    data = d["data"]
    if isinstance(data, list):
        return data
    return data.get("items", [])


def get_hot_sectors(date):
    """
    最强风口。返回当日涨停集中板块 + AI分析。
    date: YYYYMMDD 或 YYYY-MM-DD
    返回: [{code, name, changePercent, limitUpNum, continuousPlateNum,
            highBoard, days, stocks: [{code, name, changePercent, continueNum,
            highDays, reasonType, reasonInfo, changeTag, isSt, isNew}, ...]}, ...]
    """
    date_str = str(date).replace("-", "")
    d = _get("hot-sectors", {"date": date_str})
    if not d or "data" not in d:
        return []
    raw = d["data"]
    if isinstance(raw, list):
        return raw
    return raw.get("items", raw.get("sectors", []))


def get_limit_events(event_type="limit_up", limit=100):
    """
    封板/炸板事件流（实时）。
    event_type: limit_up / limit_down
    返回: [{code, name, type, orderVolume, orderAmount, turnover, time}, ...]
    """
    d = _get("limit-events", {"type": event_type, "limit": limit})
    if not d or "data" not in d:
        return []
    data = d["data"]
    if isinstance(data, dict):
        return data.get("events", [])
    if isinstance(data, list):
        return data
    return []


def get_approaching_limit_up(date):
    """
    冲刺涨停（即将涨停但未封）。
    date: YYYYMMDD 或 YYYY-MM-DD
    返回: [{code, name, changePercent, price, volumeRatio, ...}, ...]
    """
    date_str = str(date).replace("-", "")
    d = _get("approaching-limit-up", {"date": date_str})
    if not d or "data" not in d:
        return []
    raw = d["data"]
    if isinstance(raw, list):
        return raw
    return raw.get("items", [])


def get_anomalies(date=None, code=None):
    """
    异动检测。
    date: YYYYMMDD（可选，不传则最新）
    code: 股票代码（可选）
    至少传一个参数。
    返回: [{code, name, type, changeRate, volumeChangeRate, time, ...}, ...]
    """
    params = {}
    if date:
        params["date"] = str(date).replace("-", "")
    if code:
        params["code"] = code
    if not params:
        return []
    d = _get("anomalies", params)
    if not d or "data" not in d:
        return []
    raw = d["data"]
    if isinstance(raw, list):
        return raw
    return raw.get("items", raw.get("anomalies", []))


def get_briefings(date=None, btype="morning"):
    """
    每日简报（AI生成）。
    btype: morning / midday / closing / evening
    date: YYYYMMDD（可选，不传则最新）
    返回: [{date, type, title, summary, content, ...}, ...]
    """
    params = {"type": btype}
    if date:
        params["date"] = str(date).replace("-", "")
    d = _get("briefings", params)
    if not d or "data" not in d:
        return []
    raw = d["data"]
    if isinstance(raw, list):
        return raw
    return [raw] if raw else []


def get_correlation(code):
    """
    股票关联（同概念股）。
    code: 6位股票代码
    返回: [{code, name, correlation, sharedConcepts, ...}, ...]
    """
    d = _get(f"correlation/{code}", {})
    if not d or "data" not in d:
        return []
    raw = d["data"]
    if isinstance(raw, list):
        return raw
    return raw.get("items", [raw])


def get_concept_stocks(ts_code, date=None):
    """
    概念成分股。
    ts_code: 概念代码，如 885760.TI
    date: YYYYMMDD（可选）
    返回: [{code, name, changePercent, ...}, ...]
    """
    params = {"tsCode": ts_code}
    if date:
        params["date"] = str(date).replace("-", "")
    d = _get(f"concepts/{ts_code}/stocks", params)
    if not d or "data" not in d:
        return []
    raw = d["data"]
    if isinstance(raw, list):
        return raw
    return raw.get("items", [])


def get_capital_flow_v2(flow_type="stock", stock_code=None, sector_type=None, date=None, limit=30):
    """
    资金流向（增强版）。
    flow_type: market / stock / sector / hsgt
    返回: {flowType, count, data: [{date, value, direction?}, ...]}
    """
    params = {"flowType": flow_type, "limit": limit}
    if stock_code:
        params["stockCode"] = stock_code
    if sector_type:
        params["sectorType"] = sector_type
    if date:
        params["date"] = str(date).replace("-", "")
    d = _get("capital-flow", params)
    if not d or "data" not in d:
        return {}
    return d["data"]


def get_dragon_tiger(date=None, stock_code=None, stock_name=None, page=1, page_size=20):
    """
    龙虎榜（营业部席位买卖详情）。
    date: YYYY-MM-DD（必填）
    stock_code: 股票代码（可选，精确到某只股票）
    stock_name: 股票名称（可选）
    page/page_size: 分页（最大 page_size=100）
    返回（list）: [{date, stockCode, stockName, reason, close, chgPct,
                    volume, amount, netBuy, totalBuy, totalSell,
                    buyBranches: [{name, buyAmt, sellAmt, netAmt}, ...],
                    sellBranches: [{name, buyAmt, sellAmt, netAmt}, ...],
                    limitUpInfo: {...}}, ...]
    """
    params = {"page": page, "pageSize": page_size}
    if date:
        params["date"] = str(date)  # YYYY-MM-DD
    if stock_code:
        params["stockCode"] = stock_code
    if stock_name:
        params["stockName"] = stock_name
    d = _get("dragon-tiger", params)
    if not d or "data" not in d:
        return []
    raw = d["data"]
    return raw if isinstance(raw, list) else []
