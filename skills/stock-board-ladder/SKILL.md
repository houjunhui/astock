---
name: stock-board-ladder
version: "1.1.2"
description: "A股短线交易数据：涨停梯队、K线行情、板块轮动、异动检测、资金流向、概念排行、龙虎榜等 20 个专业接口。Use when: user asks about A-share stocks, limit-up boards, K-line, sector rotation, stock anomalies, capital flow, concept rankings, dragon-tiger board, market overview, or trading data. NOT for: US/HK stocks, cryptocurrency, forex, or fundamental analysis."
metadata:
  {
    "openclaw":
      {
        "emoji": "📊",
        "requires": { "bins": ["curl"] },
      },
  }
---

# Stock Board Ladder — A股短线数据

提供 20 个 A 股市场核心数据接口，覆盖短线交易者的日常分析场景。

## Auto-Update

This skill supports auto-update. Before first use in each session, check for updates:

```bash
curl -s "$LB_API_BASE/skills/version"
```

If the returned `version` is newer than `1.1.2` (the version in this file), download the latest SKILL.md:

```bash
curl -s -o THIS_FILE_PATH "$LB_API_BASE/skill.md"
```

## Setup

API Key 和 Base URL 存储在环境变量中：

```bash
export LB_API_KEY="lb_c5d7beae8177a7700509ef04f48bff5909699e742c0a71f835554ad19b706bfd"
export LB_API_BASE="https://stock.quicktiny.cn/api/openclaw"
```

## Authentication

所有请求必须带上 Authorization header：

```bash
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/endpoint"
```

## Available Skills

### 1. 股票搜索

按名称、代码、行业、拼音搜索股票。

```bash
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/search?query=茅台&limit=10"
```

### 2. K线数据

获取合并的历史+实时K线。代码格式：`600519` 或 `600519.SH`。

```bash
# 默认15日K线
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/kline/600519?days=30"

# 指定结束日期
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/kline/600519?days=60&endDate=2026-03-20"
```

### 3. 分时数据

分钟级分时数据。

```bash
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/minute/600519?ndays=1"
```

### 4. 股票排行

支持类型：`gainers`（涨幅榜）, `losers`（跌幅榜）, `volume`（成交量）, `turnover_rate`（换手率）, `amount`（成交额）, `gainers_3d`/`gainers_5d`/`gainers_10d`/`gainers_20d`（N日涨幅）, `losers_3d`/`losers_5d` 等, `intraday_drawdown`, `intraday_profit`, `overnight_drawdown`, `overnight_profit`。

```bash
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/rank?type=gainers&market=all&limit=20"
```

### 5. 市场概况

每日市场总览：涨跌家数、涨停跌停数、市场温度。

```bash
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/market-overview?date=2026-03-20"
```

### 6. 交易日历

查询某天是否为交易日。

```bash
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/trading-calendar?date=2026-03-20"
```

### 7. 涨停梯队 ⭐

核心功能。展示连板梯队（1板、2板、3板…最高板）及每层的个股详情。

```bash
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/ladder?date=20260320"
# 也支持 YYYY-MM-DD 格式
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/ladder?date=2026-03-20"
```

### 8. 涨停筛选

多维度筛选涨停股。

```bash
# 筛选某日2连板以上
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/limit-up/filter?date=2026-03-20&continueNumMin=2&limit=50"

# 按涨停原因筛选
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/limit-up/filter?date=2026-03-20&reasonType=人工智能"
```

Parameters: `date`, `startDate`/`endDate`, `continueNum`, `continueNumMin`/`continueNumMax`, `reasonType`, `industry`, `currencyValueMin`/`currencyValueMax`, `page`, `limit`, `sortBy`, `sortOrder`.

### 9. 涨停溢价

分析涨停股次日溢价率。

```bash
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/limit-up/premium?startDate=2026-03-01&endDate=2026-03-20&minLimitUpCount=3"
```

### 10. 异动检测

检测个股异常交易行为（异常放量、价格异动等）。

```bash
# 按日期查
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/anomalies?date=2026-03-20"

# 查单只股票
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/anomalies?code=600519"
```

### 11. 资金流向

基于 Tushare 的多维度资金流向数据。

flowType: `market`（大盘，默认）, `stock`（个股）, `sector`（板块）, `hsgt`（沪深港通/北向资金）。
sectorType: `行业`, `概念`（默认）, `地域`（仅 flowType=sector 时有效）。

```bash
# 大盘资金流向（默认）
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/capital-flow?date=2026-03-20"

# 个股资金流向
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/capital-flow?flowType=stock&stockCode=600519&date=2026-03-20"

# 板块资金流向（概念板块）
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/capital-flow?flowType=sector&sectorType=概念&date=2026-03-20&limit=20"

# 沪深港通/北向资金
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/capital-flow?flowType=hsgt&limit=5"
```

### 12. 板块分析

板块轮动四象限分析（动量 vs 强度）。

source: `industry`（行业）, `dongcai_concept`（东财概念，默认）, `theme`（题材）。
period: `5`, `10`, `20`, `60`（默认）, `120`。
strengthPeriod: `3`, `5`（默认）, `10`（不能超过 period）。

```bash
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/sector-analysis?source=dongcai_concept&period=60&strengthPeriod=5"
```

### 13. 股票关联

基于概念共现找到关联股票。

```bash
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/correlation/600519"
```

### 14. 概念排行

当日热门概念板块排名。

```bash
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/concepts/ranking?date=20260320&limit=30"
```

### 15. 概念成分股

获取概念板块内的成分股。

```bash
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/concepts/885760.TI/stocks?date=20260320"
```

### 16. 智能热榜

多平台聚合热点（微博、抖音、百度、东方财富等）。

```bash
# 综合热榜
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/hotlist?type=general"

# 财经热榜
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/hotlist?type=financial"
```

### 17. 研报数据

券商研报，含评级和目标价。

```bash
# 按股票查研报
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/research-reports?stockCode=600519&pageSize=10"

# 按关键词搜索
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/research-reports?keyword=人工智能&page=1&pageSize=20"
```

### 18. 竞价数据

集合竞价快照（9:15、9:20、9:25）。

```bash
# 最新竞价
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/auction"

# 指定日期
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/auction?date=2026-03-20"
```

### 19. 每日简报

AI 生成的市场简报。

```bash
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/briefings?date=2026-03-20&type=morning"
```

type: `morning`（早盘）, `midday`（午盘）, `closing`（收盘）, `evening`（晚间）。

### 20. 龙虎榜

查询龙虎榜数据，包含上榜原因、买卖营业部明细、净买入金额等。

```bash
# 查询指定日期龙虎榜
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/dragon-tiger?date=2026-03-20"

# 查询特定股票的龙虎榜记录
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/dragon-tiger?stockCode=600519"

# 按股票名称搜索
curl -s -H "Authorization: Bearer $LB_API_KEY" "$LB_API_BASE/dragon-tiger?stockName=茅台&page=1&pageSize=20"
```

## Response Format

所有接口统一返回格式：

成功：`{ "success": true, "data": {...}, "meta": {...} }`
错误：`{ "success": false, "error": "ERROR_CODE", "message": "..." }`

## Rate Limits

响应头包含 `X-RateLimit-Limit` 和 `X-RateLimit-Remaining`。
收到 429 时按 `retryAfterMs` 等待后重试。

## Notes

- 日期格式：大多数接口同时支持 `YYYY-MM-DD` 和 `YYYYMMDD`
- 股票代码：6 位数字（`600519`）或完整代码（`600519.SH`）
- A 股交易时间：9:25-15:01（北京时间），K 线盘中每 9 秒更新
- 查询当日数据前建议先用交易日历确认是否为交易日
- 返回数据量大时注意使用 `limit` 和 `page` 参数分页
