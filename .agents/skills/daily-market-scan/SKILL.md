---
name: daily-market-scan
description: Use this skill when running the daily A-share market scan (日常盘面扫描). The skill orchestrates 4 stockhot modules that collect涨停, 龙虎榜, 资金流, and 风险提示 data. Trigger phrases include '盘面扫描', '涨停', '龙虎榜', '资金流', '风险提示', '每日复盘', 'daily market scan'.
---

# Daily Market Scan Orchestration

This skill wraps four existing `stockhot` modules into a single, repeatable daily scan. The agent does not implement any analysis logic. It calls the modules in the correct order, isolates failures so one broken data source does not crash the whole run, and hands the collected data off to downstream skills or the database.

The four modules are production code in `stockhot/limit_up`, `stockhot/dragon_tiger`, `stockhot/fund_flow`, and `stockhot/risk_alert`. This skill is an orchestration wrapper. It references these modules, never modifies their source.

## 1. Overview

### What this skill does

The agent runs all four stockhot hot-topic modules for a given trade date, persists their outputs to SQLite, and returns a consolidated data package. No report generation happens here. This is the data collection layer.

```
                    Daily Market Scan
                          |
         +----------------+----------------+
         |                |                |
     涨停分析          龙虎榜            资金流
     limit_up        dragon_tiger       fund_flow
         |                |                |
         +----------------+----------------+
                          |
                      风险提示
                     risk_alert
                   (reads upstream)
```

### When to use this skill

Use when any of these apply:

- The user asks for a daily market scan, 每日复盘, or hot-topic data collection
- A downstream skill (like `invest-sop-pre-market`) needs today's market data and it is not yet in the database
- The user wants to check 涨停, 龙虎榜, 资金流, or 风险提示 for a specific date

### When NOT to use this skill

| Condition | Why not | What to use instead |
|-----------|---------|---------------------|
| User wants a valuation report for a single stock | This skill collects market-wide data, not company valuations | `valuation-loss-making-targets` skill |
| User wants a pre-market report with holdings analysis | This skill is data collection only, no report generation | `invest-sop-pre-market` skill |
| User wants to modify how a module works | This skill never edits module source | Edit the module directly in `stockhot/` |
| Non-trading day or market closed | Modules will return `no_data` status | Skip the scan, inform the user |

## 2. Prerequisites

Before running this skill, verify the following:

1. **AKShare is installed and reachable.** All four modules call AKShare endpoints through `safe_akshare_call`. Verify:
   ```bash
   python -c "import akshare; print('AKShare OK')"
   ```

2. **SQLite database is initialized.** The modules persist data via `stockhot.storage.database.save_daily_data` and `save_analysis_result`. The database lives at `stockhot/storage/database/stockhot.db`.

3. **Trade date is confirmed.** The agent needs a date string in `YYYY-MM-DD` format. If the user does not specify, default to today. On non-trading days, modules return `{"status": "no_data"}`.

4. **Rate limiter is active.** AKShare calls go through `stockhot.core.rate_limiter.safe_akshare_call`; Tushare calls go through `stockhot.core.tushare_client_safe.safe_tushare_call`. Both throttle requests. Large date ranges may take time.

5. **Python 3.12 or later** is active in the project environment.

6. **数据源策略（2026-07-07 调整）**：所有模块以 **Tushare 为第一数据源，AKShare 为 fallback**。详见 `.agents/skills/data-source-convention.md`。具体映射：
   - limit_up：`limit_list_d`（U/D/Z 一接口三用）→ AKShare `stock_zt_pool_*_em` 兜底
   - dragon_tiger：`top_list` + `top_inst` → AKShare `stock_lhb_*_em` 兜底；**活跃营业部 `stock_lhb_hyyyb_em` 保留 AKShare only（Tushare 无等价）**
   - fund_flow：`moneyflow_mkt_dc`（大盘）/ `moneyflow`（个股）→ AKShare 兜底
   - risk_alert：ST 股票用 `stock_basic` 过滤；**停牌池 `stock_zh_a_stop_em` 保留 AKShare only（Tushare 无等价）**
   - index_technical：`index_daily` → AKShare `stock_zh_index_daily` 兜底

## 3. The Four Modules

This skill wraps four modules. Each module has a `run_*_analysis(date)` entry point that fetches data, analyzes it, persists to the database, and returns a dict. The agent calls these entry points. It does not call internal helper functions.

### Module A: 涨停分析 (limit_up)

**Entry point:** `stockhot.limit_up.run_limit_up_analysis(date)`

**What it does:** Identifies the daily limit-up landscape. Pulls three pools from AKShare (涨停池, 炸板池, 跌停池), then computes consecutive-board rankings (连板梯队), sector correlation (板块联动), and seal-strength rankings (封单强度).

**Output keys:** `limit_up_pool`, `broken_pool`, `limit_down_pool`, `consecutive_boards`, `sector_correlation`, `seal_strength_ranking`, `summary`

**Role in scan:** Foundational upstream module. Writes `limit_up_pool` to the database, which `risk_alert` later reads to detect high-position risks (高位连板). Must run first.

**Note:** ST stocks and 科创板 (688xxx) are excluded by the underlying 东方财富 API.

### Module B: 龙虎榜分析 (dragon_tiger)

**Entry point:** `stockhot.dragon_tiger.run_dragon_tiger_analysis(date)`

**What it does:** Fetches the dragon-tiger list detail, institutional trading stats, and active broker offices. Cross-references brokers with their targets for hot-money tracking.

**Output keys:** `detail`, `institutional`, `brokers`, `hot_money`, `summary`

**Role in scan:** Depends on limit_up as its logical upstream. Both modules describe the same hot stocks from different angles (price action vs seat-level flows). Writes `dragon_tiger_detail` to the database, which `risk_alert` reads to detect abnormal volatility (异常波动).

### Module C: 资金流向 (fund_flow)

**Entry point:** `stockhot.fund_flow.run_fund_flow_analysis(date)`

**What it does:** Fetches market-wide and sector-level fund flow rankings. Analyzes multi-day trends including direction (持续流入 / 持续流出 / 震荡), momentum (加速 / 减速 / 稳定), and large-vs-retail divergence.

**Output keys:** `market_flow`, `sector_flow`, `trend`, `summary`

**Role in scan:** Independent of limit_up and dragon_tiger. Writes `fund_flow_market` and `fund_flow_sector` to the database. The sector data feeds `risk_alert`'s capital-flight detection (资金出逃).

### Module D: 风险提示 (risk_alert)

**Entry point:** `stockhot.risk_alert.run_risk_alert_analysis(date)`

**What it does:** Aggregates risk signals from multiple dimensions. Fetches ST and suspended stocks directly from AKShare, then reads three upstream datasets from the database: dragon_tiger_detail (for abnormal volatility), fund_flow_sector (for capital flight), and limit_up_pool (for high-position risks).

**Output keys:** `st_stocks`, `suspended_stocks`, `abnormal_volatility`, `capital_flight`, `high_position_risks`, `summary`

**Role in scan:** Terminal module. Reads from all three upstream modules via the database. Must run last, after limit_up, dragon_tiger, and fund_flow have written their data.

## 4. Execution Order (Orchestration)

The four modules run in three waves. The order is fixed because `risk_alert` reads upstream outputs from the database. Running modules out of order produces empty risk detections.

```
Wave 1          Wave 2 (parallel)              Wave 3
+---------+     +-------------+               +------------+
| limit_up| --> | dragon_tiger| ----------+--> | risk_alert |
+---------+     +-------------+           |    +------------+
                +-------------+           |
                | fund_flow   | ----------+
                +-------------+
```

### 执行顺序

| Wave | Module(s) | Why this order |
|:----:|-----------|----------------|
| 1 | `limit_up` | Foundational upstream. Writes `limit_up_pool` to DB. All later modules that need limit-up context depend on this. |
| 2 | `dragon_tiger` + `fund_flow` + `index_technical` (parallel) | 三者互不依赖。dragon_tiger 是 limit_up 的逻辑下游（同一批热点股的席位层面）；fund_flow 独立；`index_technical` 独立（采集指数 OHLCV + 6 阶段趋势识别，不依赖任何上游）。三者都写 DB 供 risk_alert / 盘后总结 / 盘前报告消费。 |
| 3 | `risk_alert` | Terminal module. Reads `limit_up_pool`, `dragon_tiger_detail`, and `fund_flow_sector` from DB via `get_daily_data(date)`. Must run after all three upstream modules. |

> **index_technical 说明**：第 5 个模块，2026-07-06 新增。对上证/深证/创业板/科创50 做技术面分析（MA/MACD/RSI/KDJ/布林 + 6 阶段趋势识别：主升/上涨中回调/高位震荡筑顶/主跌/下跌中反弹/低位筑底），输出每阶段的盘前预期行为（避免梭哈）。与 fund_flow 平级，独立无依赖，失败不影响其他模块。详见 `stockhot/index_technical/`。

### Orchestration code pattern

```python
from stockhot.limit_up import run_limit_up_analysis
from stockhot.dragon_tiger import run_dragon_tiger_analysis
from stockhot.fund_flow import run_fund_flow_analysis
from stockhot.risk_alert import run_risk_alert_analysis
from stockhot.index_technical import run_index_technical_analysis

results = {}

# Wave 1: limit_up first (foundational upstream)
try:
    results["limit_up"] = run_limit_up_analysis(date)
except Exception as e:
    results["limit_up"] = {"date": date, "status": "数据不可用", "error": str(e)}

# Wave 2: dragon_tiger + fund_flow + index_technical in parallel
try:
    results["dragon_tiger"] = run_dragon_tiger_analysis(date)
except Exception as e:
    results["dragon_tiger"] = {"date": date, "status": "数据不可用", "error": str(e)}

try:
    results["fund_flow"] = run_fund_flow_analysis(date)
except Exception as e:
    results["fund_flow"] = {"date": date, "status": "数据不可用", "error": str(e)}

try:
    results["index_technical"] = run_index_technical_analysis(date)
    # 持久化到 daily_data 表，供盘后总结/盘前报告读取
    from stockhot.storage.database import save_daily_data
    save_daily_data({"date": date, "index_technical": results["index_technical"]})
except Exception as e:
    results["index_technical"] = {"date": date, "status": "数据不可用", "error": str(e)}

# Wave 3: risk_alert last (reads all upstream from DB)
try:
    results["risk_alert"] = run_risk_alert_analysis(date)
except Exception as e:
    results["risk_alert"] = {"date": date, "status": "数据不可用", "error": str(e)}
```

The `try/except` wrappers are essential. They guarantee that a failure in one module does not crash the others. See Section 5.

## 5. Independent Failure Strategy (隔离)

Each module is wrapped in its own `try/except` block. This is the core isolation rule.

### Why isolation matters

The four modules hit different AKShare endpoints. Any single endpoint can fail due to rate limits, network issues, or API changes. If one module crashes, the other three should still complete and persist their data.

### Failure handling rules

1. **Wrap every `run_*_analysis(date)` call in its own `try/except`.** Never chain calls without isolation.

2. **On failure, record a `数据不可用` status.** Do not crash. The failed module's entry in the results dict gets:
   ```python
   {"date": date, "status": "数据不可用", "error": str(e)}
   ```

3. **Continue to the next module regardless.** A limit_up failure should not stop dragon_tiger or fund_flow.

4. **risk_alert degrades gracefully.** Because it reads from the database via `get_daily_data(date)`, any upstream module that failed will simply contribute an empty list. The `or []` fallbacks in `run_risk_alert_analysis` handle this. risk_alert still produces a valid result with partial data.

5. **Report what succeeded and what failed.** After the scan, the agent should summarize which modules returned `success`, which returned `no_data` (non-trading day), and which returned `数据不可用` (error).

### Failure matrix

| Failed module | Impact on other modules | Impact on risk_alert |
|----------------|------------------------|----------------------|
| limit_up | None. dragon_tiger and fund_flow run independently. | `limit_up_pool` is empty. High-position risk detection produces no results. Other detectors still work. |
| dragon_tiger | None. | `dragon_tiger_detail` is empty. Abnormal volatility detection produces no results. Other detectors still work. |
| fund_flow | None. | `fund_flow_sector` is empty. Capital flight detection produces no results. Other detectors still work. |
| index_technical | None. 独立模块，不依赖任何上游。 | `index_technical` 数据缺失。盘后总结的"大盘技术面"章节、盘前报告的 §1.4/§1.5 技术面预期标"数据不可用"。其他模块不受影响。 |
| risk_alert | None. Waves 1 and 2 already completed and persisted. | risk_alert result is `数据不可用`. Upstream data is still in the database for later use. |

## 6. Responsibility Boundary

### This skill vs invest-sop-pre-market

These two skills are complementary but have a strict boundary.

| Dimension | daily-market-scan (this skill) | invest-sop-pre-market |
|-----------|-------------------------------|----------------------|
| Layer | Data collection | Report generation |
| Input | A trade date | SQLite data (collected by this skill and other scripts) |
| Output | Raw data persisted to DB, consolidated results dict | Structured markdown reports for holdings |
| Decision logic | None. Pure data collection and statistical summary. | SOP decision matrix applied to holdings. |
| AI analysis | None. Summaries are pure statistics. | Reads SOP doc, applies evaluation framework. |
| Report writing | None. | Writes two daily markdown reports. |

**The boundary rule:** `daily-market-scan` collects data. `invest-sop-pre-market` reads that data and generates reports. This skill never writes reports. `invest-sop-pre-market` never calls AKShare directly.

If a user asks for a market report, use `invest-sop-pre-market`. If the underlying data is missing from the database, run this skill first to populate it, then hand off to `invest-sop-pre-market`.

### What this skill does NOT do

- Does not generate any report or markdown file
- Does not apply any decision matrix or trading logic
- Does not call AI/LLM for analysis (all summaries are pure statistics from the modules)
- Does not execute trades or issue trading instructions
- Does not modify the four stockhot module source files

## 7. Limitations

### Fixed defaults, no configuration

This skill does not expose scan parameters. All thresholds are fixed in the module source code:

| Parameter | Fixed value | Location |
|-----------|:-----------:|----------|
| High-position board threshold | 3 | `stockhot/risk_alert/HIGH_POSITION_THRESHOLD` |
| Fund flow trend lookback | 5 days | `stockhot/fund_flow/analyze_fund_flow_trend(lookback=5)` |
| Volatility reason keywords | 5 fixed strings | `stockhot/risk_alert/VOLATILITY_REASONS` |
| ST exclusion | Hardcoded in 东方财富 API | `stockhot/limit_up/` (cannot override) |
| Sector fund flow indicator | "今日" | `stockhot/fund_flow/fetch_sector_fund_flow(indicator="今日")` |

If a user needs different thresholds, they must modify the module source. This skill will not add configuration layers on top.

### Data source constraints

- **30-day lookback limit:** The 炸板池 and 跌停池 endpoints (`stock_zt_pool_zbgc_em`, `stock_zt_pool_dtgc_em`) only return data for the last 30 days. Historical scans beyond 30 days are not possible for these pools.
- **ST and 科创板 exclusion:** The limit-up API excludes ST stocks and STAR Market (688xxx) tickers. These will not appear in any limit-up output.
- **Non-trading days:** All modules return `{"status": "no_data"}` on weekends and holidays. The scan produces empty results but does not error.
- **Rate limiting:** AKShare calls are throttled by `safe_akshare_call`. A full scan for one date typically completes in 10 to 30 seconds depending on API load.

### No retry logic

This skill wraps modules in `try/except` but does not retry failed calls. If an AKShare endpoint is temporarily unavailable, the module returns `数据不可用`. The agent should inform the user and suggest re-running the scan later.

## 8. Companion Files

This skill currently has one companion file:

| File | Type | Purpose |
|------|------|---------|
| `README.zh-CN.md` | Overview | Chinese overview of the skill, module list, and quick-start guide |

Additional companions (checklists, reference schemas) may be added in future tasks. This SKILL.md is self-contained for orchestration purposes. If a companion file does not exist, follow the methodology described here directly.

### Relationship to other skills

| Related skill | Relationship |
|---------------|--------------|
| `invest-sop-pre-market` | Downstream consumer. Reads the data this skill collects and generates pre-market reports. |
| `valuation-loss-making-targets` | Unrelated. That skill values individual loss-making targets using a different methodology. |
| `local-development-environment` | Environment setup. Run this first if AKShare import fails or the database is missing. |
