# MEMORY.md - 你的长期记忆

每次醒来都会读这个文件。惜字如金，只留真正重要的。

## 索引

> `memory/` 下的文件索引。新建文件时在此添加条目。需要详情时再读取对应文件。

- YYYY-MM-DD.md: 每日日志
- learnings/：自我改进日志
  - LEARNINGS.md: 教训和发现（纠正、知识盲区、最佳实践）
  - ERRORS.md: 操作失败和异常记录
  - FEATURE_REQUESTS.md: 用户请求的缺失能力

## 用户画像

（在对话中逐步了解，在此记录。）

## 事件日志

（重要事件按时间记录。）

## 记忆

### A股超短预测系统 v3.3（2026-03-24更新）

**数据源优先级**：
1. K线：quicktiny HTTP API（`astock/quicktiny_kline.py`）> BaoStock
2. 涨停池/连板数/行业/原因：quicktiny ladder接口（`quicktiny.py`）
3. 竞价数据：quicktiny auction接口（9:25快照冻结）
4. 炸板/跌停池/涨跌停统计：quicktiny接口
   - `get_limit_stats(date)`：涨停/跌停/炸板率实时统计
   - `get_limit_down(date)`：跌停池
   - `get_broken_limit_up(date)`：炸板池（含封成率）
   - `get_market_overview_fixed(date)`：市场温度/涨跌家数

**评级规则（v3.3更新）**：
- veto_reasons≥1 OR warn_count≥2 → C级(0%)
- warn_count=1 OR susp_count≥2 → B级(30%)
- susp_count=1 → A级(50%)
- all ok → S级(100%)
- **退潮/恐慌期lb≥3板：强制降C**（宁可错过不做错）
- **一字板高开：联合封板率判断**（<70% warn，70-85% susp，≥85% ok）

**买入方式规则（v3.3新增）**：
- 竞价0-3%：竞价买入（9:15-9:25）或9:30开盘买
- 竞价3-7%：等回调至涨幅一半位置
- 竞价>7%：等回调至75%位置，超过原涨幅+2%放弃
- 一字板涨停：无法买入（排队等炸板回封）

**今日市场数据（20260324）**：
- 温度: 82.5 | 涨停: 47 | 跌停: 6 | 炸板: 0 | 阶段: 🚀主升
- 昨日（0323）：涨停28 封板率65.1% 跌停71 炸板15(28.3%) | 市场温度18.9

**数据源优先级**：
1. K线：quicktiny HTTP API（`astock/quicktiny_kline.py`）> BaoStock
2. 涨停池/连板数/行业/原因：quicktiny ladder接口（`quicktiny.py`）
3. 竞价数据：quicktiny auction接口（9:15/9:20/9:25快照）
4. 炸板/跌停池/涨跌停统计：quicktiny接口
   - `get_limit_stats(date)`：涨停/跌停/炸板率实时统计
   - `get_limit_down(date)`：跌停池
   - `get_broken_limit_up(date)`：炸板池（含封成率）
   - `get_market_overview_fixed(date)`：市场温度/涨跌家数

**今日市场数据（20260323）**：
- 涨停38（口径差异）封板率65.1% | 跌停71 | 炸板15(28.3%) | 市场温度18.9
- 昨日（0320）：涨停28 封板率54.9% 跌停13 炸板26(40%) | 市场温度23.4

**关键系数（已校准）**：
- BASE_PROBS（20260323从26228样本更新）：{1:0.22, 2:0.22, 3:0.20, 4:0.12, 5:0.35}
- PHASE_BASE_DISCOUNT：退潮×0.55，冰点×0.55，启动×0.85，发酵×1.00
- 预测系统性**低估**晋级概率，实际晋级率是预测值的2-3倍

**概率校准表（jb_prob分档→实际晋级率）**：
- 0-5%→19.4%，5-10%→19.9%，10-15%→25.8%，15-20%→39.2%，20-25%→33.9%，25-30%→34.3%，30%+→41.1%

**续涨率**：各档约50-64%，远高于晋级率22.6%，是主要盈利来源
**断板预警**：RSI>75才是真正信号（断板率22%），vr<0.5缩量反而不易断板（8.7%）
**5板+妖股**：晋级率40%，续涨率60%，断板率0%，独立策略

**环境变量**：`.env`文件存在工作空间，API Key在运行时通过`os.environ.get()`读取
**quicktiny Key**：`lb_c5d7beae8177a7700509ef04f48bff5909699e742c0a71f835554ad19b706bfd`

### 教训

**akshare限流**：真实环境中`stock_zt_pool_em`等接口响应12秒以上，切换为quicktiny解决
**parquet磁盘争用**：8个batch文件并发读导致超时；合并为单文件`all_klines.parquet`解决
**import顺序**：多import时后导入覆盖前导入，本项目中predict_calibrated被predict覆盖，需单独维护
**signal类型**：predict返回list但db.py需string，formatter需字符串，修复时注意类型转换

**退潮期高位板误判（20260324）**：大胜达3板+退潮期→B级30%买入→未涨停；修复：退潮/恐慌期lb≥3板强制降C
**一字板规则过严（20260324）**：中利集团一字板高开>9%warn，但封板率58%≠断板；修复：联合封板率判断（<70% warn，70-85% susp，≥85% ok）
**竞价买入方式（20260324）**：0-3%竞价买，3-7%等回调50%，>7%等回调75%+放弃线；一字板无法买入
**quicktiny板块接口**：get_concept_ranking和get_sector_analysis均返回空数据，暂无全板块成分股接口

**T+1规则升级（20260324）**：今日新仓不允许盘中/收盘平仓，只能T+1及之后平仓；buy_date等于今天→静默跳过
**持仓周期管理（20260324）**：龙3板+持2夜max_days=2，普通持1夜max_days=1；超期强制平仓
**流动性约束（20260324）**：一字板跳过，竞价挂单<5000手跳过；positions表需max_days字段
**成本体系（20260324）**：买入佣金0.03%+滑点0.5%；卖出佣金0.03%+印花税0.1%+滑点0.5%
**alpha因子体系v1（20260324）**：分层股票池(astock/pools/)；连板梯队分档(board_tier.py)；ML模型骨架(ml_model.py)；黑名单机制
**board_tier阶段权限**：HIGH梯队6板+仅主升期开放，分歧/退潮/冰点均关闭

### 今日教训
- **INSERT列值不匹配**：add_position添加max_days字段后 VALUES未同步添加，导致sqlite3.OperationalError: 16 values for 17 columns
- **py_compile不查运行时**：语法OK不等于逻辑OK，必须跑end-to-end验证
- **迁移脚本同步**：CSV迁移SQLite时也要同步添加新字段（max_days）
