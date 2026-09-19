# AGENTS.md

This repository expects coding agents to follow the local development environment skill for any environment-related work.

## 双仓拓扑（2026-09-19 拆分后）

本仓库（`~/Projects/AshareSop`，原 CodeAgentDashboard）是 **stockhot 基础仓**：盘面采集、热点分析、AI 建议、盘前 SOP、通知推送。姊妹仓 `~/Projects/davis-analyzer` 是 **davis 应用仓**：戴维斯选股引擎、景气度、估值、模拟盘、研报/发布流水线。

- **数据真身**在 workspace 级 `~/Projects/.ashare-data/storage/`，两仓各自以 `storage/` symlink 回接共享（DB/缓存/发布素材）。
- davis 仓 `.venv` 以 editable 方式安装本仓库 `stockhot`（`import stockhot` 可用）；本仓不依赖 davis。
- 研报写作、估值分析、景气度分析任务在 **davis 仓**执行，其 AGENTS.md 与 skills 在该仓 `.agents/skills/`。
- 系统服务已分家：本仓 2 路 inhibit unit + davis 仓 19 路 davis unit（见 `~/.config/systemd/user/`）。

## Agent 工作方式（Karpathy 四条，本地化版）

1. 动手前先想清楚方案，非平凡改动先出计划再写代码。
2. 从能解决问题的最简单方案开始，不过度设计。
3. 只做与任务直接相关的最小修改，不顺手重构。
4. 交互式会话中遇歧义先问再动手；**定时/无人值守任务**（盘面扫描、盘后总结、盘前报告等 cron 流程）不等待人工——按各 SOP 纪律记录异常后继续执行，事后在报告中说明。

## Python 解释器（硬性）

- 统一使用仓库根目录的 `.venv/bin/python`。系统无 `python` 命令，系统 `python3` 未安装 pandas 等依赖，直接调用必然失败。
- 示例（从仓库根目录运行）：`.venv/bin/python -m stockhot.eod_review.push_eod_feishu`

## 命令输出截断规则（硬性）

所有可能产生长输出的命令，**必须主动加截断/过滤**，防止大量无关输出进入上下文浪费 token：

| 命令类型 | 必须加的截断 | 示例 |
|---------|------------|------|
| 行情/数据查询 | `\| head -50` 或 `\| tail -20` | `python3 -c "...行情查询..." \| head -50` |
| pytest（全量跑） | `2>&1 \| tail -5`（只看结果摘要） | `.venv/bin/python -m pytest stockhot/ 2>&1 \| tail -5` |
| 日志文件查看 | `\| tail -20` 或 `\| grep "关键词"` | `tail -20 panic_alert.log` |
| DataFrame 打印 | `.head(10)` / `.tail(5)` / 选中列 | `print(df[['col1','col2']].head(10))` |
| tqdm 进度条 | `2>/dev/null`（stderr 丢弃） | `python script.py 2>/dev/null` |
| AKShare 拉取 | 确认行数后只看样本 | `print(f"{len(df)} 行"); print(df.head(3))` |

**例外**：输出需要精读消化的场景（研报数据/财务表/checklist 内容），不加截断。

**原则**：不确定输出多长时，默认加 `| head -50`；发现关键信息在后半段再去掉截断重跑。

## RTK 使用规范（RTK Token-Saving CLI Proxy）

本仓库已全局安装 [RTK](https://github.com/reachingforthejack/rtk)（Rust Token Killer，`/home/leo/.local/bin/rtk`，v0.43.0+），一个为 LLM agent 设计的命令行 token 节省代理。**使用与否按任务类型区分，不强制全量替代原生命令。**

### 何时使用 RTK（代码/工程类工作）

涉及**代码修改、引擎调试、工程操作**时，优先用 `rtk` 前缀替代原生命令，节省 token：

- 版本控制：`rtk git status` / `rtk git log` / `rtk gh pr list`
- 目录遍历：`rtk ls` / `rtk tree` / `rtk find -name "*.py"`
- 搜索：`rtk grep` / `rtk rg "pattern"`
- 构建/测试：`rtk test`（只看失败）/ `rtk pytest` / `rtk tsc`（分组错误）
- 依赖/格式：`rtk deps` / `rtk format` / `rtk lint`
- diff/log：`rtk diff`（只看变更行）/ `rtk log`（去重日志）
- JSON/查看：`rtk json` / `rtk read <file>` / `rtk smart <file>`

### 何时禁用 RTK（研报/分析类工作）

涉及**研报写作、数据分析、财务取数**时，**使用原生命令**，不加 `rtk` 前缀：

- `tushare` / `stockhot` 取数与数据库查询
- 读取要精读的研报模板、财务表格、checklist
- 任何输出需要完整进入上下文的场景

**判定原则**：输出是要"精读消化"的（研报数据/财务表/模板）→ 原生命令；输出是要"扫一眼找信息"的（git 状态/目录结构/测试结果/依赖列表）→ 用 rtk。

### 验证与回退

- 验证安装：`rtk --version`（应显示 rtk X.Y.Z）
- 查看 token 节省：`rtk gain`
- 不确定时用原生命令（永远准确），RTK 只是优化层
- `rtk proxy <cmd>` 或 `rtk run <cmd>` 可绕过过滤执行原始命令（调试用）

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

Use this skill whenever the task includes any of the following:

- 日常盘面扫描（daily market scan）—— 调用涨停、龙虎榜、资金流、风险提示四个模块
- 涨停分析（limit_up）—— 涨停池、炸板池、连板梯队、板块联动、封单强度
- 龙虎榜分析（dragon_tiger）—— 机构席位、营业部、游资追踪
- 资金流向（fund_flow）—— 大盘/板块资金流趋势判断
- 风险提示（risk_alert）—— ST 股票、异常波动、资金出逃、高位连板
- 为下游 skill（invest-sop-pre-market）采集当日市场数据

## Required Agent Behavior

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

Agents working on AI 交易建议 tasks must:

1. use the prompt registry —— 所有 LLM prompt 从 prompt registry 加载，永远不在代码中内联 prompt 文本
2. use the LLM provider abstraction —— 通过 `get_provider()` 获取 provider 实例，不直接 import 具体厂商 SDK
3. use the hardcoded conflict resolver —— 当信号冲突时由确定性 resolver 仲裁，绝不将冲突交给 LLM 自行决定
4. respect `MAX_STOCKS_PER_DAILY_RUN` —— `daily` 命令处理的股票数不得超过此上限（当前为 20），超出时截断并告警
5. call `run_for_stock()` as the entry point —— 单股分析通过 `run_for_stock(code, trade_date, holding=...)` 调用
6. report what was generated, skipped, or errored —— 区分 `generated`（有建议）、`skipped`（无建议或出错）

davis_analyzer 数据缺位时（拆分后本仓不再安装 davis 引擎），`data_sources/fundamental.py` 已惰性降级为"数据不可用"，不得编造 fallback。

## Guardrails

This section is guidance for AI 交易建议 work only.

Agents must not:

- 自动下单或执行实际交易 —— advisor 只生成建议，不触碰交易系统
- 让 LLM 仲裁信号冲突 —— 冲突必须由硬编码 resolver 处理，LLM 只负责生成自然语言表述
- 捏造 fallback 数据 —— 当 davis_analyzer / technical_analyzer 数据缺失时标注"数据不可用"，不得编造
- 超过 `MAX_STOCKS_PER_DAILY_RUN` 上限 —— 批量运行必须截断，不得动态提高上限
- 在代码中内联 prompt 文本 —— 所有 prompt 必须从 prompt registry 加载，便于版本管理
- 修改 `technical_analyzer` / `sell_monitor` 源码 —— 复用而非修改，advisor 只消费它们的输出

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

# 盘后总结 Skill（after-hours-review）

This repository expects coding agents to follow the after-hours-review skill for any 盘后总结、今日复盘、盘后复盘、今日热点 work involving generating a same-day market recap after close.

## Default Rule

For tasks involving 盘后总结、今日复盘、板块涨幅分析、领涨催化归因, agents must read and follow:

- `.agents/skills/after-hours-review/SKILL.md`

Companion materials:

- `.agents/skills/after-hours-review/README.zh-CN.md`

Output location: `docs/复盘/盘后/{YYYY-MM-DD}_盘后总结.md`

## When This Applies

Use the skill whenever the task includes any of the following:

- 盘后总结（after-hours review）—— 收盘后生成当日市场总结
- 今日复盘（daily recap）—— 梳理板块涨幅 + 领涨个股 + 热点方向
- 领涨催化归因（catalyst attribution）—— web 搜索找出涨停/板块领涨的原因
- 今日热点（today's hotspots）—— 分析当日核心热点方向

## Required Agent Behavior

Agents working on 盘后总结 tasks must:

1. read daily-market-scan data only —— 只读 SQLite 的 daily_data 表（fund_flow_sector / limit_up_pool / limit_up_analysis / dragon_tiger_detail），不调 AKShare
2. verify upstream data exists —— 若 daily-market-scan 未运行，提示用户先采集
3. cross-validate hotspots —— 热点方向需"板块涨幅排名 × 涨停密度"交叉确认，不单一维度
4. web-search catalysts with sources —— 每条催化必须附 web 来源 URL，搜不到标注"未找到明确催化"
5. no fabrication —— 不编造催化消息，不猜测，信息缺乏处标注
6. no trading advice —— 只呈现数据和分析，不做买卖建议

## Guardrails

Agents must not:

- 自己调 AKShare 采集数据 —— 采集属于 `daily-market-scan` skill
- 编造催化原因 —— 搜不到就标注，不猜测
- 做买卖建议 —— 合规风险
- 更新 docs/README.md 索引 —— 盘后总结是每日产物，非研报
- 修改数据库 —— 所有查询必须是只读

## Source of Truth

If this section and the skill differ in detail, treat `.agents/skills/after-hours-review/SKILL.md` as the source of truth for after-hours review methodology. Data format defers to `stockhot/storage/database.py` `get_daily_data()`.

# 盘前分析 SOP Skill（invest-sop-pre-market）【跨仓 skill】

> 拆分说明：本 skill 的**执行脚本**在本仓 `stockhot/invest_sop/scripts/`，但 skill 文档（SKILL.md/references/checklists）随研报体系迁至 davis 仓 `.agents/skills/invest-sop-pre-market/`。两仓 AGENTS.md 均保留本节入口说明。

## Default Rule

For tasks involving 盘前报告、晨间指令、或 SOP 决策矩阵评估, agents must read and follow:

- `~/Projects/davis-analyzer/.agents/skills/invest-sop-pre-market/SKILL.md`

Key entry points (do NOT modify — invoke only):

- `stockhot/invest_sop/scripts/generate_premarket_report.py` — Workflow A, produces `{date}_pre_market.md`
- `stockhot/invest_sop/scripts/generate_directive.py` — Workflow B, produces `{date}_directive.md`
- `stockhot/invest_sop/scripts/run_daily_advisor.py` — cron orchestrator (advisor daily + report)

## When This Applies

- 盘前分析（pre-market analysis）—— 读取已采集数据，对持仓套用 SOP 决策矩阵
- 盘前报告生成（pre-market report）—— 产出 `{date}_pre_market.md`
- 晨间指令（morning directive）—— 产出 `{date}_directive.md`
- 持仓决策矩阵（holding decision matrix）—— 四维评估（逻辑/事件/技术/周期）+ 矩阵 A/B
- 风控检查（risk control check）—— 仓位/板块集中度/止损距离合规校验

## Required Agent Behavior

1. read collected data only —— 只读 SQLite 的 `invest_*` 表和 `advisor_runs`，不调 AKShare、不下单、不改库
2. invoke the existing scripts —— 复用 `generate_premarket_report.py` / `generate_directive.py`，不重写报告生成逻辑
3. phrase operations as matrix results —— 写「决策矩阵结果：减仓30%」，不写「建议买入」「应该加仓」
4. fill §3-7 analysis manually —— 生成器只输出占位表，分析内容由 agent 按 decision-matrix.md 填充
5. handle missing data per §6 —— 缺表标「数据不可用」，NULL 列标「N/A」，核心表空时停止生成
6. report what was filled, left as placeholder, or marked unavailable

## Guardrails

- 直接下单或下达交易指令 —— 报告只呈现分析，决策由人工
- 调用 AKShare 采集数据 —— 采集属于 `daily-market-scan` skill
- 修改数据库 —— 所有查询必须是只读 SELECT
- 捏造缺失数据 —— 不得估算、插值、编造数值
- 修改 `generate_premarket_report.py` / `generate_directive.py` 源码 —— 复用而非修改
- 跳过 §6 错误处理 —— 核心表全空时不得生成"看起来完整"的报告

## Source of Truth

Skill 文档以 davis 仓 `.agents/skills/invest-sop-pre-market/SKILL.md` 为准；SOP 方法论 source of truth 为 `.sisyphus/drafts/a-share-pre-market-sop.md`。

# 研报产出与推送（Post-Report Push Rule）

2026-09-19 拆分后，**研报（`docs/研报/`）产出与推送流程整体归属 davis 仓**——写研报、验证、commit、push 的规则见 davis 仓 AGENTS.md 同名节。

本仓 docs/ 仅保留每日产物（`docs/复盘/盘后/`），盘后总结不进索引、不推送。

Remote: `origin` → `git@github.com:OldDriverTakeUFly/AshareSop.git`

# 公司中枢 · 项目管理协议（davis-analyzer 试点）

`davis_analyzer` 子系统作为首个试点接入"公司中枢"项目管理流程（数据源：`~/ZCodeProject/公司中枢/`，协议见其 `README.md`）。Agent 在本仓工作时的约定：

1. **读上下文**：涉及 davis/stockhot 的任务规划时，先读项目卡 `~/ZCodeProject/公司中枢/projects/davis-analyzer.yaml` 了解里程碑与当前状态。
2. **写进度**：会话中完成了里程碑级进展（如某引擎上线、回测达标、部署变更），按协议直接更新项目卡（milestones/review.updated/note），新工作项追加到 `tasks.yaml`（id 规则 T+三位自增，project 填 `davis-analyzer`）。收尾按 scoped 自动提交协议：`plan check` 通过 → `git -C ~/ZCodeProject status --porcelain -- 公司中枢/` → 只 add 本会话写入的文件 → `git commit -m "plan(<项目id>): 摘要"` 并回报 hash；非本会话变更保留不提交、报告说明（详见中枢 README）。
3. **命令行**：`~/ZCodeProject/公司中枢/tools/.venv/bin/plan -r ~/ZCodeProject/公司中枢 ls|today|check|scan` 可随时查看全局进度（输出已按本文件规则保持精简）。
4. **边界**：中枢只管状态与任务索引，不复制代码细节；本文件与本节冲突时，工程规则以本文件为准，管理协议以中枢 README 为准。
