# 连板打板/抓涨停启动研究模块（`davis_analyzer/limitup`）设计规格

> 状态：v1.1，已按用户审阅反馈修订，待最终确认
> 修订记录：v1.0（2026-08-16 初稿）→ v1.1：①形态识别升级为独立模块与一等公民，新增统计稳健性规范（防过拟合/防稀疏）；②龙虎榜特征从"预留接口"升级为 Phase 1 实现；③大盘/局势环境扩展为指数趋势+市场宽度+情绪周期三轴；④新增消息面代理指标及其诚实边界；⑤流通市值数据源修正（daily_basic 仅 23 天历史，改用 limit_list_d 自带 float_market_value）。
> 关联：`AGENTS.md`（协作规范）、`stockhot/limit_up`（数据采集）、`davis_analyzer/backtest.py`（可复用组件）

## 1. 背景与目标

在 davis_analyzer 中新开一个独立子包 `limitup`，用于**研究并实践**两类 A 股短线战法：

1. **首板启动**（抓涨停启动）：个股在长期无涨停后拉出首板，视为主力启动信号，打板买入。
2. **连板接力**（连板打板）：个股已有 ≥2 连板，在其下一板打板买入，博弈晋级。

**用户明确的设计重点（v1.1 确认）**：

- **形态识别逻辑是核心**：涨停事件的 K 线/位置/量价形态决定其后市分化，且过滤条件"过紧（过拟合、样本稀疏）"与"过松（噪声淹没）"都会使结论大幅失真——必须有系统的稳健性规范约束。
- **连板与龙虎榜、大盘局势、消息面强相关**：这些都作为事件特征纳入研究，而不是只看个股自身。

**成功标准**：

- 研究腿：基于 ≥3 年涨停事件数据，产出可复现的晋级率矩阵、打板次日收益分布、**形态分类 × 环境切片**的有效性结论，形成 markdown 研究报告；所有结论附样本量与样本外验证。
- 回测腿：事件驱动回测（含成交概率、T+1 约束、费用）给出各策略预设**费后期望与敏感性**，判断战法是否有正期望。
- 实践腿（Phase 3）：盘后生成次日打板候选清单，并可接入 paper_trading 模拟盘。

**非目标（本期不做）**：分钟线/实时行情接入、日内封板瞬间撮合模拟、实盘下单、ST/北交所个股策略、新闻/公告文本的情绪解析（无数据源，见 §6.6）。

## 2. 已确认的决策

| # | 问题 | 决策 | 状态 |
|---|------|------|------|
| D1 | 研究 vs 实践优先级 | 研究先行，分 4 阶段交付 | v1.1 用户确认 |
| D2 | 支持哪些战法变体 | 首板 + 连板接力（2 板/3 板）共用一套引擎，仅过滤配置与卖出规则不同 | v1.1 用户确认 |
| D3 | 无分钟线约束 | 日线 + 封板时间字段近似模拟，成交概率做三档敏感性 | v1.1 用户确认 |
| D4 | 模块形态 | 独立子包 + 模块级 CLI（仿 `paper_trading` 模式），不改主 pipeline | v1.1 用户确认 |
| D5 | 回补数据落点 | 回补进共享库 `market_data.db` 的 `limit_pool` 表（YYYY-MM-DD 格式，与现有一致） | v1.1 用户确认 |
| D6 | 特征广度 | 形态识别 + 龙虎榜 + 大盘局势 + 消息面代理全部纳入 Phase 1 特征集 | v1.1 用户新增 |

## 3. 方案比选

| 方案 | 描述 | 结论 |
|------|------|------|
| **A. 独立子包 + 新事件驱动引擎（采用）** | `davis_analyzer/limitup/` 子包；主循环新写（事件=个股涨停），复用现有组件 | 回测语义正确；不碰现有 `backtest.py`（回归风险为零）；符合 paper_trading 先例 |
| B. 扩展现有 backtest.py 支持事件模式 | 在固定频率框架里加事件驱动分支 | 否决：`backtest.py` 是周期再平衡的单一职责模块，塞入事件语义会形成双语义巨石文件，威胁现有回测正确性 |
| C. studies/ 风格一次性研究脚本 | 不建正式模块，直接写研究脚本 | 否决：不可持续、无测试；用户要求"研究+实践"两条腿，需要正式工程 |

**复用清单（方案 A）**：`backtest.py` 的 `_trade_cost`/`_calendar_from_union`/`PerformanceStats`+`compute_performance`；`paper_trading/executor.py` 的 `_limit_up_fill_probability` 思路（扩展为特征驱动）；`stockhot.limit_up.fetch_limit_up_pool` 等取数函数（字段归一化逻辑已测试）；`MarketDataRepository.get_limit_pool/get_dragon_tiger`。

## 4. 模块结构

```
davis_analyzer/limitup/
├── __init__.py
├── backfill.py     # Phase 0：limit_list_d 历史回补 → limit_pool（断点续传）
├── events.py       # Phase 1：涨停事件表构建（事件定义 + 基础字段 + 量价特征 + 龙虎榜 join）
├── patterns.py     # Phase 1：形态识别（K 线形态 + 位置形态 + 板质量 → 形态标签分类学）
├── sentiment.py    # Phase 1：市场环境（指数趋势 + 市场宽度 + 情绪周期四档 regime）
├── study.py        # Phase 1：事件研究（晋级率矩阵、收益分布、形态×环境切片）
├── robustness.py   # Phase 1：统计稳健性规范实现（样本门槛/IS-OOS/参数扰动/报告纪律）
├── engine.py       # Phase 2：事件驱动回测引擎
├── strategies.py   # Phase 2：策略预设（首板/接力2板/接力3板）+ 卖出规则配置
├── report.py       # Phase 1/2：markdown 报告输出
├── cli.py          # 子命令编排
└── __main__.py     # python -m davis_analyzer.limitup {backfill|study|backtest|candidates}

tests/test_limitup_backfill.py / _events.py / _patterns.py / _study.py / _robustness.py / _engine.py / _strategies.py
```

依赖方向（与项目总架构一致，自上而下单向）：

```
cli.py → engine.py / study.py → strategies.py → patterns.py / events.py / sentiment.py / robustness.py → backfill.py / 共享数据层
```

## 5. 数据层设计（Phase 0）

### 5.1 回补

- 数据源：Tushare `limit_list_d`（limit_type=U/Z/D 一接口三用），**复用** `stockhot/limit_up/_fetch_pool_via_tushare` 的字段映射（`limit_times→consecutive_boards`、`fd_amount→seal_amount`、`open_times→broken_count`、`first_time/last_time→首末封板时间`、`industry→sector`）。
- 交易日历：从 `daily_price` 表 `SELECT DISTINCT trade_date` 推导（复用 `_calendar_from_union` 思路），不调专用日历 API（铁律）。
- 写入：`market_data.db`.`limit_pool`，`INSERT OR REPLACE`，trade_date 统一 **YYYY-MM-DD**（与表内现存 38 天数据一致；与其他表 YYYYMMDD 不同是已知历史问题，events 层负责归一）。
- 断点续传：按日检查 `limit_pool` 已有覆盖，跳过已有日期；参照 `scripts/backfill/backfill_top_list.py` 模式。
- 限流预算：约 1350 交易日 × 3 pool ≈ 4000 次调用，400/min 限流下约 10–15 分钟，可分批跑。
- **流通市值来源**：`limit_list_d` 自带 `float_market_value`，随回补一并入库（新增列或并入现有 schema 皆可，以不改 stockhot 读取路径为准）。`daily_basic` 仅 23 天历史（2026-07 起），**不可用作历史流通市值来源**（v1.1 修正）。

### 5.2 历史深度风险（必须在 Phase 0 验证）

`limit_list_d` 的可回溯起点未经验证。Phase 0 先探测最早可用日期：

- 若覆盖 ≥3 年（含 2023–2026 完整）：直接进入 Phase 1。
- 若不足：**降级方案**——用 `daily_price` 按涨停价规则（`close == round(pre_close × 涨幅限制, 2)`，主板 10% / 创业板科创板 20%，用 `stock_basic` 区分）自行重构涨停事件，仅支撑收益分布与环境切片研究（无封单额/炸板/首封时间特征），封单与形态中涉及封板过程的特征只用 `limit_list_d` 覆盖窗口。

### 5.3 股票池与价格口径

- 默认剔除：ST/*ST（名称判定）、北交所（8/4 开头）、上市 <60 个自然日（次新股波动异形，可配置）。
- 价格口径：**不复权价**做涨停判定与收益计算（涨停价规则基于不复权 pre_close）；跨除权日的连板序列判定需比对 `adj_factor` 变化，跳跨越除权日的连板（保守处理，宁少勿错）。

## 6. 事件特征体系（Phase 1，用户确认的核心）

事件 = 一个 `(ts_code, trade_date)` 收盘涨停（pool_kind='limit_up'）。特征分六层，全部可从本地库计算。

### 6.1 基础字段（events.py）

ts_code、name、trade_date、sector、流通市值（limit_list_d float_market_value）、consecutive_boards、broken_count、first/last_seal_time、seal_amount、turnover_rate、事件前 60 日涨停次数。

### 6.2 形态识别（patterns.py，一等公民）

**K 线形态**（来源 `intraday_feature`，5.5 年全量字段已验证）：实体比 body_ratio、上影线 upper_shadow、下影线 lower_shadow、跳空缺口 gap、收盘位置 close_position、振幅 amplitude。

**位置形态**（来源 `daily_price` 滚动窗口计算）：

- 平台突破：收盘价创 60 日新高 / 距 60 日前高 <2%，且前 40 日箱体振幅 <25%
- 趋势加速：价在 MA20 上方且 MA20 上行，20 日累计涨幅 15%–40%
- 超跌反弹：60 日累计跌幅 >30%，价格在 MA60 下方 >10%
- 长期横盘首板：120 日振幅 <20% 后的首板

**板质量**：seal_ratio = 封单额/流通市值、首封时间档（早盘 <10:00 / 午盘 / 尾盘 ≥14:00）、炸板次数、尾盘回封（last_seal ≥ 14:30）。

**形态分类学**：每个事件打 1–2 个主形态标签（突破型 / 趋势加速型 / 超跌反转型 / 横盘首板型），互斥优先级：突破 > 加速 > 横盘 > 超跌；无法归类的标"其他"。分类阈值全部用 §7 的粗粒度档位，禁止连续寻优。

### 6.3 量价特征（events.py）

放量倍数 = 当日成交量 / 20 日均量；涨停前 5 日温和放量天数（吸筹代理）；换手率分档（<10% / 10–20% / >20%）。

### 6.4 龙虎榜特征（events.py join top_list，Phase 1 实现，v1.1 升级）

top_list 表 2021-01→2026-07 共 1351 个交易日，字段已验证。每个事件 join 得到：当日是否上榜、l_buy/l_sell、net_amount 净买额、net_rate（净买/流通成交占比）、amount_rate（上榜成交占比）、reason 上榜原因文本（"连续三日涨幅偏离"等，可识别连板性质）、机构净买（top_inst join，回补脚本已有先例）。

### 6.5 大盘与局势环境（sentiment.py，v1.1 扩展为三轴）

逐日构建市场状态 DataFrame，事件按日期对齐：

- **指数趋势轴**（index_daily 已验证：上证/深成/创业板 2021→2026 各 1361 天）：三大指数 MA20/MA60 多空状态、指数 20 日涨幅、指数距 250 日高点位置。
- **市场宽度轴**（daily_price 自算，不依赖外部）：全市场涨跌家数比、创 20 日新高个股占比、全市场成交额 5 日均值分位（量能环境）。
- **情绪周期轴**（涨停池自算，即 v1.0 的情绪指标）：涨停家数、炸板率、连板家数、最高板高度、昨日涨停今日平均溢价与红盘率、晋级率（1进2/2进3/3进4+）。
- **regime 分档**：情绪周期按（昨日涨停溢价、晋级率、最高板高度）映射为四档——冰点 / 回暖 / 高潮 / 退潮。映射阈值用先验固定值（研究期一次性校准并冻结，不做滚动拟合）。

### 6.6 消息面代理（v1.1 新增，含诚实边界）

项目无新闻/公告文本数据源（corp_event 已验证仅含解禁/增减持/回购/质押四类结构化事件；Tushare 为唯一外部数据源约束下不引入新闻 API）。消息面用三个代理指标：

- **板块联动强度**：同日同 sector 涨停家数、板块内涨停占全市场涨停比例、板块连续几日有涨停（题材持续性代理）。
- **利空事件排雷**（corp_event）：事件前 30 日内是否有解禁（share_float）/大额减持（holder_trade），作为负面过滤特征。
- **研报覆盖热度**（可选，Phase 1.5）：`get_research_reports` 已封装，涨停日前后研报数量作为机构关注度代理；历史覆盖深度待验证，验证不足则砍掉。

明确边界：**没有**新闻快讯、公告全文、社交媒体情绪。研究报告中相关结论必须声明"消息面仅代理指标覆盖"。

### 6.7 事件后收益标签（daily_price 计算）

- `ret_open_1` = T+1 开盘 / T 涨停价 − 1（**打板口径的核心收益**：成本=涨停价）
- `ret_close_1`、`ret_high_1`（次日冲高）、`ret_low_1`（次日最大回撤）
- `ret_3d / ret_5d`（持有 3/5 日收盘）
- `promoted` = T+1 是否继续收盘涨停（晋级）

## 7. 统计稳健性规范（robustness.py，v1.1 新增——回应"过紧则稀疏过拟合、过松则噪声"）

1. **粗粒度档位原则**：所有特征阈值用先验分档（3–5 档），档位在研究开始前固定并写入报告，禁止对连续阈值做网格寻优后再汇报。
2. **样本量门槛**：收益分布类结论每桶 ≥30 个事件、晋级率类 ≥50，不足则标记"样本不足"，不参与特征排序与策略采纳。
3. **样本内/外切分**：按时间 70/30 切分（如 2023-01–2025-06 为 IS，2025-07–2026-08 为 OOS）；特征筛选、档位校准、regime 阈值只用 IS；OOS 只做方向一致性验证，不做二次调整。
4. **参数扰动检验**：核心阈值 ±20% 扰动重跑，IS 结论方向在扰动下不变才判"稳定"；不稳定特征降级为"观察项"。
5. **报告纪律**：每张分桶表强制列出样本数；全部扰动结果并列呈现；禁止只报最优桶；所有期望收益同时给中位数（防长尾均值误导）。
6. **过滤条件预算**：策略预设的入场过滤条件 ≤4 个；回测报告输出日均信号数，<0.5 笔/日即告警"过稀疏"，过滤松紧度由信号量与期望的权衡表呈现。

## 8. 事件研究（Phase 1，study.py）

1. **晋级率矩阵**：P(晋级 | 当前板数 × 形态标签 × 板质量档)，全市场基础矩阵 + 逐维分桶。
2. **打板收益分布**：`ret_open_1` 的均值/中位数/胜率/盈亏比/分位数，按形态标签 × 环境 regime 切片对比（例：突破型首板在回暖期的期望 vs 退潮期）。
3. **特征有效性排序**：每个候选特征（含龙虎榜/消息面代理）分桶后组间 `ret_open_1` 期望差排序，附 §7 的样本量与稳定性标记。
4. **环境择时验证**：同一策略预设在不同 regime 档位的期望对比（验证"冰点后回暖开仓"类假设）。

产出：markdown 研究报告写入 `limitup/reports/`（config.py 新增 `LIMITUP_REPORTS_DIR`，沿用 STUDIES_DIR 的目录创建模式）。

## 9. 事件驱动回测引擎（Phase 2，engine.py）

### 9.1 主循环语义（与周期再平衡回测的本质差异）

事件驱动：信号日 T 涨停事件触发 → T 日以涨停价挂单打板 → T+1 起按卖出规则离场。每日先处理卖出再处理新买入；持仓生命周期由事件决定而非调仓日。

### 9.2 成交概率模型（无分钟线的核心妥协）

买入价恒为涨停价；是否成交由概率模型决定（参照并扩展 `_limit_up_fill_probability`）：

| 情形 | 基准概率 |
|------|----------|
| 一字板（open=low=涨停价） | 0.05 |
| 早盘封板且未炸板（first_seal < 10:00 且 broken_count=0） | 0.20 |
| 其余未炸板 | 0.35 |
| 炸板后回封（broken_count>0 且收盘涨停） | 0.70 |

三档场景系数：乐观 ×1.5、基准 ×1.0、悲观 ×0.5（截断到 [0.05, 0.95]），回测报告必须三档并列展示敏感性。概率用固定种子随机数（可复现），并提供 `--fill-scenario always` 上界参照。

### 9.3 卖出规则（strategies.py 配置化）

- `open_next`（默认）：T+1 竞价卖出，成交价 = T+1 开盘 × (1 − 滑点 bps，默认 10bps)
- `ride_board`：连板持有——若持有日继续收盘涨停则顺延，断板日次日开盘卖出
- `close_next`：T+1 收盘卖出
- **跌停无法卖出**：卖出日跌停（close=low=跌停价）则顺延至下一可卖日，按次日开盘成交（现实约束，必须建模）

### 9.4 资金管理与费用

- 初始资金默认 100 万；单票上限 = 总权益 / max_positions（默认 3）；每日候选多于额度时按预设配置的排序键取前 N（默认 `seal_ratio` 降序）。
- 费用复用 `_trade_cost` 口径：佣金双边 2.5bps + 印花税卖方 10bps；买入为涨停价限价单不加滑点，但成交概率已内含机会成本。
- 每日按收盘价 MTM 生成净值序列 → 复用 `PerformanceStats`/`compute_performance` 出收益/夏普/回撤/胜率，另加**单笔交易明细 CSV**（参照 backtest_report 导出模式）。

### 9.5 策略预设

| 预设 | 入场过滤 | 卖出 |
|------|----------|------|
| `first_board` | consecutive_boards==1 + 形态标签（默认突破型或横盘首板型） | open_next |
| `relay_2` | consecutive_boards==2 且 seal_ratio ≥ 中位 | ride_board |
| `relay_3` | consecutive_boards==3 | ride_board |
| 全部可选 | regime 过滤（默认仅回暖/高潮档开仓）、板块联动 ≥K 家、利空事件排雷 | — |

预设参数集中在 `strategies.py` 的 dataclass 配置（**不触碰** `constants.py` 全局权重，规避 SOP 同步负担）。过滤条件数受 §7 第 6 条预算约束。

## 10. 实践腿（Phase 3）

- `candidates` 子命令：盘后运行，取当日涨停事件 + 特征打分 → 输出次日打板候选清单 markdown（含未成交风险提示：一字板概率、封单比）。
- paper_trading 集成：新增 `BoardChasingStrategy` 实现 `Strategy` Protocol（`evaluate(positions, snapshot, total_equity)`），复用 `run_backfill_auto` 历史回放；涨停买入约束复用 executor 现有概率折减逻辑。
- 可选：沉淀为 `.agents/skills/` 下的盘后工作流（仿 daily-market-scan），本期只留接口不落地。

## 11. CLI

```
python -m davis_analyzer.limitup backfill  --start 20230101 [--end YYYYMMDD]   # Phase 0
python -m davis_analyzer.limitup study     --start 20230101 --end 20260814     # Phase 1
python -m davis_analyzer.limitup backtest  --preset first_board --fill-scenario base  # Phase 2
python -m davis_analyzer.limitup candidates --date 2026-08-15                  # Phase 3
```

均在**父仓库根目录**运行（AGENTS.md 架构事实 1）。输出遵守 loguru 规范，`print` 仅限 cli.py。

## 12. 测试策略

- `test_limitup_events.py`：事件构建（复权跨越剔除、次新剔除、日期格式归一）、收益标签（涨停价口径）、龙虎榜 join
- `test_limitup_patterns.py`：四类位置形态的合成数据判定、K 线特征映射、形态标签优先级与互斥
- `test_limitup_study.py`：晋级率矩阵数值、分桶边界
- `test_limitup_robustness.py`：样本门槛过滤、IS/OOS 切分时序无泄漏、参数扰动生成
- `test_limitup_engine.py`：成交概率场景（固定种子）、T+1 卖出、跌停顺延、费用/整手、一字板低概率、净值与绩效对接
- `test_limitup_backfill.py`：断点续传幂等（mock client）
- fixture 走 `tests/conftest.py` 既有模式（DataFrame fixture + MagicMock client）

## 13. 分阶段交付

| 阶段 | 交付物 | 依赖 |
|------|--------|------|
| Phase 0 | backfill.py + 完整性校验 + 历史深度探测结论 | 无 |
| Phase 1 | events.py + patterns.py + sentiment.py + robustness.py + study.py + 研究报告（形态×龙虎榜×环境切片） | Phase 0 |
| Phase 2 | engine.py + strategies.py + 三档敏感性回测报告 | Phase 1 |
| Phase 3 | candidates CLI + paper_trading Strategy 适配 | Phase 2 结论为正期望（否则终止并出结题报告） |

## 14. 风险与开放问题

1. **`limit_list_d` 历史深度未知**（§5.2 降级路径已备）。
2. **成交概率模型不可精确校验**（无 tick 数据）：以三档敏感性 + `always` 上界披露不确定性，研究结论只报告区间。
3. **打板期望对滑点/概率极敏感**：若三档结论方向不一致，报告结论必须是"不确定"而非择优汇报（诚实性铁律）。
4. **流通市值历史缺失**（daily_basic 仅 23 天）：seal_ratio 等比值特征依赖 limit_list_d 的 float_market_value；若降级到 daily_price 重构路径（§5.2），seal_ratio 及相关板质量特征在重构窗口内不可用。
5. limit_pool 的 YYYY-MM-DD 与 daily_price 的 YYYYMMDD 差异：events 层统一入口转换，测试覆盖。
6. **消息面只有代理指标**：板块联动/利空排雷/研报热度无法替代真实新闻面，报告结论须声明覆盖边界；regime 阈值冻结后若未来市场结构变化需人工复审。
