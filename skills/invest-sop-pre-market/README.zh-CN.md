# A 股盘前分析 SOP Skill

本 skill 读取已采集的盘面与持仓数据，按 SOP 决策矩阵对每只活跃持仓做评估，产出结构化的盘前报告与晨间指令。

## 定位

**报告生成层。** 只读取 SQLite 中已采集好的数据，不做数据采集、不下单、不修改数据库。所有买卖决策由人工完成，报告只呈现数据、套用决策矩阵、陈述矩阵输出。

上游 `daily-market-scan` skill 负责采集涨停/龙虎榜/资金流/风险提示，写入 `daily_data` / `analysis_results`；本 skill 主要消费 `invest_*` 表和 `advisor_runs`（数据源不同，见下表）。

## 两个 Workflow

| Workflow | 时机 | 触发数据 | 产出 |
|----------|------|----------|------|
| **A 盘前研究报告** | 21:30（盘后数据采集完成后） | 海外市场 / 国内事件 / 产业链 / 期货情绪 / 周期评估 / 持仓 + advisor 建议 | `{YYYY-MM-DD}_pre_market.md` |
| **B 晨间指令** | 09:00（morning_confirm 之后） | 前夜报告 + invest_morning_data | `{YYYY-MM-DD}_directive.md` |

实际生产环境由 cron 在 08:15 调起 `run_daily_advisor.py`（先跑 advisor daily，再生成盘前报告），见 `references/data-flow.md` 的时间表。

## 数据表速查

报告生成器 `generate_premarket_report.py` 只读下列表（写脚本/读角色）：

| 表 | 写入方 | 报告是否读取 |
|----|--------|:------------:|
| `invest_overseas_market` | `overseas_market_data.py` | ✅ §1.1 |
| `invest_domestic_events` | `domestic_events.py` | ✅ §1.2 |
| `invest_futures_sentiment` | `futures_sentiment.py` | ✅ §1.1 股指期货行 |
| `invest_cycle_assessments` | `weekly_cycle.py --update-sector`（周更） | ✅ §2 |
| `invest_holdings` | `update_holdings.py`（盘后）/ `holdings_cli.py`（手工） | ✅ §3 + 持仓监控 |
| `invest_morning_data` | `morning_confirm.py`（08:30） | ❌ 不入盘前报告（08:15 早于 08:30，数据尚未存在） |
| `invest_supply_chain` | `supply_chain.py` | ❌ 仅 `weekly_cycle.py` 读取 |
| `invest_holdings_transactions` | `update_holdings.py` | ❌ |
| `invest_sector_rules` | 初始化 seed（13 行） | ❌（间接，被 `update_holdings.py` 用作止损/目标阈值） |
| `invest_watchlist` | `watchlist_cli.py` / `advisor watchlist` | ❌ 仅 `advisor daily` 读取 |
| `advisor_runs` | `advisor daily`（`persist_recommendation`） | ✅ 「AI 综合建议」节 |

注意：报告实际输出 9 节（不止 SKILL.md §8 列的 7 节模板节），其中「持仓监控」和「AI 综合建议」来自 `sell_monitor` 与 `advisor.report_integration` 的运行时集成。详见 `references/data-flow.md`。

## 快速开始

```bash
# 1. 生成盘前报告（含 advisor 建议 + 持仓监控）
PYTHONPATH=/home/leo/Projects/CodeAgentDashboard \
  python stockhot/invest_sop/scripts/generate_premarket_report.py --date 2026-06-22

# 2. 生成晨间指令（morning_confirm 之后跑）
PYTHONPATH=/home/leo/Projects/CodeAgentDashboard \
  python stockhot/invest_sop/scripts/generate_directive.py --date 2026-06-22

# 3. 一键编排（先 advisor daily，再盘前报告）—— cron 用这个
PYTHONPATH=/home/leo/Projects/CodeAgentDashboard \
  python stockhot/invest_sop/scripts/run_daily_advisor.py --date 2026-06-22

# 4. 只看空模板（不带数据，用于人工填写）
python stockhot/invest_sop/scripts/generate_premarket_report.py --date 2026-06-22 --template-only
```

报告保存到 `stockhot/invest_sop/reports/`（目录按需自动创建）。

## 局限性

- **生成器的 §3-7 输出占位表**：`generate_premarket_report.py` 对持仓四维评估、新增备选、今日重点、风控检查、昨日复盘这些节只输出空白模板行，**四维评估 + 矩阵 A/B 的实际分析由 agent/人工根据 `references/decision-matrix.md` 填充**。生成器只负责把已采数据灌进 §1、§2、持仓监控、AI 建议。
- **`invest_morning_data` 不进盘前报告**：`morning_confirm.py` 在 08:30 执行，晚于报告生成时刻 08:15，故报告读不到晨间确认数据。该表供 `generate_directive.py` 在 09:00 之后使用。
- **`reports/` 目录按需创建**：不存在于仓库中，首次跑脚本时由 `config.py` / 各脚本自动 mkdir。
- **report_template.md 与生成器存在 drift**：`templates/report_template.md`（SOP §8.1 原版）与生成器的 `build_section_*` 是两条并行实现，字段略有出入（如模板有日经225，生成器没有）。报告实际以生成器输出为准。
- **本 skill 只读**：不写库、不下单、不调 AKShare；采集和下单分别属于 `daily-market-scan` 与人工/交易系统。

## 与其他 skill 的关系

| Skill | 关系 |
|-------|------|
| `daily-market-scan` | 数据采集上游，但其写入的 `daily_data` / `analysis_results` 本 skill **不直接读**；本 skill 主要读 `invest_*` 表 |
| `ai-trading-advisor`（`stockhot/advisor/`） | 产出 `advisor_runs`，被本 skill 的「AI 综合建议」节消费 |
| `valuation-loss-making-targets` / `industry-prosperity` / `multi-factor-screening` | 与本 skill 无直接数据流，方法论上可互为参考 |
| `local-development-environment` | AKShare/Tushare 导入失败或数据库缺失时先跑这个 |
