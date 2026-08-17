# 策略锦标赛/模块竞争裁判（`davis_analyzer/tournament`）设计规格

> 状态：v1.0 初稿，待用户审阅
> 修订记录：v1.0（2026-08-17 初稿，方向已获用户口头批准：「三层递进、先做锦标赛」）
> 关联：`AGENTS.md`（协作规范）、`docs/superpowers/specs/2026-08-16-limitup-module-design.md`（统计稳健性纪律的来源）、`davis_analyzer/backtest.py` / `backtest_report.py`（复用组件）、`market_regime.py`（regime 标签）

## 1. 背景与目标

### 1.1 三层路线图定位（因子进化论）

用户提出的「量化因子进化论」经论证拆为三层，本规格只实现第一层：

| 层级 | 内容 | 状态 |
|------|------|------|
| **L1 选择层（本规格）** | 模块不动，统一裁判下按滚动 OOS 表现 + regime 匹配度分配资金（锦标赛轮动） | 本期实现 |
| L2 变异层（受控参数进化） | 基因组限于人工划定的权重/阈值区间，walk-forward + 多重检验校正，晋升参数冻结版本化 | 另立规格，仅预留接口（§10） |
| L3 合成层（GP/RL 公式发明） | 算子树自动合成新因子 | 暂缓，现有数据约束下过拟合与未来函数风险不可控 |

**核心论证结论**（为何选择层先行）：

1. 「竞争」的基建已存在：`run_backtest` 本身是竞争排名引擎（竞争发生在股票间），`FactorConfig` 是天然的参数载体，`compute_performance` 是现成适应度函数；缺的只是模块间的统一裁判。
2. 「进化」的主要风险是多重检验与 OOS 消耗，不是算法；L1 零新增寻优面，纯选择。
3. 锦标赛最值钱的产物是副产品：**模块在各 regime 下的相对表现序列**——它是策略轮动的 meta 因子，可能比任何进化出的单因子更有用。

### 1.2 成功标准

- 至少 3 个参赛者（davis 双击预设变体 ≥2 + 指数基准 ≥1）跑通 ≥3 年历史回放的季度锦标赛，产出结构化 markdown 报告。
- 防前视性质有自动化测试证明：篡改任一时点 T 之后的数据，T 时点的评分结果不变。
- 报告含：参赛者×窗口表现矩阵、regime 切片矩阵、相对表现序列（meta 因子数据，CSV 导出）、建议权重及置信度、数据假设明细（成本/成交假设/样本量/N/A 原因）。
- 所有评分与分配参数版本化进 OOS 台账；同一版本参数不得因单次结果不佳而连续微调。

### 1.3 非目标（本期不做）

- L2 参数进化沙箱与 L3 公式合成（§10 仅留接口）。
- 自动资金调拨、实盘下单、paper_trading 自动读取建议权重（产出仅供人工决策）。
- 接入尚未建成的模块实现（连板、周期反转埋伏——adapter 协议本期只做兼容性预留，不写空壳实现之外的逻辑）。
- 新因子开发、对现有因子模块的任何改动。

## 2. 已确认的决策

| # | 问题 | 决策 | 状态 |
|---|------|------|------|
| D1 | 进化层级 | L1 策略层竞争先行，L2/L3 另立规格 | 2026-08-17 用户批准 |
| D2 | 模块形态 | 独立子包 + 模块级 CLI（沿用 `paper_trading`/`limitup` 先例），不改主 pipeline | 沿用 D4 先例 |
| D3 | 初期参赛者 | davis 引擎的冻结预设变体（均衡/动量倾斜/估值倾斜）+ 上证指数基准；连板/周期反转就位后接入 | 本规格确定 |
| D4 | 适应度口径 | 统一 `PerformanceStats` + regime 切片；同 horizon 组内排名，组间只做风险预算展示，禁止跨 horizon 单一夏普排名 | 本规格确定 |
| D5 | 纪律对齐 | OOS 台账 + 参数版本冻结，对齐 limitup 规范「禁止连续寻优、阈值冻结」 | 本规格确定 |
| D6 | 产出形态 | 报告 + 建议权重（人决策），不自动执行 | 本规格确定 |
| D7 | 窗口语义 | 每评估窗口独立起跑（等额初始资金子回测），窗口间无路径依赖 | 本规格确定 |

## 3. 方案比选

| 方案 | 描述 | 结论 |
|------|------|------|
| **A. 独立子包 + Adapter 协议归一（采用）** | `davis_analyzer/tournament/` 子包；各模块经 `ModuleAdapter` 协议输出归一化权益曲线，裁判层统一评估 | 不碰现有模块，零回归风险；连板（事件驱动）与周期再平衡（定期调仓）两种引擎语义在 adapter 层吸收，裁判层保持单一职责 |
| B. 扩展 `backtest.py` 加多策略对比 | 在现有引擎里加对比循环 | 否决：`backtest.py` 是周期再平衡单一职责引擎，塞入裁判语义形成巨石文件，威胁现有回测正确性 |
| C. studies/ 风格一次性对比脚本 | 不建正式模块，直接写对比脚本 | 否决：不可复现、无防前视约束、无法滚动产出 meta 序列；与「进化论」需要的纪律根本冲突 |

**复用清单（方案 A）**：`backtest.py` 的 `BacktestConfig`/`run_backtest`（davis 系参赛者的执行引擎）、`EquitySnapshot`（adapter 归一化载体）；`backtest_report.py` 的 `PerformanceStats` + `compute_performance`（适应度计算）；`market_regime.py` 的 `get_market_regime_with_confirm`（regime 标签）；`tushare_client.TushareClient`（数据层）。

## 4. 模块结构

```
davis_analyzer/tournament/
├── __init__.py
├── adapters.py    # ModuleAdapter 协议 + RunResult + DavisPresetAdapter + IndexBenchmarkAdapter
├── judge.py       # JudgeHarness：滚动窗口调度、防前视铁律、最小样本门槛、regime 切片
├── scorecard.py   # 滚动 OOS 表现分（半衰期）+ regime 匹配度分 → 合成总分
├── allocator.py   # 风险预算分配（权重上下限、归一化）
├── ledger.py      # OOS 使用台账 + 参数版本冻结（晋升/版本纪律的唯一执行点）
├── report.py      # markdown 锦标赛报告 + meta 序列 CSV 导出
├── cli.py         # 子命令编排
└── __main__.py    # python -m davis_analyzer.tournament {run|replay|list}

tests/test_tournament_adapters.py / _judge.py / _scorecard.py / _allocator.py / _ledger.py
```

依赖方向（自上而下单向，与项目总架构一致）：

```
cli.py → judge.py → adapters.py → backtest.py / tushare_client.py
      │         ↘ market_regime.py（regime 标签，只读）
      → scorecard.py → allocator.py → report.py
      → ledger.py（全程旁路审计，被 judge/scorecard/allocator 调用）
```

配置：`config.py` 增加 `TOURNAMENT_REPORTS_DIR`（仿 `LIMITUP_REPORTS_DIR` 先例）。冻结参数集中于根 `constants.py` 新增 `TOURNAMENT_*` 段（评分权重是「评分权重」，受单一真相源铁律约束，需与 `SOP.md` 及 `tests/test_doc_consistency.py` 同步）。

## 5. 核心设计

### 5.1 ModuleAdapter 协议（归一化层）

```python
@dataclass
class RunResult:
    """一个参赛者在单一窗口内的归一化结果."""
    equity_curve: list[EquitySnapshot]   # 复用 backtest.py 的 dataclass
    trades: list[Trade]                  # 复用，用于样本门槛与换手统计
    assumptions: dict[str, str]          # 成本/成交假设自述，报告强制展示

@runtime_checkable
class ModuleAdapter(Protocol):
    name: str                # 参赛者唯一名，如 "davis_balanced"
    horizon: str             # "periodic" | "event" | "passive"，分组用
    version: str             # 参赛者自身参数版本（预设权重向量的指纹）
    def run_window(self, client: TushareClient, start: date, end: date) -> RunResult | None: ...
```

- **DavisPresetAdapter**：包装 `run_backtest`，预设 = 冻结的 `FactorConfig` 权重向量（`davis_balanced` 默认权重 / `davis_momentum_tilt` / `davis_valuation_tilt`）。预设一经登记即冻结、改动需版本 bump（Phase 2 起由 ledger 强制执行）。
- **IndexBenchmarkAdapter**：被动持有指数（上证指数 `000001.SH`，即日历锚，缓存必有；可选 000300.SH/000905.SH，缓存缺失时跳过并注明）。无交易成本（买入持有）。
- **返回 `None` 语义**：窗口内数据不足（如缓存未覆盖）→ 该参赛者该窗口记 N/A，不参与排名。

**成本现实性规则**：各参赛者用各自引擎的**最保守**现实性假设（周期引擎：佣金 2.5bps + 印花税 10bps；连板就位后：成交概率三档取中位档），假设明细由 `assumptions` 自述、报告强制展示——不同构的成本模型不做强行统一，只做披露。

### 5.2 JudgeHarness（裁判铁律）

1. **滚动评估**：评估点每 63 个交易日（季度）一个；任一评估点 T 的评分只使用 T 之前已实现窗口的数据（point-in-time，代码层面由 judge 唯一控制窗口边界，adapter 不感知评估点序列）。
2. **独立窗口**：每窗口等额初始资金的独立子回测（D7），窗口间无路径依赖，保证参赛者间可比。
3. **最小样本门槛**：窗口内交易日 < 40 或成交笔数 < 10 → N/A（对齐 limitup 规范的样本门槛思想；passive 组豁免成交笔数门槛）。
4. **regime 切片**：每个窗口用 `get_market_regime_with_confirm`（HMM 三态 + MA 确认，标签函数版本进台账）打一个标签，产出 模块×regime 表现矩阵。
5. **分组**：同 `horizon` 组内排名；跨组仅并列展示（D4）。regime 标签输出字符串以 `market_regime.py` 当前实现为准，裁判不硬编码标签值。

### 5.3 ScoreCard（评分）

- `trailing_score`：最近 4 个有效窗口的表现分，窗口权重按半衰期 2 个窗口指数衰减。窗口表现分初始冻结公式：`夏普比率 − 0.1 × |最大回撤%|`。
- `regime_match_score`：当前 regime 下该参赛者全部已实现窗口的加权表现（同样只用历史数据）。
- 合成总分 = 0.6 × trailing + 0.4 × regime_match（初始冻结值）。
- 有效窗口数 < 2 时，合成总分置 N/A，报告标注「参考性结论」。

### 5.4 Allocator（资金分配建议）

- 权重 ∝ `exp(总分 / τ)`（τ = 0.5，冻结初值），归一化后强制夹在 `[0.05, 0.50]`（下限保多样性、上限防赢家通吃/追策略热点）。
- 输出：各参赛者建议权重 + 置信度（基于有效窗口数与样本量），**仅写报告，不触碰 paper_trading**。
- 存在 N/A 参赛者时其权重固定为下限，并在报告说明。

### 5.5 ledger.py（OOS 台账与版本纪律）

反过拟合宪法的唯一执行点：

- 每次 `run`/`replay` 追加一条记录（SQLite 表 `tournament_ledger`，落在共享 `market_data.db`）：评估日期、参赛者清单及版本、评分/分配参数版本、OOS 窗口使用计数。
- **参数版本冻结**：`TOURNAMENT_*` 常量与参赛者预设均带版本号；修改必须 bump 版本并留记录；台账检测到「同一版本参数在同窗口上重复评估产生不同结论的连续微调模式」时，报告顶部强制警告（软约束 + 显式披露）。
- L2 晋升门槛的判定逻辑本期不实现；ledger 本期只提供版本记录与 OOS 使用计数的通用机制，门槛具体数值（deflated Sharpe 下限、±20% 参数扰动稳健、OOS 评估次数上限）由 L2 规格立项时定义。

## 6. 数据与依赖

- 价格数据全走 SQLite 缓存（与 backtest 铁律一致）；因子打分可能触发首次运行的增量缓存填充（复用 `score_universe_at` 现有行为），其后为纯缓存读。
- 交易日历：沿用 `backtest.py` 锚定股票推导法，不调专用日历 API。
- regime 标签：`market_regime.py` 现有函数，只读；其 HMM 模型重训视为标签函数版本变化，进台账。
- 无新增 Tushare 接口依赖。

## 7. 分阶段交付

| 阶段 | 内容 | 交付物 |
|------|------|--------|
| Phase 1 裁判与报告 | `adapters.py`（davis 预设×3 + 指数基准）、`judge.py`、`scorecard.py`、`report.py`、`config.py`/`constants.py`/SOP 同步 + 全套测试 | 第一份真实锦标赛报告（并列展示，暂不分配） |
| Phase 2 分配与回放 | `allocator.py`、`ledger.py`、`replay` 子命令（历史 walk-forward 回放 → meta 序列 CSV + 锦标赛自身的前向模拟业绩曲线，用于检验「选择层」是否真的增值） | 建议权重报告 + meta 因子序列数据 |
| Phase 3 接入新模块（条件触发） | 连板 adapter（依赖 limitup Phase 2 引擎落地）、周期反转 adapter（依赖该模块立项） | 新参赛者接入，协议零改动 |

## 8. 测试策略

- `test_tournament_adapters.py`：合成权益曲线 → 归一化 `RunResult` → `compute_performance` 数值正确；数据不足返回 None。
- `test_tournament_judge.py`：**防前视性质测试**（篡改/删除评估点 T 之后的所有数据，断言 T 时点评分不变）；最小样本门槛触发 N/A；窗口边界由 judge 独占。
- `test_tournament_scorecard.py`：半衰期权重、regime 匹配只用历史、有效窗口不足置 N/A。
- `test_tournament_allocator.py`：权重夹限与归一化、N/A 参赛者处理。
- `test_tournament_ledger.py`：版本 bump 强制、重复运行幂等、连续微调检测告警。
- `test_doc_consistency.py`：纳入 `TOURNAMENT_*` 常量与 SOP 的一致性校验。
- 测试全部走 mock/synthetic 数据（沿用 `conftest.py` 的 `mock_client` 模式），不依赖真实缓存。

## 9. 风险与边界

| 风险 | 应对 |
|------|------|
| R1 初期参赛者太少（3-4 个），锦标赛统计意义有限 | 报告强制标注「参考性结论」；成功标准里写明这是裁判基建，参赛者随模块落地增员 |
| R2 成本/成交假设不同构（事件驱动 vs 定期调仓） | 各用最保守假设 + `assumptions` 强制披露，不做虚假统一 |
| R3 regime 标签函数本身演化（HMM 重训） | 标签函数版本进台账；跨版本结论对比时报告注明 |
| R4 与 limitup「禁止连续寻优」规范冲突 | 设计上已对齐：本期零寻优，参数冻结 + 台账是硬约束 |
| R5 replay 的锦标赛自身业绩曲线仍是一次回测 | 报告措辞规范：标注为「选择层的样本内检验」，不作为实盘依据 |

## 10. L2/L3 接口预留（本期不实现）

- **genome 草案**：L2 基因组 = `FactorConfig` 权重子集 + 各模块阈值区间声明（`(参数名, 下界, 上界)` 列表）；adapter 的 `version` 字段即为 L2 晋升参数的版本载体。
- **晋升门槛**：数值在 L2 规格立项时定义；本包保证的结构性前提是——任何晋升都必须经 ledger 的版本记录与 OOS 使用计数通道，不需要改本包结构。
- **L3 不预留**：GP/RL 合成层与锦标赛的关系（合成因子作为新参赛者 vs 独立赛道）在 L2 有结论后再定。
