# 连板打板/抓涨停启动研究模块（`davis_analyzer/limitup`）设计规格

> 状态：待用户审阅（v1 草案，2026-08-16）
> 关联：`AGENTS.md`（协作规范）、`stockhot/limit_up`（数据采集）、`davis_analyzer/backtest.py`（可复用组件）

## 1. 背景与目标

在 davis_analyzer 中新开一个独立子包 `limitup`，用于**研究并实践**两类 A 股短线战法：

1. **首板启动**（抓涨停启动）：个股在长期无涨停后拉出首板，视为主力启动信号，打板买入。
2. **连板接力**（连板打板）：个股已有 ≥2 连板，在其下一板打板买入，博弈晋级。

**成功标准**：

- 研究腿：基于 ≥3 年涨停事件数据，产出可复现的晋级率矩阵、打板次日收益分布、特征分桶有效性结论、情绪周期溢价指标，形成 markdown 研究报告。
- 回测腿：事件驱动回测（含成交概率、T+1 约束、费用）给出各策略预设**费后期望与敏感性**，判断战法是否有正期望。
- 实践腿（Phase 3）：盘后生成次日打板候选清单，并可接入 paper_trading 模拟盘。

**非目标（本期不做）**：分钟线/实时行情接入、日内封板瞬间撮合模拟、实盘下单、ST/北交所个股策略。

## 2. 已采用的默认决策（用户未应答澄清问题，按最优判断取默认，审阅时可否决）

| # | 问题 | 默认决策 | 理由 |
|---|------|----------|------|
| D1 | 研究 vs 实践优先级 | 研究先行，分 4 阶段交付 | 数据须先回补；未验证期望的打板策略直接上模拟盘意义有限 |
| D2 | 支持哪些战法变体 | 首板 + 连板接力（2 板/3 板）共用一套引擎，仅过滤配置与卖出规则不同 | 用户原话同时提到两种战法 |
| D3 | 无分钟线约束 | 日线 + 封板时间字段近似模拟，成交概率做三档敏感性 | 全项目无分钟数据，AGENTS.md 约束 Tushare 为唯一外部数据源 |
| D4 | 模块形态 | 独立子包 + 模块级 CLI（仿 `paper_trading` 模式），不改主 pipeline | 不污染现有 8 步筛选管线 |
| D5 | 回补数据落点 | 回补进共享库 `market_data.db` 的 `limit_pool` 表（YYYY-MM-DD 格式，与现有一致） | 单一事实源；stockhot 读取路径不受影响 |

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
├── events.py       # Phase 1：涨停事件表构建（事件定义 + 特征工程）
├── sentiment.py    # Phase 1：情绪周期指标（涨停家数/连板高度/昨日涨停溢价/晋级率时序）
├── study.py        # Phase 1：事件研究（晋级率矩阵、收益分布、特征分桶）
├── engine.py       # Phase 2：事件驱动回测引擎
├── strategies.py   # Phase 2：策略预设（首板/接力2板/接力3板）+ 卖出规则配置
├── report.py       # Phase 1/2：markdown 报告输出
├── cli.py          # 子命令编排
└── __main__.py     # python -m davis_analyzer.limitup {backfill|study|backtest|candidates}

tests/test_limitup_backfill.py / _events.py / _study.py / _engine.py / _strategies.py
```

依赖方向（与项目总架构一致，自上而下单向）：

```
cli.py → engine.py / study.py → strategies.py → events.py / sentiment.py → backfill.py / 共享数据层
```

## 5. 数据层设计（Phase 0）

### 5.1 回补

- 数据源：Tushare `limit_list_d`（limit_type=U/Z/D 一接口三用），**复用** `stockhot/limit_up/_fetch_pool_via_tushare` 的字段映射（`limit_times→consecutive_boards`、`fd_amount→seal_amount`、`open_times→broken_count`、`first_time/last_time→首末封板时间`、`industry→sector`）。
- 交易日历：从 `daily_price` 表 `SELECT DISTINCT trade_date` 推导（复用 `_calendar_from_union` 思路），不调专用日历 API（铁律）。
- 写入：`market_data.db`.`limit_pool`，`INSERT OR REPLACE`，trade_date 统一 **YYYY-MM-DD**（与表内现存 38 天数据一致；与其他表 YYYYMMDD 不同是已知历史问题，events 层负责归一）。
- 断点续传：按日检查 `limit_pool` 已有覆盖，跳过已有日期；参照 `scripts/backfill/backfill_top_list.py` 模式。
- 限流预算：约 1350 交易日 × 3 pool ≈ 4000 次调用，400/min 限流下约 10–15 分钟，可分批跑。

### 5.2 历史深度风险（必须在 Phase 0 验证）

`limit_list_d` 的可回溯起点未经验证。Phase 0 先探测最早可用日期：

- 若覆盖 ≥3 年（含 2023–2026 完整）：直接进入 Phase 1。
- 若不足：**降级方案**——用 `daily_price` 按涨停价规则（`close == round(pre_close × 涨幅限制, 2)`，主板 10% / 创业板科创板 20%，用 `stock_basic` 区分）自行重构涨停事件，仅支撑收益分布研究（无封单额/炸板/首封时间特征），封单类特征研究只用 `limit_list_d` 覆盖窗口。

### 5.3 股票池与价格口径

- 默认剔除：ST/*ST（名称判定）、北交所（8/4 开头）、上市 <60 个自然日（次新股波动异形，可配置）。
- 价格口径：**不复权价**做涨停判定与收益计算（涨停价规则基于不复权 pre_close）；跨除权日的连板序列判定需比对 `adj_factor` 变化，跳跨越除权日的连板（保守处理，宁少勿错）。

## 6. 事件与特征（Phase 1）

### 6.1 事件定义

一行 = 一个 `(ts_code, trade_date)` 涨停事件（收盘涨停，pool_kind='limit_up'）。核心字段：

- 身份：ts_code、name、trade_date、sector、流通市值（daily_basic 或 limit_list_d float_market_value）
- 连板状态：consecutive_boards（1=首板）、炸板次数 broken_count、首末封板时间
- 封板质量：seal_amount（封单额）、seal_ratio = seal_amount / 流通市值
- 前置状态：事件前 60 日涨停次数、20 日涨幅位置、是否突破平台（可后置到 Phase 1.5）

### 6.2 事件后收益标签（由 daily_price 计算）

- `ret_open_1` = T+1 开盘 / T 涨停价 − 1（**打板口径的核心收益**：成本=涨停价）
- `ret_close_1`、`ret_high_1`（次日冲高）、`ret_low_1`（次日最大回撤）
- `ret_3d / ret_5d`（持有 3/5 日收盘）
- `promoted` = T+1 是否继续收盘涨停（晋级）

### 6.3 情绪周期指标（sentiment.py，按日）

- 涨停家数、炸板率、连板家数、**最高板高度**
- **昨日涨停今日溢价**：昨日涨停池等权平均 `ret_open_1` 与红盘率（打板情绪温度计）
- 晋级率时序：1进2 / 2进3 / 3进4+
- 输出为逐日 DataFrame，既作研究产出，也作回测引擎的**环境过滤器**（如仅在"溢价 > -1%"时开仓）

## 7. 事件研究（Phase 1，study.py）

1. **晋级率矩阵**：P(晋级 | 当前板数 × 特征分桶)，全市场基础矩阵 + 按封单比/首封时间/板块联动分桶。
2. **打板收益分布**：`ret_open_1` 的均值/中位数/胜率/盈亏比/分位数，按策略预设的核心维度分桶对比。
3. **特征有效性**：每个候选特征分桶后组间 `ret_open_1` 期望差，输出排序（不做复杂 IC，够用即可）。
4. **情绪环境切片**：不同情绪档位下同一策略的期望对比（验证"冰点后回暖开仓"类择时假设）。

产出：markdown 研究报告写入 `limitup/reports/`（config.py 新增 `LIMITUP_REPORTS_DIR`，沿用 STUDIES_DIR 的目录创建模式）。

## 8. 事件驱动回测引擎（Phase 2，engine.py）

### 8.1 主循环语义（与周期再平衡回测的本质差异）

事件驱动：信号日 T 涨停事件触发 → T 日以涨停价挂单打板 → T+1 起按卖出规则离场。每日先处理卖出再处理新买入；持仓生命周期由事件决定而非调仓日。

### 8.2 成交概率模型（无分钟线的核心妥协）

买入价恒为涨停价；是否成交由概率模型决定（参照并扩展 `_limit_up_fill_probability`）：

| 情形 | 基准概率 |
|------|----------|
| 一字板（open=low=涨停价） | 0.05 |
| 早盘封板且未炸板（first_seal < 10:00 且 broken_count=0） | 0.20 |
| 其余未炸板 | 0.35 |
| 炸板后回封（broken_count>0 且收盘涨停） | 0.70 |

三档场景系数：乐观 ×1.5、基准 ×1.0、悲观 ×0.5（截断到 [0.05, 0.95]），回测报告必须三档并列展示敏感性。概率用固定种子随机数（可复现），并提供 `--fill-scenario always` 上界参照。

### 8.3 卖出规则（strategies.py 配置化）

- `open_next`（默认）：T+1 竞价卖出，成交价 = T+1 开盘 × (1 − 滑点 bps，默认 10bps)
- `ride_board`：连板持有——若持有日继续收盘涨停则顺延，断板日次日开盘卖出
- `close_next`：T+1 收盘卖出
- **跌停无法卖出**：卖出日跌停（close=low=跌停价）则顺延至下一可卖日，按次日开盘成交（现实约束，必须建模）

### 8.4 资金管理与费用

- 初始资金默认 100 万；单票上限 = 总权益 / max_positions（默认 3）；每日候选多于额度时按预设配置的排序键取前 N（默认 `seal_ratio` 降序）。
- 费用复用 `_trade_cost` 口径：佣金双边 2.5bps + 印花税卖方 10bps；买入额外加滑点 0（涨停价限价单）但成交概率已内含成本。
- 每日按收盘价 MTM 生成净值序列 → 复用 `PerformanceStats`/`compute_performance` 出收益/夏普/回撤/胜率，另加**单笔交易明细 CSV**（参照 backtest_report 导出模式）。

### 8.5 策略预设

| 预设 | 入场过滤 | 卖出 |
|------|----------|------|
| `first_board` | consecutive_boards==1 且 60 日内涨停次数 ≤1 | open_next |
| `relay_2` | consecutive_boards==2 且 seal_ratio ≥ 中位 | ride_board |
| `relay_3` | consecutive_boards==3 | ride_board |
| 全部可选 | 情绪过滤（昨日涨停溢价阈值）、板块联动 ≥K 家 | — |

预设参数集中在 `strategies.py` 的 dataclass 配置（**不触碰** `constants.py` 全局权重，规避 SOP 同步负担）。

## 9. 实践腿（Phase 3）

- `candidates` 子命令：盘后运行，取当日涨停事件 + 特征打分 → 输出次日打板候选清单 markdown（含未成交风险提示：一字板概率、封单比）。
- paper_trading 集成：新增 `BoardChasingStrategy` 实现 `Strategy` Protocol（`evaluate(positions, snapshot, total_equity)`），复用 `run_backfill_auto` 历史回放；涨停买入约束复用 executor 现有概率折减逻辑。
- 可选：沉淀为 `.agents/skills/` 下的盘后工作流（仿 daily-market-scan），本期只留接口不落地。

## 10. CLI

```
python -m davis_analyzer.limitup backfill  --start 20230101 [--end YYYYMMDD]   # Phase 0
python -m davis_analyzer.limitup study     --start 20230101 --end 20260814     # Phase 1
python -m davis_analyzer.limitup backtest  --preset first_board --fill-scenario base  # Phase 2
python -m davis_analyzer.limitup candidates --date 2026-08-15                  # Phase 3
```

均在**父仓库根目录**运行（AGENTS.md 架构事实 1）。输出遵守 loguru 规范，`print` 仅限 cli.py。

## 11. 测试策略

- `test_limitup_events.py`：涨停事件构建（复权跨越剔除、次新剔除、日期格式归一）、收益标签计算（涨停价口径）
- `test_limitup_study.py`：晋级率矩阵数值、分桶边界、情绪指标
- `test_limitup_engine.py`：成交概率场景（固定种子）、T+1 卖出、跌停顺延、费用/整手、一字板低概率、净值与绩效对接
- `test_limitup_backfill.py`：断点续传幂等（mock client）
- fixture 走 `tests/conftest.py` 既有模式（DataFrame fixture + MagicMock client）

## 12. 分阶段交付

| 阶段 | 交付物 | 依赖 |
|------|--------|------|
| Phase 0 | backfill.py + 完整性校验 + 历史深度探测结论 | 无 |
| Phase 1 | events.py + sentiment.py + study.py + 研究报告 | Phase 0 |
| Phase 2 | engine.py + strategies.py + 三档敏感性回测报告 | Phase 1 |
| Phase 3 | candidates CLI + paper_trading Strategy 适配 | Phase 2 结论为正期望（否则终止并出结题报告） |

## 13. 风险与开放问题

1. **`limit_list_d` 历史深度未知**（§5.2 降级路径已备）。
2. **成交概率模型不可精确校验**（无 tick 数据）：以三档敏感性 + `always` 上界披露不确定性，研究结论只报告区间。
3. **打板期望对滑点/概率极敏感**：若三档结论方向不一致，报告结论必须是"不确定"而非择优汇报（诚实性铁律）。
4. limit_pool 的 YYYY-MM-DD 与 daily_price 的 YYYYMMDD 差异：events 层统一入口转换，测试覆盖。
5. 龙虎榜数据（top_list，5.5 年）作为 Phase 1.5 候选特征（是否上榜/净买额），本期仅预留 join 接口不实现。
