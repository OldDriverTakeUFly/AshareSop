# StockHot-CN — A股个人投资者决策工具箱

一套面向 A 股个人投资者的本地化工具箱：从每日盘面数据采集、热点分析，到多因子量化选股、景气度周期定位、困境反转估值，再到 AI 交易建议生成与 Telegram 推送，覆盖「复盘 → 选股 → 估值 → 持仓决策 → 推送」全链路。

> 定位：**辅助决策的数据与分析层**，不自动下单、不给投资建议兜底，所有买卖由人工决策。

---

## 能力总览

本仓库为 **stockhot 基础仓**（2026-09-19 与 davis-analyzer 拆分后），由三个子系统组成：

| 子系统 | 目录 | 解决什么问题 | 数据源 |
|--------|------|--------------|--------|
| **StockHot 盘面分析** | `stockhot/` | 每日盘面数据采集 + 热点分析 + 报告图片生成 | AKShare |
| **AI 交易建议** | `stockhot/advisor/` | 多源信号聚合 → 确定性仲裁 → LLM 生成建议 | 引擎输出 |
| **盘前 SOP 报告** | `stockhot/invest_sop/` | 持仓决策矩阵 + 盘前报告 + 晨间指令 | 采集入库的数据 |

**姊妹仓 `~/Projects/davis-analyzer`**（davis 应用仓）：戴维斯双击选股引擎、景气度/困境反转估值、量化模拟盘、研报与发布流水线、其 WebUI。该仓 `.venv` 以 editable 安装本仓 `stockhot`。数据真身两仓共享于 `~/Projects/.ashare-data/storage/`，各自 `storage/` symlink 回接。

## 一、StockHot 盘面分析（`stockhot/`）

每日收盘后自动采集 A 股盘面数据，分析后入库，供前端可视化与上游模块消费。

### 四大扫描模块（`daily-market-scan` skill 编排）

| 模块 | 入口函数 | 产出 |
|------|----------|------|
| 涨停分析 `limit_up/` | `run_limit_up_analysis(date)` | 涨停池 / 炸板池 / 跌停池、连板梯队、板块联动、封单强度 |
| 龙虎榜 `dragon_tiger/` | `run_dragon_tiger_analysis(date)` | 机构席位、营业部、游资动向 |
| 资金流向 `fund_flow/` | `run_fund_flow_analysis(date)` | 大盘 / 板块资金流、主力 vs 散户、多日趋势 |
| 风险提示 `risk_alert/` | `run_risk_alert_analysis(date)` | ST、停复牌、异常波动、资金出逃、高位连板 |

固定执行顺序：`limit_up → dragon_tiger + fund_flow 并行 → risk_alert`（最后一个读取上游 DB 数据）。每个模块独立 try/except，单模块失败不影响其他模块。

### 配套引擎

- **技术分析 `technical_analyzer/`** — MA / RSI / MACD / KDJ / Bollinger / 支撑阻力 / 量价 / 综合技术评分，共 9 个锁定指标，基于 pandas-ta，不依赖 TA-Lib。
- **卖出监控 `sell_monitor/`** — 4 个独立卖出信号：硬止损 / 移动止损 / 目标价达成 / 逻辑破坏，各自触发不仲裁。
- **AI 分析 `ai_analyzer/`** + **研报 `research_report/`** + **图片生成 `image_generator/`** — 热点归因、结构化报告、小红书尺寸图片。
- **FastAPI 后端 `api/`**（端口 8321）— 上述数据的 REST 接口，Basic Auth 保护。

---

## 三、AI 交易建议（`stockhot/advisor/`）

对个股生成建仓 / 调仓 / 清仓 / 做T 建议。**核心原则：LLM 只负责生成自然语言表述，信号冲突由硬编码 resolver 仲裁，绝不交给 LLM 自行决定。**

### CLI

```bash
python -m stockhot.advisor ask 600519           # 单股生成建议
python -m stockhot.advisor daily                # 持仓 + watchlist 批量（≤20）
python -m stockhot.advisor watchlist add 600519 # watchlist 管理
```

### 工作流（`run_for_stock`）

1. **信号聚合** — 实时价、技术分、Davis 分、已触发卖出信号、持仓上下文（仓位/成本/止损/浮盈亏）
2. **确定性仲裁** — T5 规则产出 action（EXIT/TRIM/BUY/HOLD），做T 条件单独判定
3. **映射建议类型** → 选 prompt → 调 LLM
4. **幻觉守卫** — 校验 entry_zone / stop_loss / target 合理范围，违规则降级 confidence=LOW 并标注；LLM 不可用时返回 NO_ACTION，绝不编造
5. **持久化** — 按 `(date, code, type)` 幂等，`--force` 覆盖

### LLM Provider（`llm_provider.py`）

三选一，均走 OpenAI 兼容协议，由 `LLM_PROVIDER` 切换：`glm`（默认，GLM-5.2）/ `openai`（gpt-4o-mini）/ `deepseek`（deepseek-chat）。

---

## 四、通知推送（`stockhot/notification/`）

Telegram Bot 推送，纯 `httpx` 调用（不引入 `python-telegram-bot`）。

- **紧急优先** — EXIT 动作或 HIGH 置信度单独成条、优先发送（🚨）
- **批量合并** — 其余按每条消息 20 只股票合并为表格（📊），单次最多 5 条
- **用户授权** — `TELEGRAM_ALLOWED_USER_IDS` 白名单，白名单外命令一律丢弃
- **429 处理** — 读取服务端 `retry_after` 而非默认退避；最多重试 3 次

---

## 五、盘前 SOP（`stockhot/invest_sop/`）

读取已采集数据，对持仓套用 SOP 决策矩阵，生成每日盘前报告。**只读 SQLite，不调 AKShare、不下单、不改库。**

### 脚本（`scripts/`）

| 脚本 | 产出 |
|------|------|
| `generate_premarket_report.py` | `{date}_pre_market.md`（7 节：环境/板块/持仓决策/备选/重点/风控/复盘） |
| `generate_directive.py` | `{date}_directive.md`（晨间指令，叠加早盘对比） |
| `weekly_cycle.py` | `{date}_cycle_review.md`（周度周期/拥挤度） |
| `run_daily_advisor.py` | cron 编排：`advisor daily` → 盘前报告 |
| `overseas_market_data.py` 等 | 海外市场/事件/供应链/期货/早盘数据采集入库 |

---

## Agent 技能规范（`.agents/skills/`）

ZCode 自动发现的 skill，定义各分析任务的方法论与硬编码参数（权重/阈值/执行顺序），agent 执行对应任务时必须遵循：

| Skill | 适用场景 |
|-------|----------|
| `local-development-environment` | 本地环境搭建 / 依赖 / 运行时选择 |
| `daily-market-scan` | 盘面扫描四模块编排 |
| `after-hours-review` | 盘后总结 / 热点催化归因 |

研报写作/景气度/多因子/估值/盘前 SOP 五个 skill 文档在 davis 仓 `.agents/skills/`。详见 [`AGENTS.md`](AGENTS.md)。

## 快速开始

### 环境变量

复制 `.env.template` → `.env`，按需填入：

- **通用** — `PROJECT_ROOT`、`STOCKHOT_API_PASSWORD`、`CORS_ORIGINS`
- **AI 建议** — `LLM_PROVIDER`（默认 `glm`）、`LLM_API_KEY`
- **推送** — `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、`TELEGRAM_ALLOWED_USER_IDS`

### 后端

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# StockHot API
uvicorn stockhot.api.main:app --port 8321
```


## 项目结构

```
stockhot/                  # 盘面分析后端
├── limit_up/ dragon_tiger/ fund_flow/ risk_alert/   # 四大扫描模块
├── technical_analyzer/    # 技术指标（pandas-ta）
├── sell_monitor/          # 卖出信号监控
├── ai_analyzer/ research_report/ image_generator/   # AI 分析 + 报告 + 图片
├── advisor/               # AI 交易建议引擎
├── notification/          # Telegram 推送
├── invest_sop/            # 盘前 SOP 报告（接入策略信号）
├── api/ core/ storage/    # FastAPI + 配置 + 存储

scripts/ops/               # 运维 shell（盘后/交易时段 inhibit、补数等待）
docs/复盘/盘后/            # 每日盘后总结
.agents/skills/            # Agent 技能规范（ZCode 自动发现）
storage/ -> ~/Projects/.ashare-data/storage/   # 数据真身 symlink（gitignore）
```

Davis 引擎/模拟盘/研报流水线结构 → davis 仓 README。

## 文档

- [`SPEC.md`](SPEC.md) — 项目需求规格说明书
- [`AGENTS.md`](AGENTS.md) — Agent 行为规范（双仓拓扑 + 各 skill 强制行为与护栏）
- [`docs/复盘/盘后/`](docs/复盘/盘后/) — 每日盘后总结（当日产物，不进索引）
- 研报/方法论/回测记录 → davis 仓 `docs/`（见该仓 README）
- [`.agents/skills/`](.agents/skills/) — Agent 技能规范

## 部署

Docker + docker-compose（NAS），见 `docker-compose.yml` 与 `docker/DEPLOY.md`。

