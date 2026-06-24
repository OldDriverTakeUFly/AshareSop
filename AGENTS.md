# AGENTS.md

This repository expects coding agents to follow the local development environment skill for any environment-related work.

## Default Rule

For tasks involving local setup, dependency installation, runtime selection, environment repair, toolchain isolation, local services, or project switching, agents must read and follow:

- `.agents/skills/local-development-environment/SKILL.md`

Companion materials are available here:

- `.agents/skills/local-development-environment/checklists/preflight.md`
- `.agents/skills/local-development-environment/checklists/cleanup.md`
- `.agents/skills/local-development-environment/examples/setup-session.md`
- `.agents/skills/local-development-environment/examples/switch-projects.md`
- `.agents/skills/local-development-environment/examples/troubleshoot-conflicts.md`
- `.agents/skills/local-development-environment/references/tooling-matrix.md`
- `.agents/skills/local-development-environment/README.zh-CN.md`

## When This Applies

Use the skill whenever the task includes any of the following:

- setting up a local development environment
- switching between projects with different runtime requirements
- fixing dependency conflicts or mixed environments
- choosing Python, Node.js, Java, Ruby, or mixed-language runtime strategies
- deciding whether something should be local, global, or containerized
- documenting team conventions for local environment management

## Required Agent Behavior

The requirements below are a non-exhaustive summary. They do not replace `.agents/skills/local-development-environment/SKILL.md`.

Agents working on environment-related tasks must:

1. inspect the repository before making changes
2. prefer existing repository conventions when they are coherent
3. prefer project-local isolation over host-level fixes
4. avoid global installs unless the project or user explicitly requires them
5. verify the resulting environment after setup or repair
6. report what was created, reused, skipped, or left unresolved

## Guardrails

This skill is guidance for environment-related work only.

Agents must not:

- use the skill as a reason to make unrelated repository changes
- overwrite environment files without first reading and understanding them
- introduce new environment tools without explaining why they are needed
- claim an environment is reproducible without checking versions, lockfiles, or startup paths

## Post-Report Push Rule (MANDATORY)

After **every** research report is completed and verified in `docs/`, the agent MUST:

1. **Verify** the report file: check chapter completeness, no PART markers, proper start/end
2. **Commit** with semantic style: `feat(docs): add {report-name}`
3. **Push** to `origin/master` immediately
4. **Confirm** push succeeded before reporting completion to user

This is a non-negotiable step. A report is not "done" until it is on GitHub.

Commit message format (match existing repo style):
```
feat(docs): add {short-description}
```

Always include agent attribution footer:
```
Ultraworked with [Sisyphus](https://github.com/code-yeongyu/oh-my-openagent)
Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
```

Report naming: use **Chinese filenames** (e.g. `docs/ai算力全景研报.md`, `docs/固态电池产业链报告.md`). Do NOT use English filenames for research reports.

Remote: `origin` → `git@github.com:OldDriverTakeUFly/AshareSop.git`

## Source of Truth

If this file and the skill differ in detail, treat `.agents/skills/local-development-environment/SKILL.md` as the source of truth for environment-management behavior.

# 估值分析 Skill（valuation-loss-making-targets）

This repository expects coding agents to follow the loss-making target valuation skill for any 估值分析 work involving companies that are currently unprofitable.

## Default Rule

For tasks involving 估值分析、亏损标的估值、困境反转定价、或 PS+DCF 三角验证建模, agents must read and follow:

- `.agents/skills/valuation-loss-making-targets/SKILL.md`

Companion materials are available here:

- `.agents/skills/valuation-loss-making-targets/README.zh-CN.md`
- `.agents/skills/valuation-loss-making-targets/references/distress-probability-rules.md`
- `.agents/skills/valuation-loss-making-targets/references/valuation-model-schema.md`
- `.agents/skills/valuation-loss-making-targets/references/study-script-templates/financial_deep_template.py`
- `.agents/skills/valuation-loss-making-targets/references/study-script-templates/scoring_template.py`
- `.agents/skills/valuation-loss-making-targets/references/study-script-templates/quant_data_template.py`
- `.agents/skills/valuation-loss-making-targets/checklists/differentiation-audit.md`
- `.agents/skills/valuation-loss-making-targets/checklists/source-traceability.md`

## When This Applies

Use the skill whenever the task includes any of the following:

- 估值分析（target valuation）for an acquisition or investment target
- 亏损标的（loss-making targets）—— net income 为负、PE 估值失效的场景
- 困境反转（distress / turnaround）候选公司定价
- DCF for loss-making companies —— 需要对负自由现金流做情景化处理
- 同业对比估值（global peer anchoring）—— 跨市场可比公司映射
- PS + DCF 双模型建模与三角验证（Triangle Framework）

## Required Agent Behavior

The requirements below are a non-exhaustive summary. They do not replace `.agents/skills/valuation-loss-making-targets/SKILL.md`.

Agents working on 估值分析 tasks must:

1. inspect the target company before valuing —— 先读取标的的财务数据、行业地位、亏损原因
2. reuse existing engines when coherent —— 复用 `research_report/` 与 `invest_sop/` 中已有的财务分析脚本，避免重写
3. tag every number with a named source —— 报告中每个数字必须可追溯到具名数据源（年报、券商研报、行业数据库）
4. produce scenario-weighted ranges, never a single price —— 输出悲观/中性/乐观三情景概率加权区间，权重由客观 distress 信号调整
5. apply the Triangle Framework —— 困境评分、PS+DCF 双模型、全球同业锚定三角度必须同时给出，发散本身是风险信号
6. report what was computed, reused, assumed, or left unresolved —— 区分实算、复用、假设与未决项

## Guardrails

This skill is guidance for 估值分析 work only.

Agents must not:

- 改动既有估值引擎（`research_report/`、`invest_sop/`）的实现 —— 复用而非修改，如需扩展请另起模块
- 复制粘贴财务表格而不附数据源 —— 未标注来源的数字一律视为不可信
- 给出单一目标价 —— 亏损标的必须以概率加权区间输出，禁止单点定价
- 使用未标注数据或未公开内幕信息作为估值输入
- 用 PE / PEG 等依赖正盈利的指标对亏损标的做主估值（仅可作为辅助参照）
- 跳过 distress 信号核查而直接给乐观结论

## Source of Truth

If this section and the skill differ in detail, treat `.agents/skills/valuation-loss-making-targets/SKILL.md` as the source of truth for loss-making target valuation methodology.

# 日常盘面扫描 Skill（daily-market-scan）

This repository expects coding agents to follow the daily market scan skill for any 盘面扫描、每日复盘、热点数据采集 work involving the four stockhot hot-topic modules.

## Default Rule

For tasks involving 盘面扫描、涨停分析、龙虎榜、资金流向、风险提示、或每日数据采集, agents must read and follow:

- `.agents/skills/daily-market-scan/SKILL.md`

Companion materials are available here:

- `.agents/skills/daily-market-scan/README.zh-CN.md`
- `.agents/skills/daily-market-scan/checklists/scan-completeness.md`
- `.agents/skills/daily-market-scan/references/module-orchestration.md`

## When This Applies

Use the skill whenever the task includes any of the following:

- 日常盘面扫描（daily market scan）—— 调用涨停、龙虎榜、资金流、风险提示四个模块
- 涨停分析（limit_up）—— 涨停池、炸板池、连板梯队、板块联动、封单强度
- 龙虎榜分析（dragon_tiger）—— 机构席位、营业部、游资追踪
- 资金流向（fund_flow）—— 大盘/板块资金流趋势判断
- 风险提示（risk_alert）—— ST 股票、异常波动、资金出逃、高位连板
- 为下游 skill（invest-sop-pre-market）采集当日市场数据

## Required Agent Behavior

The requirements below are a non-exhaustive summary. They do not replace `.agents/skills/daily-market-scan/SKILL.md`.

Agents working on 盘面扫描 tasks must:

1. follow the fixed execution order —— limit_up 先行 → dragon_tiger + fund_flow 并行 → risk_alert 最后（读取上游 DB 数据）
2. wrap each module in its own try/except —— 单个模块失败标记为"数据不可用"，不影响其他模块执行
3. call module entry points only —— 只调用 `run_*_analysis(date)`，不调用内部 helper 函数
4. persist results to the database —— 所有成功模块通过 `save_daily_data` + `save_analysis_result` 持久化
5. report what succeeded and what failed —— 区分 `success`、`no_data`（非交易日）、`数据不可用`（错误）
6. respect the boundary with invest-sop-pre-market —— 本 skill 只采集数据，不生成报告；报告生成交给 `invest-sop-pre-market`

## Guardrails

This skill is guidance for 盘面扫描 work only.

Agents must not:

- 修改四个 stockhot 模块（limit_up/dragon_tiger/fund_flow/risk_alert）的源码 —— 复用而非修改
- 暴露或调整扫描参数（阈值/行业筛选/市值筛选）—— 所有阈值固定在模块源码中
- 生成任何 markdown 报告 —— 本 skill 是数据采集层，报告生成属于 `invest-sop-pre-market`
- 调用 AI/LLM 做分析 —— 所有模块摘要均为纯统计
- 跳过 try/except 隔离而让单个模块崩溃终止整个扫描
- 将模块执行顺序打乱 —— risk_alert 必须最后运行，否则读取的上游 DB 数据为空

## Source of Truth

If this section and the skill differ in detail, treat `.agents/skills/daily-market-scan/SKILL.md` as the source of truth for daily market scan orchestration methodology.

# 景气度投资 Skill（industry-prosperity）

This repository expects coding agents to follow the industry prosperity skill for any 景气度投资分析 work involving growth cycle classification, ΔG signaling, or six-dimension indicator assessment.

## Default Rule

For tasks involving 景气度分析、G+ΔG 框架、周期定位、二次点火筛选、或六维指标监控, agents must read and follow:

- `.agents/skills/industry-prosperity/SKILL.md`

Companion materials are available here:

- `.agents/skills/industry-prosperity/README.zh-CN.md`
- `.agents/skills/industry-prosperity/references/six-dimension-indicators.md`
- `.agents/skills/industry-prosperity/references/scoring-template.py`
- `.agents/skills/industry-prosperity/checklists/prosperity-audit.md`

## When This Applies

Use the skill whenever the task includes any of the following:

- 景气度投资分析（prosperity analysis）—— 判断行业或个股处于加速期、减速期还是拐点
- G+ΔG 框架 —— 增速绝对值（G）与增速边际变化（ΔG）的联合分析
- 周期定位（cycle classification）—— 用山峰理论或成长股投资时钟分类标的当前位置
- 二次点火筛选（secondary ignition screening）—— 筛选 G 和 ΔG 同时为正的标的
- 六维指标监控（six-dimension indicators）—— BB Ratio / 稼动率 / 交期 / 库存 / 排产 / LTA
- 景气预期追踪（prosperity expectation）—— 分析师一致预期的边际变化

## Required Agent Behavior

The requirements below are a non-exhaustive summary. They do not replace `.agents/skills/industry-prosperity/SKILL.md`.

Agents working on 景气度投资 tasks must:

1. inspect the target before classifying —— 先获取至少 4 个季度的财务数据，确认 ΔG 可计算
2. reuse davis_analyzer engines —— 复用 `prosperity.py`、`prosperity_inflection.py`、`prosperity_sector.py` 的函数，不重写评分逻辑
3. tag every indicator reading with a named source —— 六维指标的每个读数必须标注数据来源（厂商法说会、分销商报告、行业调研）
4. classify cycle position honestly —— 如实分类周期位置，不因"行业景气"就强行说个股在加速期
5. apply the G+ΔG framework —— G 和 ΔG 必须同时给出，ΔG 符号决定山峰位置（左山坡/右山坡/山后）
6. report what was computed, reused, assumed, or left unresolved —— 区分实算、复用、假设与未决项

## Guardrails

This skill is guidance for 景气度投资 work only.

Agents must not:

- 修改 `davis_analyzer` 景气度引擎（`prosperity.py`、`prosperity_inflection.py`、`prosperity_sector.py`）的源码 —— 复用而非修改
- 复制 prosperity.py 代码到 skill 文件 —— 只描述映射关系，不复制实现
- 自动抓取六维指标数据 —— skill 定义框架和计算逻辑，数据需手动收集
- 在 ΔG 数据不足 2 个季度时强行给出周期定位 —— 必须标注"ΔG 不可靠"
- 混淆行业景气（β）与个股表现（α）—— 行业景气不等于每只个股都在加速
- 忽视 30% 阈值 —— 净利润增速降至 30% 以下时超额收益大概率下滑，必须标注

## Source of Truth

If this section and the skill differ in detail, treat `.agents/skills/industry-prosperity/SKILL.md` as the source of truth for industry prosperity investment methodology.

# 多因子量化选股 Skill（multi-factor-screening）

This repository expects coding agents to follow the multi-factor screening skill for any 多因子量化选股、因子打分、分域选股 work involving systematic stock universe ranking.

## Default Rule

For tasks involving 多因子选股、量化选股、因子打分、分域选股、三层结构选股管线, agents must read and follow:

- `.agents/skills/multi-factor-screening/SKILL.md`

Companion materials are available here:

- `.agents/skills/multi-factor-screening/README.zh-CN.md`
- `.agents/skills/multi-factor-screening/checklists/factor-audit.md`
- `.agents/skills/multi-factor-screening/references/three-layer-pipeline.md`
- `.agents/skills/multi-factor-screening/references/screening-template.py`

## When This Applies

Use the skill whenever the task includes any of the following:

- 多因子量化选股（multi-factor screening）—— 从股票池中系统化筛选和排名候选标的
- 因子打分（factor scoring）—— 成长/质量/估值/技术/资金情绪五大因子族加权
- 分域选股（domain-specific selection）—— 红利型/成长型/价值型/周期型四域差异化权重
- 三层结构管线（three-layer pipeline）—— 硬过滤 → 打分 → 加分
- 季度组合调仓（quarterly rebalancing）—— 重新评估和排序持仓候选

## Required Agent Behavior

The requirements below are a non-exhaustive summary. They do not replace `.agents/skills/multi-factor-screening/SKILL.md`.

Agents working on 多因子选股 tasks must:

1. follow the three-layer pipeline strictly —— 硬过滤层先行 → 打分层 → 加分层，三层不可跳过或打乱
2. score within industry peer groups —— 所有因子打分在行业内部排序，不做全市场一刀切
3. apply domain-specific weights —— 红利型/成长型/价值型/周期型四域使用不同权重，分别输出排名
4. use hardcoded constants only —— 所有权重和阈值固定在 SKILL.md 和模板脚本中，不提供运行时配置
5. report present-day ranking only —— 只输出当日截面排名，不做回测、IC 分析或因子衰减曲线
6. call davis_analyzer functions as-is —— 复用而非修改，权重体系不同时以本 skill 为准

## Guardrails

This skill is guidance for 多因子选股 work only.

Agents must not:

- 修改 davis_analyzer 源码 —— 复用 `scoring.py`、`pipeline.py` 等模块，不修改其实现
- 将权重设为可配置参数 —— 30/20/25/25 默认权重和四域覆盖权重均为硬编码常量
- 做回测或 IC 分析 —— 本 skill 只做当日截面排名，回测属于独立流程
- 跳过硬过滤层直接打分 —— 硬过滤是底线，不可为"特殊标的"破例
- 跨域混合排名 —— 四域综合分不可比，必须分域输出排名清单
- 加分层对信号缺位减分 —— 加分只加不减，无信号不等于差公司

## Source of Truth

If this section and the skill differ in detail, treat `.agents/skills/multi-factor-screening/SKILL.md` as the source of truth for multi-factor quantitative screening methodology.

# AI 交易建议引擎（ai-trading-advisor）

This repository expects coding agents to follow the AI trading advisor convention for any AI 建议生成、建仓/调仓/清仓/做T 建议 work involving the `stockhot/advisor/` module.

## Default Rule

For tasks involving AI 建议生成、信号聚合、冲突仲裁、或 watchlist 管理, agents must use:

- `stockhot/advisor/` module

Key entry points:

- `stockhot/advisor/cli.py` — CLI with `ask` / `daily` / `watchlist` subcommands
- `stockhot/advisor/recommendation_engine.py` — `run_for_stock()` core engine
- `stockhot/advisor/llm_provider.py` — LLM provider abstraction (`get_provider`, `LLMProvider`)
- `stockhot/advisor/watchlist_cli.py` — watchlist CRUD management

## When This Applies

Use this module whenever the task includes any of the following:

- AI 建议生成（recommendation generation）—— 对个股生成建仓/调仓/清仓/做T 建议
- 信号聚合（signal aggregation）—— 汇总 davis_analyzer、technical_analyzer、sell_monitor 等多源信号
- 冲突仲裁（conflict arbitration）—— 当多个信号方向矛盾时，由硬编码 resolver 决定最终 action
- LLM 调用（LLM invocation）—— 通过 provider 抽象（DeepSeek / GLM / OpenAI）调用大模型
- watchlist 管理 —— 通过 CLI `watchlist add/list/remove/update` 管理关注列表
- 每日批量建议（daily batch run）—— 对全部持仓 + watchlist 执行 `daily` 命令

## Required Agent Behavior

The requirements below are a non-exhaustive summary.

Agents working on AI 交易建议 tasks must:

1. use the prompt registry —— 所有 LLM prompt 从 prompt registry 加载，永远不在代码中内联 prompt 文本
2. use the LLM provider abstraction —— 通过 `get_provider()` 获取 provider 实例，不直接 import 具体厂商 SDK
3. use the hardcoded conflict resolver —— 当信号冲突时由确定性 resolver 仲裁，绝不将冲突交给 LLM 自行决定
4. respect `MAX_STOCKS_PER_DAILY_RUN` —— `daily` 命令处理的股票数不得超过此上限（当前为 20），超出时截断并告警
5. call `run_for_stock()` as the entry point —— 单股分析通过 `run_for_stock(code, trade_date, holding=...)` 调用
6. report what was generated, skipped, or errored —— 区分 `generated`（有建议）、`skipped`（无建议或出错）

## Guardrails

This section is guidance for AI 交易建议 work only.

Agents must not:

- 自动下单或执行实际交易 —— advisor 只生成建议，不触碰交易系统
- 让 LLM 仲裁信号冲突 —— 冲突必须由硬编码 resolver 处理，LLM 只负责生成自然语言表述
- 捏造 fallback 数据 —— 当 davis_analyzer / technical_analyzer 数据缺失时标注"数据不可用"，不得编造
- 超过 `MAX_STOCKS_PER_DAILY_RUN` 上限 —— 批量运行必须截断，不得动态提高上限
- 在代码中内联 prompt 文本 —— 所有 prompt 必须从 prompt registry 加载，便于版本管理
- 修改 `davis_analyzer` / `technical_analyzer` / `sell_monitor` 源码 —— 复用而非修改，advisor 只消费它们的输出

## Source of Truth

If this section and the module implementation differ in detail, treat `stockhot/advisor/` source code as the source of truth for AI trading advisor behavior.

# 通知推送模块（notification）

This repository expects coding agents to follow the notification convention for any Telegram 推送、消息通知 work involving the `stockhot/notification/` module.

## Default Rule

For tasks involving Telegram 推送、消息批处理、或用户授权验证, agents must use:

- `stockhot/notification/` module

Key entry points:

- `stockhot/notification/telegram_bot.py` — `TelegramNotifier` class, `get_telegram_config()` helper

## When This Applies

Use this module whenever the task includes any of the following:

- Telegram 推送（Telegram push）—— 通过 Bot API 发送 AI 交易建议或行情通知
- 消息批处理（message batching）—— 将多条建议合并为 ≤5 条消息批量发送，紧急消息（EXIT / HIGH）优先
- 用户授权验证（user authorization）—— 通过 `TELEGRAM_ALLOWED_USER_IDS` 白名单校验用户身份
- 速率限制处理（rate-limit handling）—— 429 响应时读取 `retry_after` 并指数退避重试

## Required Agent Behavior

The requirements below are a non-exhaustive summary.

Agents working on 通知推送 tasks must:

1. use `httpx` for all API calls —— 直接调用 Telegram Bot API（`POST /sendMessage`），不引入 `python-telegram-bot` 依赖
2. verify user allowlist —— 通过 `verify_user(user_id)` 或 `TELEGRAM_ALLOWED_USER_IDS` 环境变量校验，未授权用户命令一律忽略
3. batch messages ≤5 per push —— 单次 `send_recommendations_batch` 最多发送 `max_messages`（默认 5）条消息
4. handle 429 with `retry_after` —— 遇到 429 时读取响应体 `parameters.retry_after` 字段休眠，而非使用默认指数退避
5. retry up to 3 times —— 所有 HTTP 错误最多重试 3 次，最终失败时 `raise_for_status()`
6. send urgent first —— EXIT 动作或 HIGH 置信度的建议单独成条、优先发送

## Guardrails

This section is guidance for 通知推送 work only.

Agents must not:

- 实际发送测试消息到真实 Telegram —— 测试必须全 mock（`_transport=httpx.MockTransport`），不得触碰真实 API
- 接受未授权用户命令 —— 白名单外的用户消息一律丢弃，不得回执
- 超过 5 条消息/次推送 —— `max_messages` 上限为 5，紧急消息优先占用配额
- 修改 `ai_analyzer` 源码 —— notification 模块只消费建议数据，不修改上游生成逻辑
- 引入 `python-telegram-bot` 或其他 Telegram SDK —— 统一使用 `httpx` 原生调用

## Source of Truth

If this section and the module implementation differ in detail, treat `stockhot/notification/telegram_bot.py` source code as the source of truth for notification behavior.

# 盘前分析 SOP Skill（invest-sop-pre-market）

This repository expects coding agents to follow the pre-market SOP skill for any 盘前分析、盘前报告生成、晨间指令、持仓决策矩阵 work involving reading collected data and producing daily markdown reports.

## Default Rule

For tasks involving 盘前报告、晨间指令、或 SOP 决策矩阵评估, agents must read and follow:

- `.agents/skills/invest-sop-pre-market/SKILL.md`

Companion materials are available here:

- `.agents/skills/invest-sop-pre-market/README.zh-CN.md`
- `.agents/skills/invest-sop-pre-market/checklists/report-completeness.md`
- `.agents/skills/invest-sop-pre-market/references/data-flow.md`
- `.agents/skills/invest-sop-pre-market/references/decision-matrix.md`

Key entry points (do NOT modify — invoke only):

- `stockhot/invest_sop/scripts/generate_premarket_report.py` — Workflow A, produces `{date}_pre_market.md`
- `stockhot/invest_sop/scripts/generate_directive.py` — Workflow B, produces `{date}_directive.md`
- `stockhot/invest_sop/scripts/run_daily_advisor.py` — cron orchestrator (advisor daily + report)

## When This Applies

Use the skill whenever the task includes any of the following:

- 盘前分析（pre-market analysis）—— 读取已采集数据，对持仓套用 SOP 决策矩阵
- 盘前报告生成（pre-market report）—— 产出 `{date}_pre_market.md`
- 晨间指令（morning directive）—— 产出 `{date}_directive.md`
- 持仓决策矩阵（holding decision matrix）—— 四维评估（逻辑/事件/技术/周期）+ 矩阵 A/B
- 风控检查（risk control check）—— 仓位/板块集中度/止损距离合规校验

## Required Agent Behavior

The requirements below are a non-exhaustive summary. They do not replace `.agents/skills/invest-sop-pre-market/SKILL.md`.

Agents working on 盘前分析 tasks must:

1. read collected data only —— 只读 SQLite 的 `invest_*` 表和 `advisor_runs`，不调 AKShare、不下单、不改库
2. invoke the existing scripts —— 复用 `generate_premarket_report.py` / `generate_directive.py`，不重写报告生成逻辑
3. phrase operations as matrix results —— 写「决策矩阵结果：减仓30%」，不写「建议买入」「应该加仓」
4. fill §3-7 analysis manually —— 生成器对持仓四维评估、新增备选、今日重点、风控、复盘只输出占位表，分析内容由 agent 根据 `references/decision-matrix.md` 填充
5. handle missing data per §6 —— 缺表标「数据不可用」，NULL 列标「N/A」，核心表空时停止生成
6. report what was filled, left as placeholder, or marked unavailable —— 区分实填、占位、不可用

## Guardrails

This skill is guidance for 盘前分析 work only.

Agents must not:

- 直接下单或下达交易指令 —— 报告只呈现分析，决策由人工
- 调用 AKShare 采集数据 —— 采集属于 `daily-market-scan` skill 和 `stockhot/invest_sop/scripts/` 的采集脚本
- 修改数据库 —— 所有查询必须是只读 SELECT
- 捏造缺失数据 —— 不得估算、插值、编造数值
- 修改 `generate_premarket_report.py` / `generate_directive.py` 源码 —— 复用而非修改
- 跳过 §6 错误处理 —— 核心表（holdings / overseas market）全空时不得生成"看起来完整"的报告

## Source of Truth

If this section and the skill differ in detail, treat `.agents/skills/invest-sop-pre-market/SKILL.md` as the source of truth for pre-market SOP report-generation behavior. The SOP methodology source of truth is `.sisyphus/drafts/a-share-pre-market-sop.md`.

# 研报写作流程 Skill（research-report）

This repository expects coding agents to follow the research-report skill for any 研报写作、深度分析、产业链报告、个股研报、方法论报告 work involving producing long-form Chinese research documents under `docs/`.

## Default Rule

For tasks involving 写研报、深度报告、产业链分析、个股研报、方法论报告、估值报告, agents must read and follow:

- `.agents/skills/research-report/SKILL.md`

Companion materials are available here:

- `.agents/skills/research-report/references/report-templates.md` — 4 种报告类型的完整章节结构（写作前必读）
- `.agents/skills/research-report/references/data-sourcing.md` — 数据来源分级体系 + 采集方法 + 引用格式
- `.agents/skills/research-report/checklists/report-quality.md` — 提交前的质量检查清单（18 项）

## When This Applies

Use the skill whenever the task includes any of the following:

- 研报写作（research report authoring）—— 在 `docs/` 下产出长篇中文研究文档
- 个股深度研报（single-stock deep report）—— 11 章景气度结构
- 产业链研报（industry-chain report）—— 6 节景气度结构或传统 8 章
- 方法论研报（methodology report）—— 固定 8 章结构
- 分析笔记/短篇（short analysis）—— 轻量 5 节结构
- 估值报告（valuation report）—— 概率加权三情景区间

## Required Agent Behavior

The requirements below are a non-exhaustive summary. They do not replace `.agents/skills/research-report/SKILL.md`.

Agents working on 研报 tasks must:

1. reuse engines, don't reinvent —— 财务/估值/景气度数据调 `davis_analyzer` 引擎，不手算
2. reference sibling skills —— 景气度方法论遵循 `industry-prosperity`，亏损标的估值遵循 `valuation-loss-making-targets`，本 skill 只管写作编排
3. tag every number with a named source —— ≥80% 数字带 inline 来源标签，无来源数字不可接受
4. output probability-weighted ranges, never a single target price —— 悲观/中性/乐观三情景概率加权
5. declare what was computed, estimated, or unavailable —— 区分实算/估算/不可用，信息缺乏处标注，不编造
6. verify before commit —— 用 `checklists/report-quality.md` 逐项检查，全通过才提交
7. commit + push is mandatory —— 遵循 Post-Report Push Rule，中文文件名，`feat(docs): add {name}` 格式

## Guardrails

This skill is guidance for 研报写作 work only.

Agents must not:

- 给单一目标价 —— 必须概率加权区间
- 编造数据 —— 无来源数字不可接受，信息缺乏标注"估算/不可用"
- 写买卖建议 —— 合规风险，表述为"矩阵结果"/"投资框架定位"
- 复制旧报告表格而不重新取数
- 用 PE/PEG 对亏损标的做主估值 —— 遵循 `valuation-loss-making-targets` 的三角框架
- 跳过验证直接提交 —— `checklists/report-quality.md` 全通过才提交
- 提交后不 push —— "A report is not done until it is on GitHub"

## Source of Truth

If this section and the skill differ in detail, treat `.agents/skills/research-report/SKILL.md` as the source of truth for research-report authoring workflow. The methodology details defer to `docs/方法论/` (9 篇方法论文档) and the `davis_analyzer` engine for all quantitative computations.
