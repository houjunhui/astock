# A股超短预测系统

> 专注于涨停板短线交易的量化选股与风控系统

---

## 系统架构

```
astock/
├── quicktiny.py          # quicktiny API 封装（数据源）
├── quicktiny_kline.py    # K线数据获取（本地缓存 + baostock）
├── predict.py            # 晋级率预测 v1
├── predict_calibrated.py # 晋级率预测 v2（已校准）
├── calibration_v2.py     # 概率校准模块
├── risk_control.py       # 七层风控引擎
├── db.py                 # SQLite 主数据库
├── config.py             # 全局配置
├── market.py             # 市场工具函数
├── market_sentiment.py   # 市场情绪判断
├── auction.py            # 竞价数据处理
├── formatter.py           # 格式化输出
├── trade_logger.py        # 交易日志
│
├── pools/                # Alpha因子体系
│   ├── stock_pool.py     # 股票池分层
│   ├── board_tier.py    # 连板梯队分档
│   ├── ml_model.py      # ML模型骨架
│   ├── dynamic_position.py # 动态仓位
│   └── emotion_*.py      # 情绪自适应
│
├── position/             # 持仓管理
│   ├── position_sqlite.py # 持仓持久化
│   ├── position_tracker.py # 持仓跟踪
│   ├── position_sizer.py  # 仓位计算
│   └── daily_report.py    # 每日报告
│
└── backtest/            # 回测引擎
    ├── engine.py
    ├── runner.py
    └── costs.py

scripts/
├── auto_buy.py          # 竞价自动买入（cron 9:26）
├── auto_monitor.py      # 盘中持仓监控（cron 每5分钟）
├── auto_close.py        # 收盘自动平仓（cron 15:05）
├── evening_preselect.py # 晚盘预选（cron 23:00）
├── astock_auction_full.py # 完整竞价筛选
├── quicktiny_cache.py   # 回测数据缓存
└── backtest_*.py        # 回测分析脚本
```

---

## 核心数据表

| 表名 | 用途 |
|------|------|
| `predictions` | 每日预测结果（代码/名称/概率/信号/风控等级） |
| `daily_stats` | 每日市场统计（涨停数/跌停数/炸板率/市场温度） |
| `positions` | 持仓记录（代码/买入价/数量/持仓天数/风控标签） |
| `calibration` | 概率校准记录 |
| `historical_zt` | 历史涨停股池（用于回溯验证） |

---

## 核心API（quicktiny.py）

### 数据获取
```python
get_ladder(date)              # 连板天梯（今日涨停股池）
get_zt_stocks(date)           # 涨停股详情
get_kline_hist(code, days)   # 历史K线
get_kline_ohlcv(code, days)  # OHLCV数据
get_minute(code, ndays)       # 分时数据
get_market_overview_fixed(date) # 市场概览（温度/涨跌家数）
get_limit_stats(date)         # 涨停/跌停/炸板统计
get_limit_down(date)          # 跌停池
get_broken_limit_up(date)     # 炸板池
get_auction_for_codes(codes, delay) # 竞价数据批量获取
```

### 热点与资金
```python
get_hot_sectors(date)         # 热点板块 + AI分析（⭐最高价值）
get_capital_flow(date)       # 资金流向
get_capital_flow_v2(...)     # 增强版资金流向
get_limit_events(event_type)  # 封板/炸板事件流
get_approaching_limit_up(date) # 逼近涨停监控
get_anomalies(date)           # 异动检测
```

### 搜索与龙虎榜
```python
get_search(query)             # 股票搜索（名称/代码/行业）
get_rank(rtype)              # 涨跌幅排行
get_dragon_tiger(date)       # 龙虎榜 + 营业部席位明细
```

### 其他
```python
get_trading_calendar(date)    # 交易日判断
get_limit_up_premium(...)     # 涨停溢价分析（回测用）
get_limit_up_filter(date)     # 涨停过滤器
```

---

## 晋级预测模型（predict_calibrated.py）

### 输入
- 股票代码 + 名称
- 连板数（from ladder）
- 竞价数据（from auction）
- K线指标（MA20/MA60/RSI/VR/MACD）
- 市场阶段（from market_sentiment）

### 评级规则
| 条件 | 评级 | 买入概率 |
|------|------|---------|
| veto_reasons≥1 OR warn_count≥2 | C级 | 0% |
| warn_count=1 OR susp_count≥2 | B级 | 30% |
| susp_count=1 | A级 | 50% |
| all ok | S级 | 100% |

**特殊规则**：
- 退潮/恐慌期 + 连板≥3板 → 强制降C
- 一字板高开：联合封板率判断（<70% warn，70-85% susp，≥85% ok）

### 竞价买入方式
| 竞价涨幅 | 买入方式 |
|---------|---------|
| 0~3% | 竞价买入 或 9:30开盘买 |
| 3~7% | 等回调至涨幅一半位置 |
| >7% | 等回调至75%位置，超过原涨幅+2%放弃 |
| 一字板涨停 | 无法买入（排队等炸板回封） |

---

## 风控引擎（risk_control.py）

**七层风控（优先级从高到低）**：

1. **止损**：-4%（绝对红线）
2. **炸板回落**：龙头≥6% / 非龙头≥4%
3. **目标价止盈**：达到预设目标价
4. **动态止盈**：高点回落40% + 浮盈≥6%
5. **浮亏处理I**：-2% → 降仓50%
6. **浮亏处理II**：-5% → 全清
7. **持仓超期**：超过max_days强制平仓

---

## 自动化定时任务

| 时间 | 脚本 | 说明 |
|------|------|------|
| 9:26（周一~五） | `auto_buy.py` | 竞价自动买入 |
| 每5分钟（10~14时） | `auto_monitor.py` | 盘中持仓监控 |
| 15:05（周一~五） | `auto_close.py` | 收盘自动平仓 |
| 23:00（周一~五） | `evening_preselect.py` | 晚盘预选报告 |

---

## 使用示例

### 竞价筛选（手动）
```bash
cd /home/gem/workspace/agent/workspace
python3 scripts/astock_auction_full.py
```

### 盘中查询单只股票
```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from astock.predict_calibrated import predict_stock_v2
from astock.quicktiny import get_auction_for_codes, get_ladder

date = '20260325'
lad = get_ladder(date)
codes = [s['code'] for s in lad['items'][:5]]  # 前5只
auction = get_auction_for_codes(codes, delay=3)
result = predict_stock_v2(codes[0], '测试股', auction[0] if auction else None)
print(result)
"
```

### 查看持仓
```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from astock.position import load_portfolio
for p in load_portfolio():
    print(p)
"
```

### 回测数据缓存
```bash
python3 scripts/quicktiny_cache.py 15  # 缓存最近15个交易日
```

### 龙虎榜回测
```bash
python3 scripts/backtest_dragon_tiger.py
```

---

## 关键配置

| 配置项 | 位置 | 说明 |
|--------|------|------|
| quicktiny API Key | `.env` → `LB_API_KEY` | 数据源 |
| 初始资金 | `astock/config.py` → `CAPITAL` | 默认100万 |
| 评级概率 | `astock/predict_calibrated.py` → 内部常量 | S/A/B/C四级 |
| 板块梯队权限 | `astock/pools/board_tier.py` | HIGH梯队仅主升期开放 |
| max_days | `positions` 表 per-record | 龙3板持仓2夜，其他1夜 |

---

## 数据库路径
```
/home/gem/workspace/agent/workspace/data/astock/model/astock.db
```

---

## 常见问题

**Q: 竞价数据获取失败（限流）？**
→ 把 `get_auction_for_codes(codes, delay=0)` 改为 `delay=3~5`，避开9:25-9:26峰值

**Q: baostock 并发报错？**
→ `get_kline_hist` 使用串行加载（不用 ThreadPoolExecutor），避免UTF-8解码错误

**Q: 退潮期选股太宽？**
→ `board_tier.py` 中 HIGH 梯队（6板+）在退潮/分歧/冰点期自动关闭

**Q: 概率晋级率预测偏低？**
→ 已校准：实际晋级率约为预测值的2~3倍，见 `calibration_v2.py` 校准表

---

*最后更新：2026-03-25*
