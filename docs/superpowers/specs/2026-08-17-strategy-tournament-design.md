# 策略锦标赛/参数进化模块（`davis_analyzer/tournament`）设计规格

> 状态：v1.1，已按用户反馈修订，待用户审阅
> 修订记录：v1.0（2026-08-17 初稿：L1 选择层）→ v1.1：①按用户反馈将 L2 参数进化纳入本模块范围，并加硬约束「进化仅限参数调优，逻辑结构冻结」；②新增冠军存档（多冠军、非单一最佳）；③新增随机时间段验证（CPCV-lite，防单一答案过拟合）；④新增反环化规则（裁判参数永不可进化）；⑤新增决赛窗口与晋升门槛的具体数值。
> 关联：`AGENTS.md`（协作规范）、`docs/superpowers/specs/2026-08-16-limitup-module-design.md`（统计稳健性纪律的来源）、`davis_analyzer/backtest.py` / `backtest_report.py`（复用组件）、`market_regime.py`（regime 标签）

## 1. 背景与目标

### 1.1 路线图定位（因子进化论）

用户提出的「量化因子进化论」，经 v1.1 反馈后收敛为本模块两层，均在本规格范围内：

| 层级 | 内容 | 状态 |
|------|------|------|
| **L1 选择层** | 模块不动，统一裁判下按滚动 OOS 表现 + regime 匹配度分配资金（锦标赛轮动） | 本期实现 |
| **L2 参数进化层** | **仅参数调优**：基因组限于人工划定的参数区间，逻辑结构（因子公式、流程、裁判规则）冻结；晋级参数进冠军存档 | 本期实现（用户 v1.1 确认） |
| L3 公式合成层（GP/RL 算子树发明新因子） | 暂缓：现有数据约束下过拟合与未来函数风险不可控 | 不做，仅记录边界 |

**核心论证结论**（为何这两层、且参数层带硬约束）：

1. 「竞争」的基建已存在：`run_backtest` 本身是竞争排名引擎（竞争发生在股票间），`FactorConfig` 是天然的参数载体，`compute_performance` 是现成适应度函数；缺的只是模块间的统一裁判。
2. 「进化」若触及逻辑结构，搜索空间爆炸且无法归因；限定在参数层后，每次进化都可审计（哪个参数、动了多少、证据是什么）。
3. 进化的主要风险是多重检验与 OOS 消耗，不是算法；对策是随机段验证 + 晋升门槛 + 台账计数（§5.6-5.8），而非依赖单次全样本最优。
4. 锦标赛最值钱的产物之一是副产品：**模块在各 regime 下的相对表现序列**——它是策略轮动的 meta 因子。

### 1.2 成功标准

- 至少 3 个参赛者（davis 双击预设变体 ≥2 + 指数基准 ≥1）跑通 ≥3 年历史回放的季度锦标赛，产出结构化 markdown 报告。
- 防前视性质有自动化测试证明：篡改任一时点 T 之后的数据，T 时点的评分结果不变。
- 参数进化闭环可复现：一次进化战役从基因声明 → 随机段验证 → 晋升判定 → 冠军存档落库 → `constants.py` 同步，全程有台账记录。
- 冠军存档保存**多名**冠军（按 模块×regime 分槽），任何时点可回答「现任冠军是谁、靠什么证据上位、参数是什么」。
- 报告含：参赛者×窗口表现矩阵、regime 切片矩阵、相对表现序列（meta 因子数据，CSV 导出）、建议权重及置信度、数据假设明细（成本/成交假设/样本量/N/A 原因）。

### 1.3 非目标（本期不做）

- **逻辑结构的任何调整**（因子公式、模块流程、裁判规则、评分结构的进化均禁止——这是用户 v1.1 的硬约束，也是本模块与 GP/RL 的本质区别）。
- L3 公式合成（另案）。
- 自动资金调拨、实盘下单、paper_trading 自动读取建议权重（产出仅供人工决策）。
- 接入尚未建成的模块实现（连板、周期反转埋伏——adapter 协议本期只做兼容性预留）。
- 新因子开发、对现有因子模块内部逻辑的任何改动。

## 2. 已确认的决策

| # | 问题 | 决策 | 状态 |
|---|------|------|------|
| D1 | 进化层级 | L1 选择层 + L2 参数进化层均在本模块；L3 另案 | v1.1 用户确认 |
| D2 | 模块形态 | 独立子包 + 模块级 CLI（沿用 `paper_trading`/`limitup` 先例），不改主 pipeline | 沿用先例 |
| D3 | 初期参赛者 | davis 引擎的冻结预设变体（均衡/动量倾斜/估值倾斜）+ 上证指数基准；连板/周期反转就位后接入 | v1.0 确定 |
| D4 | 适应度口径 | 统一 `PerformanceStats` + regime 切片；同 horizon 组内排名，组间只做风险预算展示，禁止跨 horizon 单一夏普排名 | v1.0 确定 |
| D5 | 纪律对齐 | OOS 台账 + 参数版本冻结，对齐 limitup 规范「禁止连续寻优、阈值冻结」 | v1.0 确定 |
| D6 | 产出形态 | 报告 + 建议权重（人决策），不自动执行 | v1.0 确定 |
| D7 | 窗口语义 | L1 评分用滚动前推窗口、每窗口独立起跑（等额初始资金子回测） | v1.0 确定 |
| D8 | 进化范围 | **仅参数调优，逻辑结构冻结**；可调参数必须在基因声明中显式列出（名称+区间），未声明者不可进化 | v1.1 用户确认 |
| D9 | 冠军存档 | 调优后的参数以「冠军」身份存档（SQLite `tournament_champions` 表）；多冠军分槽保存（模块×regime 各 ≤2 名 + 现任 1 名），**不收敛到单一最佳答案** | v1.1 用户确认 |
| D10 | 防过耦合验证 | 进化验证用**随机抽取时间段**（CPCV-lite：分段+随机留出+边界隔离），按抽取分布判稳健性，不按单次全样本最优 | v1.1 用户确认 |
| D11 | 反环化规则 | 裁判/评分/分配参数**永不可进化**，只由人工修订单调版本；进化对象仅限参赛者参数 | v1.1 设计推论（防裁判被参赛者博弈） |

## 3. 方案比选

| 方案 | 描述 | 结论 |
|------|------|------|
| **A. 独立子包 + Adapter 协议归一（采用）** | `davis_analyzer/tournament/` 子包；各模块经 `ModuleAdapter` 协议输出归一化权益曲线，裁判层统一评估；进化引擎在裁判之上、只写参数 | 不碰现有模块，零回归风险；连板（事件驱动）与周期再平衡（定期调仓）两种引擎语义在 adapter 层吸收，裁判层保持单一职责 |
| B. 扩展 `backtest.py` 加多策略对比 | 在现有引擎里加对比循环 | 否决：`backtest.py` 是周期再平衡单一职责引擎，塞入裁判/进化语义形成巨石文件，威胁现有回测正确性 |
| C. studies/ 风格一次性对比脚本 | 不建正式模块，直接写对比脚本 | 否决：不可复现、无防前视约束、无台账与冠军档案，与「进化」需要的纪律根本冲突 |

**复用清单（方案 A）**：`backtest.py` 的 `BacktestConfig`/`run_backtest`（davis 系参赛者的执行引擎）、`EquitySnapshot`/`Trade`（adapter 归一化载体）；`backtest_report.py` 的 `PerformanceStats` + `compute_performance`（适应度计算）；`market_regime.py` 的 `get_market_regime_with_confirm`（regime 标签）；`tushare_client.TushareClient`（数据层）。

## 4. 模块结构

```
davis_analyzer/tournament/
├── __init__.py
├── adapters.py    # ModuleAdapter 协议 + RunResult + DavisPresetAdapter + IndexBenchmarkAdapter
├── judge.py       # JudgeHarness：滚动窗口调度、防前视铁律、最小样本门槛、regime 切片
├── scorecard.py   # 滚动 OOS 表现分（半衰期）+ regime 匹配度分 → 合成总分
├── allocator.py   # 风险预算分配（权重上下限、归一化）
├── ledger.py      # OOS 使用台账 + 参数版本冻结（晋升/版本纪律的唯一执行点）
├── genome.py      # 基因声明：参赛者可调参数的 (名称, 下界, 上界, 类型) 注册表；未声明即冻结
├── evolution.py   # 参数进化引擎：种群变异 + 随机段验证（CPCV-lite）+ 晋升门槛判定
├── champions.py   # 冠军存档：tournament_champions 表 CRUD + 现任冠军 ↔ constants.py 同步
├── report.py      # markdown 锦标赛报告 + meta 序列 CSV 导出
├── cli.py         # 子命令编排
└── __main__.py    # python -m davis_analyzer.tournament {run|replay|evolve|champions|list}

tests/test_tournament_adapters.py / _judge.py / _scorecard.py / _allocator.py
     / _ledger.py / _genome.py / _evolution.py / _champions.py
```

依赖方向（自上而下单向，与项目总架构一致）：

```
cli.py → judge.py → adapters.py → backtest.py / tushare_client.py
      │         ↘ market_regime.py（regime 标签，只读）
      → scorecard.py → allocator.py → report.py
      → evolution.py → genome.py / judge.py（复用裁判评估，只读裁判规则）
      → champions.py → constants.py（部署同步，单向）
      → ledger.py（全程旁路审计，被 judge/evolution/champions 调用）
```

配置：`config.py` 增加 `TOURNAMENT_REPORTS_DIR`（仿 `LIMITUP_REPORTS_DIR` 先例）。冻结参数集中于根 `constants.py` 新增 `TOURNAMENT_*` 段，现任冠军参数经同步流程写入 `constants.py` 的 `CHAMPION_PRESETS`（评分权重受单一真相源铁律约束，需与 `SOP.md` 及 `tests/test_doc_consistency.py` 同步）。

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
    version: str             # 参赛者自身参数版本（参数指纹，进化后自动变化）
    def run_window(self, client: TushareClient, start: date, end: date,
                   params: dict[str, float | int] | None = None) -> RunResult | None: ...
```

- `params` 为**仅有的**外部可调入口，键必须已在该参赛者的基因声明（`genome.py`）中注册；传入未声明键直接抛错（逻辑结构无法通过参数通道改动，D8 的代码级保证）。
- **DavisPresetAdapter**：包装 `run_backtest`。初始基因声明：六维因子权重（`momentum_weight` 等，界 [0,1]，内部归一化）、`top_n` ∈ {5,10,15,20}、`frequency` ∈ {5,10,20}。预设（`davis_balanced` / `davis_momentum_tilt` / `davis_valuation_tilt`）= 冻结的参数点，改动需版本 bump。
- **IndexBenchmarkAdapter**：被动持有指数（上证指数 `000001.SH`，即日历锚，缓存必有；可选 000300.SH/000905.SH，缓存缺失时跳过并注明）。无参数（空基因），买入持有。
- **返回 `None` 语义**：窗口内数据不足（如缓存未覆盖）→ 该参赛者该窗口记 N/A，不参与排名。
- **成本现实性规则**：各参赛者用各自引擎的**最保守**现实性假设（周期引擎：佣金 2.5bps + 印花税 10bps；连板就位后：成交概率三档取中位档），假设明细由 `assumptions` 自述、报告强制展示——不同构的成本模型不做强行统一，只做披露。

### 5.2 JudgeHarness（裁判铁律）

1. **滚动评估**：评估点每 63 个交易日（季度）一个；任一评估点 T 的评分只使用 T 之前已实现窗口的数据（point-in-time，代码层面由 judge 唯一控制窗口边界，adapter 不感知评估点序列）。
2. **独立窗口**：每窗口等额初始资金的独立子回测（D7），窗口间无路径依赖，保证参赛者间可比。
3. **最小样本门槛**：窗口内交易日 < 40 或成交笔数 < 10 → N/A（对齐 limitup 规范的样本门槛思想；passive 组豁免成交笔数门槛）。
4. **regime 切片**：每个窗口用 `get_market_regime_with_confirm`（HMM 三态 + MA 确认，标签函数版本进台账）打一个标签，产出 模块×regime 表现矩阵。
5. **分组**：同 `horizon` 组内排名；跨组仅并列展示（D4）。regime 标签输出字符串以 `market_regime.py` 当前实现为准，裁判不硬编码标签值。
6. **裁判复用**：`evolution.py` 评估候补参数时复用同一 JudgeHarness 实例与规则，保证进化适应度与锦标赛评分同口径；裁判规则参数本身不可被进化触碰（D11）。

### 5.3 ScoreCard（评分，L1）

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

- 每次 `run`/`replay`/`evolve` 追加记录（SQLite 表 `tournament_ledger`，落在共享 `market_data.db`）：日期、操作类型、参赛者清单及版本、评分/分配参数版本、OOS 窗口使用计数、进化战役代数与种群规模。
- **参数版本冻结**：`TOURNAMENT_*` 常量、参赛者预设、现任冠军参数均带版本号；修改必须 bump 版本并留记录；台账检测到「同一版本参数在同窗口上重复评估产生不同结论的连续微调模式」时，报告顶部强制警告（软约束 + 显式披露）。
- **进化战役限额**：每年进化战役 ≤ 4 次（初值冻结）；每次战役的全部 OOS 评估计入台账。这是「OOS 是消耗品」的直接预算控制。

### 5.6 evolution.py（参数进化引擎，L2）

**进化算法（刻意保持简单）**：

- 种群 = 该参赛者参数点集合，规模 16（初值冻结）；每代：随机变异（高斯扰动，σ = 参数区间宽度的 15%，越界截断、权重类参数重归一化）→ 用裁判在**选择集（7 段）**上打分 → 前 25% 存活。验证集（3 段）绝不参与代际选择，只用于最终候补 vs 现任的分布比较——这是随机段验证防泄漏的关键。不做交叉（参数量 ≤ 15 时收益可疑）。每战役 ≤ 10 代。
- 诚实备注：该参数量级下随机搜索与遗传算法效率相近；保留种群框架是为了与锦标赛叙事一致并支持未来扩展，不是为了算法优越性。

**随机时间段验证（CPCV-lite，D10 的实现）**：

- 将可用评估历史切为 N = 10 个不重叠顺序段；每次抽取随机留出 k = 3 段为验证集，其余 7 段为选择集；段边界处各留 5 个交易日隔离带（embargo，防自相关泄漏）。
- 重复 M = 20 次抽取 → 得到（候补 − 现任）表现差在抽取分布上的样本，而非单次全样本数字。
- 判定看分布不看均值：胜率（候补优于现任的抽取占比）与分位数共同决策（见晋升门槛）。

**晋升门槛（全部满足才可晋升，数值为冻结初值）**：

1. 随机段胜率 ≥ 65%（对现任冠军，M = 20 次抽取）。
2. 中位改进 > 0，且 25 分位改进 > −1.0（窗口表现分单位；防右偏分布靠单次运气过关）。
3. ±20% 参数扰动稳健性：对候补参数做 ±20% 扰动后重估，性能衰减 ≤ 30%（对齐 limitup 规范，升格为本项目标准）。
4. **决赛窗口**：保留最近 ~18 个月为一次性决赛段，每次晋升消耗一次（台账计数）；决赛段烧尽后，新晋升须以 paper_trading 前向证据替代决赛（诚实降级路径）。

### 5.7 champions.py（冠军存档，D9）

- 表 `tournament_champions`（`market_data.db`）：冠军 ID、参赛者名、params JSON、版本、世代数、上位证据（随机段分布统计、扰动结果、决赛结论）、冻结日期、OOS 消耗计数。
- **分槽多冠军**：按（参赛者 × regime）各保留 ≤ 2 名历史冠军 + 1 名现任——「避免只有一个最佳答案」的结构化实现；同槽竞争、跨槽不比较。
- **部署同步（单向）**：现任冠军参数同步进 `constants.py` 的 `CHAMPION_PRESETS`（人工触发 `champions deploy` 子命令，生成 SOP 变更说明）；`tests/test_doc_consistency.py` 校验 constants 与 DB 现任冠军一致。DB 存全部历史，constants 只存部署态——单一真相源铁律不被破坏。
- adapter 生成参赛者时可从 `CHAMPION_PRESETS` 实例化「现任冠军参赛者」（`davis_champion`），与人工预设同台竞争——进化结果本身也要接受锦标赛检验。

### 5.8 反环化与诚实边界

- **反环化（D11）**：裁判、评分、分配、晋升门槛的参数属于「逻辑判断」，永不可进化、只随人工修订单调调版本。进化只能产出参赛者参数。若允许进化裁判参数，系统会学会博弈裁判（Goodhart），这是最短的自毁路径。
- **随机段的诚实边界**：随机抽取时间段能消解「单一路径运气」，但不能凭空制造新 regime——A 股历史约 10-15 年，独立 regime 情节有限，随机段会反复重采样相同 regime。因此随机段验证是必要条件而非充分条件，决赛窗口 + 台账限额 + 扰动稳健共同构成防线。报告措辞规范：所有进化结论必须附此边界声明。
- **锦标赛自身的检验**：Phase 2 的 `replay` 产出锦标赛建议组合的前向模拟业绩曲线，用于检验「选择层+参数层」整体是否真的增值；该曲线本身仍是一次回测，报告标注为「样本内检验」，不作为实盘依据。

## 6. 数据与依赖

- 价格数据全走 SQLite 缓存（与 backtest 铁律一致）；因子打分可能触发首次运行的增量缓存填充（复用 `score_universe_at` 现有行为），其后为纯缓存读。
- 交易日历：沿用 `backtest.py` 锚定股票推导法，不调专用日历 API。
- regime 标签：`market_regime.py` 现有函数，只读；其 HMM 模型重训视为标签函数版本变化，进台账。
- 新增 SQLite 表两张（均在共享 `market_data.db`）：`tournament_ledger`（§5.5）、`tournament_champions`（§5.7）。
- 无新增 Tushare 接口依赖。

## 7. 分阶段交付

| 阶段 | 内容 | 交付物 |
|------|------|--------|
| Phase 1 裁判与报告 | `adapters.py`（davis 预设×3 + 指数基准）、`judge.py`、`scorecard.py`、`report.py`、`config.py`/`constants.py`/SOP 同步 + 全套测试 | 第一份真实锦标赛报告（并列展示，暂不分配） |
| Phase 2 分配与回放 | `allocator.py`、`ledger.py`、`replay` 子命令（历史 walk-forward 回放 → meta 序列 CSV + 锦标赛自身的前向模拟业绩曲线） | 建议权重报告 + meta 因子序列数据 + 台账运转 |
| Phase 3 参数进化与冠军存档 | `genome.py`、`evolution.py`（随机段验证 CPCV-lite + 晋升门槛）、`champions.py`（存档 + constants 同步）、`evolve`/`champions` 子命令 + 决赛窗口管理 | 首次参数进化战役全流程落档（无论成败，负结果同样记录） |
| Phase 4 接入新模块（条件触发） | 连板 adapter（依赖 limitup Phase 2 引擎落地）、周期反转 adapter（依赖该模块立项）；新参赛者基因声明随接入定义 | 新参赛者接入，协议零改动 |

## 8. 测试策略

- `test_tournament_adapters.py`：合成权益曲线 → 归一化 `RunResult` → `compute_performance` 数值正确；数据不足返回 None；**传入未声明参数键直接抛错**（D8 代码级保证）。
- `test_tournament_judge.py`：**防前视性质测试**（篡改/删除评估点 T 之后的所有数据，断言 T 时点评分不变）；最小样本门槛触发 N/A；窗口边界由 judge 独占。
- `test_tournament_scorecard.py`：半衰期权重、regime 匹配只用历史、有效窗口不足置 N/A。
- `test_tournament_allocator.py`：权重夹限与归一化、N/A 参赛者处理。
- `test_tournament_ledger.py`：版本 bump 强制、重复运行幂等、连续微调检测告警、进化战役年度限额。
- `test_tournament_genome.py`：声明/未声明参数的准入与拒绝、区间截断与权重归一化。
- `test_tournament_evolution.py`：随机段抽取的**不重叠与 embargo 性质**（断言隔离带内数据既不进选择集也不进验证集）；晋升门槛四条的独立判定；扰动稳健性计算；变异只动已声明参数。
- `test_tournament_champions.py`：分槽上限（模块×regime ≤ 2 + 现任 1）、constants 同步一致性（与 `test_doc_consistency.py` 联动）、决赛窗口消耗计数。
- 测试全部走 mock/synthetic 数据（沿用 `conftest.py` 的 `mock_client` 模式），不依赖真实缓存。

## 9. 风险与边界

| 风险 | 应对 |
|------|------|
| R1 初期参赛者太少（3-4 个），锦标赛统计意义有限 | 报告强制标注「参考性结论」；参赛者随模块落地增员 |
| R2 成本/成交假设不同构（事件驱动 vs 定期调仓） | 各用最保守假设 + `assumptions` 强制披露，不做虚假统一 |
| R3 regime 标签函数本身演化（HMM 重训） | 标签函数版本进台账；跨版本结论对比时报告注明 |
| R4 与 limitup「禁止连续寻优」规范冲突 | 设计上已对齐：参数冻结 + 台账 + 战役限额；进化是显式、限额、留痕的例外流程，不是连续寻优 |
| R5 replay 的锦标赛自身业绩曲线仍是一次回测 | 报告措辞规范：标注为「样本内检验」，不作为实盘依据 |
| R6 随机段验证的虚假安全感 | §5.8 诚实边界写入报告模板；随机段是必要非充分条件 |
| R7 决赛窗口烧尽 | 台账计数 + 降级路径（paper_trading 前向证据替代决赛） |
| R8 进化博弈裁判（Goodhart） | D11 反环化规则：裁判参数永不可进化，代码上参数通道仅通向参赛者 |
| R9 冠军参数过拟合上位 | 晋升门槛四条（随机段胜率/分位数/扰动/决赛）+ 上位后作为 `davis_champion` 参赛者继续接受锦标赛检验，可被拉下马 |

## 10. L3 边界记录（不做，仅声明）

GP/RL 公式合成层与锦标赛的关系（合成因子作为新参赛者进入 L1/L2 框架，还是独立赛道）在 L2 运行出经验后再定。本模块对其不做任何结构性预留——避免为不确定的未来设计过度抽象。
