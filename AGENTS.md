# AGENTS.md

This repository expects coding agents to follow the local development environment skill for any environment-related work.

## Default Rule

For tasks involving local setup, dependency installation, runtime selection, environment repair, toolchain isolation, local services, or project switching, agents must read and follow:

- `skills/local-development-environment/SKILL.md`

Companion materials are available here:

- `skills/local-development-environment/checklists/preflight.md`
- `skills/local-development-environment/checklists/cleanup.md`
- `skills/local-development-environment/examples/setup-session.md`
- `skills/local-development-environment/examples/switch-projects.md`
- `skills/local-development-environment/examples/troubleshoot-conflicts.md`
- `skills/local-development-environment/references/tooling-matrix.md`
- `skills/local-development-environment/README.zh-CN.md`

## When This Applies

Use the skill whenever the task includes any of the following:

- setting up a local development environment
- switching between projects with different runtime requirements
- fixing dependency conflicts or mixed environments
- choosing Python, Node.js, Java, Ruby, or mixed-language runtime strategies
- deciding whether something should be local, global, or containerized
- documenting team conventions for local environment management

## Required Agent Behavior

The requirements below are a non-exhaustive summary. They do not replace `skills/local-development-environment/SKILL.md`.

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

If this file and the skill differ in detail, treat `skills/local-development-environment/SKILL.md` as the source of truth for environment-management behavior.

# 估值分析 Skill（valuation-loss-making-targets）

This repository expects coding agents to follow the loss-making target valuation skill for any 估值分析 work involving companies that are currently unprofitable.

## Default Rule

For tasks involving 估值分析、亏损标的估值、困境反转定价、或 PS+DCF 三角验证建模, agents must read and follow:

- `skills/valuation-loss-making-targets/SKILL.md`

Companion materials are available here:

- `skills/valuation-loss-making-targets/README.zh-CN.md`
- `skills/valuation-loss-making-targets/references/distress-probability-rules.md`
- `skills/valuation-loss-making-targets/references/valuation-model-schema.md`
- `skills/valuation-loss-making-targets/references/study-script-templates/financial_deep_template.py`
- `skills/valuation-loss-making-targets/references/study-script-templates/scoring_template.py`
- `skills/valuation-loss-making-targets/references/study-script-templates/quant_data_template.py`
- `skills/valuation-loss-making-targets/checklists/differentiation-audit.md`
- `skills/valuation-loss-making-targets/checklists/source-traceability.md`

## When This Applies

Use the skill whenever the task includes any of the following:

- 估值分析（target valuation）for an acquisition or investment target
- 亏损标的（loss-making targets）—— net income 为负、PE 估值失效的场景
- 困境反转（distress / turnaround）候选公司定价
- DCF for loss-making companies —— 需要对负自由现金流做情景化处理
- 同业对比估值（global peer anchoring）—— 跨市场可比公司映射
- PS + DCF 双模型建模与三角验证（Triangle Framework）

## Required Agent Behavior

The requirements below are a non-exhaustive summary. They do not replace `skills/valuation-loss-making-targets/SKILL.md`.

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

If this section and the skill differ in detail, treat `skills/valuation-loss-making-targets/SKILL.md` as the source of truth for loss-making target valuation methodology.

# 日常盘面扫描 Skill（daily-market-scan）

This repository expects coding agents to follow the daily market scan skill for any 盘面扫描、每日复盘、热点数据采集 work involving the four stockhot hot-topic modules.

## Default Rule

For tasks involving 盘面扫描、涨停分析、龙虎榜、资金流向、风险提示、或每日数据采集, agents must read and follow:

- `skills/daily-market-scan/SKILL.md`

Companion materials are available here:

- `skills/daily-market-scan/README.zh-CN.md`
- `skills/daily-market-scan/checklists/scan-completeness.md`
- `skills/daily-market-scan/references/module-orchestration.md`

## When This Applies

Use the skill whenever the task includes any of the following:

- 日常盘面扫描（daily market scan）—— 调用涨停、龙虎榜、资金流、风险提示四个模块
- 涨停分析（limit_up）—— 涨停池、炸板池、连板梯队、板块联动、封单强度
- 龙虎榜分析（dragon_tiger）—— 机构席位、营业部、游资追踪
- 资金流向（fund_flow）—— 大盘/板块资金流趋势判断
- 风险提示（risk_alert）—— ST 股票、异常波动、资金出逃、高位连板
- 为下游 skill（invest-sop-pre-market）采集当日市场数据

## Required Agent Behavior

The requirements below are a non-exhaustive summary. They do not replace `skills/daily-market-scan/SKILL.md`.

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

If this section and the skill differ in detail, treat `skills/daily-market-scan/SKILL.md` as the source of truth for daily market scan orchestration methodology.
