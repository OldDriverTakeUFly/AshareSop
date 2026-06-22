# Data Flow Reference

This document supplements `SKILL.md` §3 (Data Sources) and §4 (Execution Flow). It gives the complete table inventory, the end-to-end data flow with cron timing, and the report's actual 9-section output mapping.

## 1. Complete Table Inventory

The pre-market report reads from a subset of these tables. The schema defines **9 tables prefixed with `invest_`** plus `advisor_runs` (no `invest_` prefix but read by the report).

| Table | Writer | Reader | Report reads? |
|-------|--------|--------|:-------------:|
| `invest_overseas_market` | `overseas_market_data.py` (cron 20:00) | `generate_premarket_report.py` `_fetch_overseas` | ✅ §1.1 |
| `invest_domestic_events` | `domestic_events.py` (cron 20:30) | `generate_premarket_report.py` `_fetch_events` | ✅ §1.2 |
| `invest_supply_chain` | `supply_chain.py` (cron 21:00) | `weekly_cycle.py` `fetch_supply_chain_last_n` | ❌ |
| `invest_futures_sentiment` | `futures_sentiment.py` (cron 21:30) | `generate_premarket_report.py` `_fetch_futures` | ✅ §1.1 股指期货行 |
| `invest_morning_data` | `morning_confirm.py` (cron 08:30) | `generate_directive.py` | ❌ 不进盘前报告（见 §3 timing） |
| `invest_cycle_assessments` | `weekly_cycle.py --update-sector` (weekly Sun 10:00) | `generate_premarket_report.py` `_fetch_cycle_assessments` | ✅ §2 |
| `invest_holdings` | `update_holdings.py` (after close) / `holdings_cli.py` (manual) | `generate_premarket_report.py` `_fetch_active_holdings` + `sell_monitor` + `advisor` | ✅ §3 + 持仓监控 |
| `invest_holdings_transactions` | `update_holdings.py` | — | ❌ |
| `invest_sector_rules` | `init_database()` seeds 13 default rows | `config.get_sector_rule()` (used by `update_holdings.py`) | ❌（间接） |
| `invest_watchlist` | `watchlist_cli.py` / `advisor watchlist` | `advisor daily` `cmd_daily` | ❌ |
| `advisor_runs` | `advisor daily` → `persist_recommendation` | `report_integration._fetch_recommendations` | ✅ AI 综合建议 |

Sources: schema in `stockhot/storage/database.py:21-273`; upsert/query allowlist in `stockhot/invest_sop/utils/db_helpers.py:8-20`.

**Note on SKILL.md §3**: it says "7 tables prefixed with `invest_`". The schema actually defines 9 `invest_*` tables. SKILL.md §3 only documents the 7 the report historically read; the additional 3 (`invest_holdings_transactions`, `invest_sector_rules`, `invest_watchlist`) and `advisor_runs` are listed above for completeness.

## 2. End-to-End Data Flow

```
[D-1 evening]                                                  [D morning]
                                                              
20:00  overseas_market_data.py  → invest_overseas_market ─┐    
20:30  domestic_events.py      → invest_domestic_events ──┤    
21:00  supply_chain.py         → invest_supply_chain      │   (read by weekly_cycle only)
21:30  futures_sentiment.py    → invest_futures_sentiment ┤    
                                                          │    
[weekly Sun 10:00] weekly_cycle.py → invest_cycle_assessments ─┤    
[after market close] update_holdings.py → invest_holdings ─────┤    
                                                               │    
                                                               ▼
                                          [D 08:15]  run_daily_advisor.py
                                                      │
                                                      ├─ advisor daily  ──→ advisor_runs
                                                      │   (iterates invest_holdings
                                                      │    + invest_watchlist)
                                                      │
                                                      └─ generate_premarket_report.py
                                                          ├─ reads invest_overseas_market
                                                          ├─ reads invest_domestic_events
                                                          ├─ reads invest_futures_sentiment
                                                          ├─ reads invest_cycle_assessments
                                                          ├─ reads invest_holdings
                                                          ├─ build_section_holdings_monitor (sell signals, live)
                                                          └─ build_advisor_section (reads advisor_runs, live)
                                                                      ↓
                                          storage/files/reports/invest_sop/{D}_pre_market.md
                                                               │
                                          [D 08:30]  morning_confirm.py → invest_morning_data
                                                               │
                                          [D 09:00+]  generate_directive.py
                                                      ├─ reads invest_overseas_market
                                                      ├─ reads invest_morning_data
                                                      └─ reads invest_holdings
                                                                      ↓
                                              storage/files/reports/invest_sop/{D}_directive.md
```

Source: `stockhot/invest_sop/crontab.txt`; orchestration in `run_daily_advisor.py:32-43`; section assembly in `generate_premarket_report.py:262-291`.

## 3. Cron Timeline

Sorted by clock time. All commands run with `PYTHONPATH=PROJECT_ROOT` from `stockhot/invest_sop/`. Non-trading days are skipped by each script's `is_trading_day()` guard.

| Time | Script | Writes | Read by report? |
|------|--------|--------|:---:|
| 08:15 Mon–Fri | `run_daily_advisor.py` | runs `advisor daily` → `advisor_runs`; then `generate_premarket_report.py` → `{date}_pre_market.md` | yes (this is the report generator) |
| 08:30 Mon–Fri | `morning_confirm.py` | `invest_morning_data` | ❌ not by 08:15 report |
| 10:00 Sun | `weekly_cycle.py` | `invest_cycle_assessments` (manual update mode); writes `{date}_cycle_review.md` | ✅ §2 |
| 20:00 daily | `overseas_market_data.py` | `invest_overseas_market` | ✅ §1.1 |
| 20:30 daily | `domestic_events.py` | `invest_domestic_events` | ✅ §1.2 |
| 21:00 daily | `supply_chain.py` | `invest_supply_chain` | ❌ |
| 21:30 daily | `futures_sentiment.py` | `invest_futures_sentiment` | ✅ §1.1 股指期货行 |

`update_holdings.py` is **not in this crontab** — its docstring says "Run daily after market close (e.g., 16:00)". It must be scheduled separately so `current_price` / `position_pct` are fresh for the next morning's report.

## 4. Critical Timing Observations

1. **Evening collection (20:00–21:30) precedes the morning report (08:15).** The report for date D consumes overseas/domestic/futures data collected the prior evening. Because these scripts self-guard with `is_trading_day()`, the data persisted is effectively "last trading day's" data.

2. **`invest_morning_data` is NOT read by the 08:15 report.** `morning_confirm.py` runs at 08:30 — 15 minutes *after* `generate_premarket_report.py`. The morning data (Nikkei / KOSPI / A50 early move) feeds the 09:00 directive (`generate_directive.py`), not the pre-market report.

3. **`invest_cycle_assessments` updates weekly.** `weekly_cycle.py --update-sector` runs Sunday 10:00; sector cycle positions change slowly. That is why `_fetch_cycle_assessments()` reads the latest assessment with no date filter.

4. **`invest_holdings` must be refreshed after close.** `update_holdings.py` is the only thing keeping `current_price`, `position_pct`, `stop_loss_hard`, `target_price` fresh. If it doesn't run, the report will show stale prices.

5. **`run_daily_advisor.py` is a 2-step wrapper.** It first runs `advisor daily --date {date}` (writes `advisor_runs`), then unconditionally runs `generate_premarket_report.py`. If the advisor step partially fails, the report still generates.

## 5. Report Output: Actual 9 Sections

The generator emits **9 sections**, not the 7 in `templates/report_template.md`. Two extra live-data sections are inserted between §3 and §4:

| # | Section | Source | Type |
|---|---------|--------|------|
| 1 | 一、市场环境评估 | `invest_overseas_market` + `invest_domestic_events` + `invest_futures_sentiment` | **live** (4 subsections: 海外市场 / 重大事件 / 国内政策占位 / 综合判断占位) |
| 2 | 二、板块周期评估 | `invest_cycle_assessments` | **live** (9 fixed sectors) |
| 3 | 三、持仓标的操作决策 | `invest_holdings` | **half-live** (per-holding scaffold table with stop-loss/target filled; four-dimension cells left as placeholder) |
| — | 持仓监控（卖出时机） | `sell_monitor.build_section_holdings_monitor`, wrapped in `<!-- SELL_SIGNALS_START/END -->` | **live** (3 sell signals per holding: hard_stop, target_reached, thesis_broken) |
| — | AI 综合建议（{date}） | `advisor.report_integration.build_advisor_section`, wrapped in `<!-- ADVISOR_SECTION_START/END -->` | **live** (4 subtables: 建仓 / 调仓 / 清仓 / 做T, from `advisor_runs`) |
| 4 | 四、新增标的备选 | — | **placeholder** ("（暂无备选标的）") |
| 5 | 五、今日重点关注 | — | **placeholder** (3 empty tables) |
| 6 | 六、风控检查 | — | **placeholder** (5 empty rows) |
| 7 | 七、昨日复盘（简要） | — | **placeholder** |

The four-dimension evaluation (logic / event / technical / cycle) and Matrix A/B application that §3's table alludes to are **not computed by the generator** — they are filled by the agent/human using `references/decision-matrix.md`. Likewise §4–§7 are scaffold-only; their analytical content is a manual step per the SOP.

## 6. `templates/report_template.md` vs. Generator Drift

The static template at `stockhot/invest_sop/templates/report_template.md` (SOP §8.1 verbatim, 134 lines) and the generator's `build_section_*` functions are **two parallel implementations** that have drifted:

- Template §1.1 includes a `日经225早盘：{__%}` line; the generator does not emit 日经.
- Template has 7 sections; generator emits 9 (adds 持仓监控 + AI 综合建议).
- The generator is the **source of truth for actual output**. The template is the SOP's canonical format reference and the basis for `--template-only` mode (which actually still calls the generator's `build_section_*` with `None`/`[]` inputs, not the template file).

Reconciling the template with the generator is out of scope for this skill — it would be a code change to `generate_premarket_report.py`.
