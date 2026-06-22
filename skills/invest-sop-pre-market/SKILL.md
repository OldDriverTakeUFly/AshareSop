---
name: invest-sop-pre-market
description: Use this skill when executing A-share pre-market analysis SOP. Agent reads collected data from SQLite, analyzes holdings using the SOP decision matrix, and generates structured markdown reports. Trigger phrases include '盘前分析', '生成盘前报告', 'pre-market report', '持仓分析'.
---

# A股盘前分析 SOP 执行指南

This skill enables agents to execute the A-share pre-market standard operating procedure. The agent reads collected market data from SQLite, evaluates active holdings through a structured decision matrix, and produces two daily markdown reports.

All behavior in this skill is read-only. The agent generates analysis reports, never trading instructions.

## 1. Overview

**What this skill does:**

The agent follows the pre-market SOP defined in `.sisyphus/drafts/a-share-pre-market-sop.md`. It reads data already collected by data collection scripts, applies the SOP's evaluation framework to every active holding, and fills the report template.

**Key documents:**

| Document | Path |
|----------|------|
| SOP document (source of truth) | `.sisyphus/drafts/a-share-pre-market-sop.md` |
| Report template | `stockhot/invest_sop/templates/report_template.md` |
| AKShare endpoint reference | `stockhot/invest_sop/AKSHARE_ENDPOINTS.md` |
| Database config | `stockhot/invest_sop/config.py` |
| Table migration | `stockhot/invest_sop/migrate.py` |

**Database:**

SQLite at `stockhot/storage/database/stockhot.db`, running in WAL mode. Connection is established via `stockhot.storage.database.get_connection()`.

**Report output directory:**

`stockhot/invest_sop/reports/` (configured as `INVEST_REPORTS_DIR` in `config.py`).

## 2. Prerequisites

Before running this skill, verify:

1. **SOP document exists** at `.sisyphus/drafts/a-share-pre-market-sop.md` and is readable.
2. **SQLite database has the invest_ tables.** If not, run:
   ```bash
   PYTHONPATH=/home/leo/Projects/CodeAgentDashboard python3 stockhot/invest_sop/migrate.py
   ```
3. **Data collection scripts have populated today's data.** Check by querying a table with today's date. If tables are empty for today, the data collection step must run first.

## 3. Data Sources (SQLite Queries)

The database has 9 tables prefixed with `invest_` plus `advisor_runs` (no `invest_` prefix, but read by the report's AI-recommendations section). This section documents the 7 tables the report reads plus `advisor_runs`; the remaining 3 (`invest_holdings_transactions`, `invest_sector_rules`, `invest_watchlist`) are written by other scripts and not read by the report. For the full table inventory see `references/data-flow.md`.

### 3.1 invest_overseas_market (海外市场数据)

Stores overnight overseas market data: US indices, US treasury yields, VIX, A50 futures, and USD/CNY.

**Key columns:** `date`, `sp500_pct`, `nasdaq_pct`, `dow_pct`, `us_10y`, `us_10y_change_bp`, `vix`, `a50_pct`, `usd_cny`

**Example query:**

```sql
SELECT date, sp500_pct, nasdaq_pct, dow_pct,
       us_10y, us_10y_change_bp, vix, a50_pct, usd_cny
FROM invest_overseas_market
WHERE date = date('now', 'localtime')
ORDER BY date DESC
LIMIT 1;
```

**What to focus on:** Any single index move exceeding 1% in absolute value. VIX above 25. US 10Y yield change exceeding 10bp. A50 direction as a direct proxy for A-share opening. Cross-reference with SOP §2.1 event grading (🟢🟡🟠🔴).

### 3.2 invest_domestic_events (国内政策事件)

Stores domestic policy announcements and A-share specific events (regulatory changes, earnings, lockup expirations, block trades, index rebalancing).

**Key columns:** `id`, `date`, `event_name`, `affected_sector`, `impact_direction`, `severity`, `source`

**Example query:**

```sql
SELECT event_name, affected_sector, impact_direction, severity
FROM invest_domestic_events
WHERE date = date('now', 'localtime')
ORDER BY id;
```

**What to focus on:** Events where `affected_sector` matches any active holding's sector. Severity levels map directly to the SOP event grading: 🟢 (normal), 🟡 (watch), 🟠 (alert), 🔴 (crisis). See SOP §2.2 for the A-share event checklist.

### 3.3 invest_supply_chain (产业链指标)

Stores supply chain metrics organized by sector. Each row is one metric observation for one sector on one date.

**Key columns:** `id`, `date`, `sector`, `metric_name`, `value`, `unit`, `source`

**Example query:**

```sql
SELECT sector, metric_name, value, unit
FROM invest_supply_chain
WHERE date = date('now', 'localtime')
ORDER BY sector, metric_name;
```

**What to focus on:** Sectors are `tech`, `new_energy`, `cyclicals`. Within each sector, look at price trends, inventory levels, capacity utilization, and margin spreads. Compare against SOP §2.3 sector-specific signal tables (科技 §2.3.1, 新能源 §2.3.2, 周期品 §2.3.3).

### 3.4 invest_futures_sentiment (期货与市场情绪)

Stores index futures direction, basis data, northbound capital flow, margin balance, and put/call ratio.

**Key columns:** `date`, `if_pct`, `ic_pct`, `im_pct`, `if_basis`, `ic_basis`, `northbound_net`, `margin_balance`, `put_call_ratio`

**Example query:**

```sql
SELECT date, if_pct, ic_pct, im_pct,
       if_basis, ic_basis,
       northbound_net, margin_balance, put_call_ratio
FROM invest_futures_sentiment
WHERE date = date('now', 'localtime');
```

**What to focus on:** Basis direction (positive = bullish, negative = bearish) per SOP §2.4.1 basis signal lookup. Northbound net outflows for 3+ consecutive days signal caution. Margin balance rapid increase signals leverage risk. See SOP §2.4 for full interpretation rules.

### 3.5 invest_morning_data (早上确认数据)

Stores morning confirmation data collected after the pre-market report but before market open: A50 morning move, Nikkei, KOSPI, and updated USD/CNY.

**Key columns:** `date`, `a50_morning_pct`, `nikkei_pct`, `kospi_pct`, `usd_cny_morning`, `notes`

**Example query:**

```sql
SELECT date, a50_morning_pct, nikkei_pct, kospi_pct, usd_cny_morning, notes
FROM invest_morning_data
WHERE date = date('now', 'localtime');
```

**What to focus on:** Compare morning data against overnight pre-study conclusions. If anything changed materially, flag it for the morning directive. See SOP §4.1 for the modification decision tree.

### 3.6 invest_cycle_assessments (行业周期评估)

Stores the weekly cycle position assessment for each sector. Updated by `weekly_cycle.py`, not daily.

**Key columns:** `id`, `sector`, `cycle_position`, `crowding_score`, `assessment_date`, `notes`

**Example query:**

```sql
SELECT sector, cycle_position, crowding_score, assessment_date, notes
FROM invest_cycle_assessments
ORDER BY sector;
```

**What to focus on:** `cycle_position` values are `复苏`, `繁荣`, `衰退早期`, `衰退晚期`. `crowding_score` is 0-12 (see SOP §3.5.2 scoring card). Sectors at 复苏 or 繁荣 early stage with crowding below 7 are candidates for new positions. See SOP §3.1 for the four-stage model and §3.6 for the quick reference table.

### 3.7 invest_holdings (持仓记录)

Stores current and historical holdings. Active holdings have `status = 'active'`.

**Key columns:** `id`, `code`, `name`, `sector`, `entry_price`, `current_price`, `stop_loss_logic`, `stop_loss_technical`, `stop_loss_hard`, `target_price`, `position_pct`, `entry_date`, `status`, `notes`, `quantity`, `avg_cost`, `davis_score_at_buy`, `thesis_snapshot_json`, `updated_at`

(`quantity`, `avg_cost`, `davis_score_at_buy`, `thesis_snapshot_json` were added by the migration in `migrate.py`; `thesis_snapshot_json` stores the original investment thesis as JSON and is the baseline for the logic-status dimension in §5.1 — see `references/decision-matrix.md`.)

**Example query:**

```sql
SELECT code, name, sector, entry_price, current_price,
       stop_loss_logic, stop_loss_technical, stop_loss_hard,
       target_price, position_pct, entry_date, notes
FROM invest_holdings
WHERE status = 'active'
ORDER BY position_pct DESC;
```

**What to focus on:** This is the core table. Every active holding needs to go through the four-dimension evaluation in SOP §5. Check distance to each stop-loss level. Sum `position_pct` across all rows to verify total position compliance with SOP §7.1.

### 3.8 advisor_runs (AI 交易建议)

Not prefixed with `invest_`, but the report's AI-recommendations section reads it. Stores one row per `(trade_date, stock_code, recommendation_type)` produced by `stockhot.advisor` (the `daily` command iterates active holdings then the watchlist).

**Key columns:** `trade_date`, `stock_code`, `recommendation_type` (`build` / `adjust` / `clear` / `t_trade` / `none`), `action`, `confidence`, `reasoning_json`, `model_name`, `created_at`

**Example query:**

```sql
SELECT stock_code, recommendation_type, action, confidence, reasoning_json
FROM advisor_runs
WHERE trade_date = date('now', 'localtime')
ORDER BY recommendation_type, stock_code;
```

**What to focus on:** The report's "AI 综合建议" section groups rows by `recommendation_type` into four subtables (建仓 / 调仓 / 清仓 / 做T). `advisor_runs` is written by `advisor daily`, which runs at 08:15 via `run_daily_advisor.py` before the report is generated. If `advisor daily` failed, this section will be empty — flag as "数据不可用" rather than fabricating.

## 4. Execution Flow

Two distinct workflows run at different times.

### Workflow A: Pre-market Research Report (21:30, after data collection)

This runs after the evening data collection scripts finish. It produces the main pre-market report.

**Steps:**

1. **Query all collected data.** Pull from `invest_overseas_market`, `invest_domestic_events`, `invest_supply_chain`, and `invest_futures_sentiment` for today's date. See Section 3 queries above.

2. **Query active holdings** from `invest_holdings` where `status = 'active'`.

3. **Query cycle assessments** from `invest_cycle_assessments` for sector context.

4. **Evaluate each holding** using the four-dimension framework from SOP §5:
   - **Logic status** (✅完好 / ⚠️动摇 / ❌破坏): Has the original investment thesis changed? Check supply chain data and domestic events for the holding's sector.
   - **Event impact** (🟢无 / 🟡轻微 / 🟠中度 / 🔴严重): Are there events in `invest_domestic_events` affecting this holding or its sector?
   - **Technical status** (强势/震荡/弱势/关键位): Where is the price relative to stop-loss levels in `invest_holdings`? Is `current_price` above or below entry?
   - **Cycle position**: Look up the holding's sector in `invest_cycle_assessments`. Map to operation tendency per SOP §5.1 维度四.

5. **Apply the decision matrix** from SOP §5.2:
   - First apply **Matrix A** (logic + event) to determine base operation.
   - Then apply **Matrix B** (technical + cycle) to adjust up or down.

6. **Fill the report template** at `stockhot/invest_sop/templates/report_template.md`. Replace all `{...}` placeholders with queried data and analysis results.

7. **Save the report** to `stockhot/invest_sop/reports/{YYYY-MM-DD}_pre_market.md`.

### Workflow B: Morning Directive (09:00, after morning data confirmation)

This runs after morning data collection. It produces a short, focused directive.

**Steps:**

1. **Read yesterday's pre-market report** from `stockhot/invest_sop/reports/{yesterday}_pre_market.md`.

2. **Query morning confirmation data** from `invest_morning_data` for today.

3. **Compare overnight vs morning.** Has A50 direction reversed? Has USD/CNY moved significantly? Any overnight events not captured in the evening report?

4. **Generate a simplified directive** with a comparison table and an operation table. Format (matches `generate_directive.py` actual output):
   ```markdown
   # 盘前指令 | {date}

   ## 早盘数据对比
   | 指标 | 昨夜 | 今早 | 变化 |
   |------|------|------|------|
   | A50 | {X%} | {Y%} | {Δ} |
   | 日经 | ... | ... | ... |
   | USD/CNY | ... | ... | ... |

   ## 操作指令表
   | 标的 | 昨夜预案 | 早上修正 | 最终操作 | 价格触发条件 |
   |------|----------|----------|----------|--------------|
   | {code} {name} | {昨夜操作} | {维持/上调/下调} | {最终决策} | {价位} |
   ```

5. **Save** to `stockhot/invest_sop/reports/{YYYY-MM-DD}_directive.md`.

## 5. Decision Boundaries (CRITICAL)

Read this section carefully.

**The agent GENERATES ANALYSIS REPORTS only.**

The agent does NOT:
- Make buy or sell decisions
- Execute trades
- Modify the database (all queries are read-only SELECT statements)
- Recommend specific buy/sell actions as instructions

All buy/sell decisions are made by the human user. Reports present data, apply the SOP decision matrix, and state the matrix output. The user reads the report and decides whether to follow the analysis.

Reports should phrase operations as "matrix result" or "analysis suggests" rather than direct commands. For example:
- Write: "决策矩阵结果：减仓30%" (decision matrix result: reduce 30%)
- Do NOT write: "建议买入" or "应该加仓"

## 6. Error Handling

Handle missing data gracefully. Never fabricate numbers.

| Condition | Response |
|-----------|----------|
| `invest_overseas_market` empty for today | Mark §1.1 海外市场 as "数据不可用" in the report |
| `invest_domestic_events` empty for today | Mark §1.3 as "无重大事件记录" |
| `invest_supply_chain` empty for today | Mark §2.3 产业链 section as "数据不可用" |
| `invest_futures_sentiment` empty for today | Mark §1 期货部分 as "数据不可用" |
| `invest_morning_data` empty for today | Morning directive cannot be generated. Log warning. |
| `invest_cycle_assessments` empty | Note: "请先运行 weekly_cycle.py 更新周期评估" |
| `invest_holdings` has no active rows | Report shows "当前无持仓" in §3 |
| Database file does not exist | Stop. Report that migration is needed. Do not create the database. |

**Absolute rules:**
- NEVER fabricate data or make up numbers
- NEVER estimate or interpolate missing data points
- NEVER proceed with a report if core tables (holdings, overseas market) are completely empty
- If a single column is NULL in an otherwise populated row, show "N/A" in the report

## 7. Key SOP Section References

When evaluating data or filling the report, the agent should consult these specific sections of the SOP document at `.sisyphus/drafts/a-share-pre-market-sop.md`:

| SOP Section | Title | When to Reference |
|-------------|-------|-------------------|
| §2.1 | 海外宏观扫描 | Grading overseas market events (🟢🟡🟠🔴 trigger conditions) |
| §2.2 | 国内政策与事件扫描 | Evaluating domestic event impact on holdings |
| §2.3 | 产业链动态追踪 | Interpreting supply chain metrics by sector (tech, new energy, cyclicals) |
| §2.4 | 期货与市场情绪检查 | Interpreting basis signals, northbound flow patterns, margin data |
| §3 | 行业周期分析框架 | Understanding cycle position definitions (复苏/繁荣/衰退) |
| §3.5.2 | 拥挤度打分卡 | Interpreting crowding_score (0-12 scale, 4-tier reading) |
| §3.6 | 周期位置速查表 | Mapping sectors to cycle positions and investment advice |
| §5 | 持仓管理决策矩阵 | The core four-dimension evaluation and two-matrix decision logic |
| §5.1 | 持仓评估四维度 | Defining logic status, event impact, technical status, cycle position |
| §5.2 | 操作决策矩阵 | Matrix A (logic + event) and Matrix B (technical + cycle adjustments) |
| §5.3 | 减仓/清仓执行标准 | Position sizing for reductions, timing requirements |
| §7.1 | 仓位管理规则 | Position limits: single stock 25%, single sector 40%, total range by market |
| §7.2 | 止损体系 | Three-layer stop-loss: logic, technical (-12% hard stop), time-based |
| §7.3 | 组合风险管理 | Portfolio drawdown limits, correlation checks, daily risk checklist |
| §8.1 | 日报告模板 | Output format reference (the template to fill) |
| 附录B | 事件分级标准 | Event classification reference (default severity by event type) |
| 附录D | SOP执行时间表 | Timing reference for when each step should run |

## 8. Report Output Format

The static template at `stockhot/invest_sop/templates/report_template.md` (SOP §8.1) defines seven sections. The actual generator (`generate_premarket_report.py`) emits **nine** sections by inserting two live-data sections between §3 and §4. The generator's output is the source of truth.

1. **市场环境评估** (Market Environment): Filled from `invest_overseas_market`, `invest_domestic_events`, `invest_futures_sentiment`
2. **板块周期评估** (Sector Cycle): Filled from `invest_cycle_assessments`
3. **持仓标的操作决策** (Holding Decisions): Per-holding scaffold table from `invest_holdings`; stop-loss/target filled, four-dimension + Matrix A/B cells left as placeholder for the agent to fill per `references/decision-matrix.md`
4. **持仓监控（卖出时机）** (Holdings Monitor): Computed live by `stockhot.sell_monitor.build_section_holdings_monitor`, runs 3 sell signals (hard_stop, target_reached, thesis_broken) per holding. Wrapped in `<!-- SELL_SIGNALS_START -->` / `<!-- SELL_SIGNALS_END -->` sentinels.
5. **AI 综合建议** (AI Recommendations): Computed live by `stockhot.advisor.report_integration.build_advisor_section`, reads `advisor_runs` grouped by `recommendation_type` into four subtables (建仓 / 调仓 / 清仓 / 做T). Wrapped in `<!-- ADVISOR_SECTION_START -->` / `<!-- ADVISOR_SECTION_END -->` sentinels.
6. **新增标的备选** (New Candidates): Placeholder — only filled if sectors in recovery with crowding below 7
7. **今日重点关注** (Today's Focus): Placeholder — key price levels, scheduled events, threshold triggers
8. **风控检查** (Risk Control): Placeholder — position limits compliance check per SOP §7
9. **昨日复盘** (Yesterday Review): Placeholder — brief comparison of yesterday's plan vs actual

Sections 1, 2, 4, 5 are **live** (filled from DB at generation time). Section 3 is **half-live** (price rows filled, analysis rows placeholder). Sections 6–9 are **scaffold-only** and filled by the agent/human per the SOP.

Note: the static `report_template.md` and the generator's `build_section_*` functions have drifted (e.g., template includes 日经225, generator does not). See `references/data-flow.md` §6.

**Naming convention:**

| Report Type | Filename |
|-------------|----------|
| Pre-market report | `{YYYY-MM-DD}_pre_market.md` |
| Morning directive | `{YYYY-MM-DD}_directive.md` |

**Save location:** `stockhot/invest_sop/reports/`

## 9. Example Usage

### Generate pre-market report (Workflow A)

```bash
PYTHONPATH=/home/leo/Projects/CodeAgentDashboard python3 stockhot/invest_sop/scripts/generate_premarket_report.py --date 2026-06-22
```

Add `--template-only` for an unfilled scaffold (all sections emit placeholders, no DB reads).

### Generate morning directive (Workflow B)

Run **after** `morning_confirm.py` has populated `invest_morning_data` (cron 08:30):

```bash
PYTHONPATH=/home/leo/Projects/CodeAgentDashboard python3 stockhot/invest_sop/scripts/generate_directive.py --date 2026-06-22
```

### Daily orchestration (advisor + report in one shot)

This is what cron runs at 08:15. It first executes `advisor daily` (writes `advisor_runs`), then unconditionally generates the pre-market report:

```bash
PYTHONPATH=/home/leo/Projects/CodeAgentDashboard python3 stockhot/invest_sop/scripts/run_daily_advisor.py --date 2026-06-22
```

### Check data availability

```bash
sqlite3 stockhot/storage/database/stockhot.db "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'invest_%';"
```

Expected output: 9 table names. If fewer, run `migrate.py`.

## 10. Data Collection Note

This skill assumes data has already been collected. The agent does not call AKShare endpoints directly. Data collection is handled by separate scripts documented in `stockhot/invest_sop/AKSHARE_ENDPOINTS.md`.

Known data gaps (as of the endpoint verification):
- **Lithium carbonate prices**: Not available in AKShare. Requires external source.
- **PV/solar module prices**: Not available in AKShare. Requires external source.
- **US VIX**: Not available in AKShare. China VIX (50ETF QVIX) is available via `index_option_50etf_qvix()`.

If these gaps affect the report, note them explicitly rather than leaving sections blank without explanation.
