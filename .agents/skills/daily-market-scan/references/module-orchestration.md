# Module Orchestration Reference

This document provides the detailed orchestration dependency diagram, try/except isolation strategy, and cross-module data flow for the daily-market-scan skill. It supplements `SKILL.md` Section 4 (Execution Order) and Section 5 (Independent Failure Strategy).

## 1. Orchestration Dependency Diagram

The four modules run in three waves. The dependency is driven by `risk_alert`'s need to read upstream data from the database. Two additional Wave-2 modules (`index_technical`, `volatility`) extend the scan with technical-trend and volatility/sentiment dimensions; both are independent and do not feed `risk_alert`.

```
                    ┌─────────────────────────────────┐
                    │         Wave 1 (serial)         │
                    │                                 │
                    │   stockhot.limit_up             │
                    │   run_limit_up_analysis(date)   │
                    │   Writes: limit_up_pool → DB    │
                    └──────────────┬──────────────────┘
                                   │
                    ┌──────────────▼──────────────────┐
                    │      Wave 2 (parallel)          │
                    │                                 │
   ┌────────────────┤  Neither reads the other's     ├────────────────┐
   │                │  output. Both write to DB.     │                │
   │                └────────────────────────────────┘                │
   │                                                                  │
   ▼                                                                  ▼
┌──────────────────────┐                          ┌──────────────────────┐
│ stockhot.dragon_tiger│                          │  stockhot.fund_flow  │
│ run_dragon_tiger_    │                          │  run_fund_flow_      │
│ analysis(date)       │                          │  analysis(date)      │
│                      │                          │                      │
│ Writes:              │                          │ Writes:              │
│  dragon_tiger_detail │                          │  fund_flow_market    │
│  → DB                │                          │  fund_flow_sector    │
│                      │                          │   → DB               │
└──────────┬───────────┘                          └──────────┬───────────┘
           │                                                 │
           └──────────────────────┬──────────────────────────┘
                                  │
                   ┌──────────────▼──────────────────┐
                   │         Wave 3 (serial)         │
                   │                                 │
                   │   stockhot.risk_alert           │
                   │   run_risk_alert_analysis(date) │
                   │                                 │
                   │   Reads from DB:                │
                   │    - limit_up_pool              │
                   │    - dragon_tiger_detail        │
                   │    - fund_flow_sector           │
                   │   via get_daily_data(date)      │
                   └─────────────────────────────────┘
```

### Why this order

- **Wave 1 (limit_up):** Foundational. Writes `limit_up_pool` to DB. This data is consumed by risk_alert for high-position risk detection (高位连板). No upstream dependencies.
- **Wave 2 (dragon_tiger + fund_flow + index_technical + volatility):** All four are logically independent of each other. dragon_tiger is the seat-level view of the same hot stocks that limit_up found at the price-action level. fund_flow is completely independent. `index_technical` (大盘技术面 6 阶段趋势) and `volatility` (RV 分位 + iVIX/V/R 中国版 VIX) are both independent OHLCV/option consumers that do NOT feed risk_alert — they write to DB for 盘后总结/盘前报告 to consume directly.
- **Wave 3 (risk_alert):** Terminal module. Reads three DB keys via `get_daily_data(date)`: `limit_up_pool`, `dragon_tiger_detail`, `fund_flow_sector`. Must run last or it will read empty/stale data. Note: risk_alert does NOT read index_technical or volatility.

## 2. Cross-Module Data Flow

The modules communicate through the SQLite database, not through direct function calls. risk_alert is the only module that reads another module's output. index_technical and volatility write their output for direct consumption by downstream skills (盘后总结/盘前报告), bypassing risk_alert entirely.

### DB keys written and read

| DB Key | Written By | Read By | Used For |
|--------|-----------|---------|----------|
| `limit_up_pool` | `limit_up` (`save_daily_data`) | `risk_alert` (`get_daily_data`) | High-position risk detection — `detect_high_position_risks()` flags stocks with `consecutive_boards > 3` |
| `dragon_tiger_detail` | `dragon_tiger` (`save_daily_data`) | `risk_alert` (`get_daily_data`) | Abnormal volatility detection — `detect_abnormal_volatility()` filters entries whose `reason` matches `VOLATILITY_REASONS` keywords |
| `fund_flow_market` | `fund_flow` (`save_daily_data`) | (not read by other modules) | Stored for downstream skills / dashboard |
| `fund_flow_sector` | `fund_flow` (`save_daily_data`) | `risk_alert` (`get_daily_data`) | Capital flight detection — `detect_capital_flight()` flags sectors with `main_net < 0` |

### Data flow in run_risk_alert_analysis

```python
# From stockhot/risk_alert/__init__.py (reference only — do NOT modify)

market_data = get_daily_data(target_date)
lhb_detail = market_data.get("dragon_tiger_detail") or []
sector_fund_flow = market_data.get("fund_flow_sector") or []
limit_up_pool = market_data.get("limit_up_pool") or []

abnormal = detect_abnormal_volatility(lhb_detail)
flight = detect_capital_flight(sector_fund_flow)
high_pos = detect_high_position_risks(limit_up_pool)
```

The `or []` fallbacks ensure that if any upstream module failed (and thus did not write its key to the DB), the corresponding detector receives an empty list and returns empty results. This is the graceful degradation mechanism.

## 3. Try/Except Isolation Strategy

Every `run_*_analysis(date)` call is wrapped in its own `try/except` block. This is mandatory.

### Pattern

```python
results = {}

# Wave 1
try:
    results["limit_up"] = run_limit_up_analysis(date)
except Exception as e:
    results["limit_up"] = {"date": date, "status": "数据不可用", "error": str(e)}

# Wave 2
try:
    results["dragon_tiger"] = run_dragon_tiger_analysis(date)
except Exception as e:
    results["dragon_tiger"] = {"date": date, "status": "数据不可用", "error": str(e)}

try:
    results["fund_flow"] = run_fund_flow_analysis(date)
except Exception as e:
    results["fund_flow"] = {"date": date, "status": "数据不可用", "error": str(e)}

# Wave 3
try:
    results["risk_alert"] = run_risk_alert_analysis(date)
except Exception as e:
    results["risk_alert"] = {"date": date, "status": "数据不可用", "error": str(e)}
```

### Rules

1. **One try/except per module.** Never chain two module calls inside the same try block. A failure in module A must not prevent module B from running.
2. **Catch broad `Exception`.** The goal is isolation, not error recovery. Any exception — whether from AKShare, network, or module logic — should be caught and recorded.
3. **Record `数据不可用` status.** The failed module's entry gets `{"date": date, "status": "数据不可用", "error": str(e)}`. This status is distinct from `no_data` (non-trading day, returned by the module itself).
4. **Never re-raise.** The scan must complete all four modules regardless of individual failures.

### Failure propagation matrix

| Failed Module | DB Keys Missing | risk_alert Impact | Other Modules |
|---------------|----------------|-------------------|---------------|
| limit_up | `limit_up_pool` | High-position detection returns `[]`. Other detectors unaffected. | dragon_tiger, fund_flow still run. |
| dragon_tiger | `dragon_tiger_detail` | Abnormal volatility detection returns `[]`. Other detectors unaffected. | limit_up, fund_flow still run. |
| fund_flow | `fund_flow_market`, `fund_flow_sector` | Capital flight detection returns `[]`. Other detectors unaffected. | limit_up, dragon_tiger still run. |
| risk_alert | (risk_alert writes `risk_alert_raw`) | risk_alert result is `数据不可用`. | Waves 1+2 already completed and persisted. Data remains in DB. |

## 4. Module Entry Points Summary

| Module | Entry Point | Source File | Lines |
|--------|------------|-------------|:-----:|
| limit_up | `stockhot.limit_up.run_limit_up_analysis(date)` | `stockhot/limit_up/__init__.py` | 203 |
| dragon_tiger | `stockhot.dragon_tiger.run_dragon_tiger_analysis(date)` | `stockhot/dragon_tiger/__init__.py` | 262 |
| fund_flow | `stockhot.fund_flow.run_fund_flow_analysis(date)` | `stockhot/fund_flow/__init__.py` | 294 |
| risk_alert | `stockhot.risk_alert.run_risk_alert_analysis(date)` | `stockhot/risk_alert/__init__.py` | 202 |

All entry points return a dict with shape `{date, status, data}` where `status` is one of `success`, `no_data`, or (on isolation failure) `数据不可用`.

## 5. Fixed Thresholds (Not Configurable)

This skill does not expose any scan parameters. The following thresholds are hardcoded in module source:

| Threshold | Value | Location | Used By |
|-----------|:-----:|----------|---------|
| HIGH_POSITION_THRESHOLD | 3 | `stockhot/risk_alert/HIGH_POSITION_THRESHOLD` | `detect_high_position_risks()` |
| Fund flow trend lookback | 5 days | `stockhot/fund_flow/analyze_fund_flow_trend(lookback=5)` | `analyze_fund_flow_trend()` |
| Volatility reason keywords | 5 strings | `stockhot/risk_alert/VOLATILITY_REASONS` | `detect_abnormal_volatility()` |
| Sector fund flow indicator | "今日" | `stockhot/fund_flow/fetch_sector_fund_flow(indicator="今日")` | `fetch_sector_fund_flow()` |
| ST / 科创板 exclusion | Hardcoded in 东方财富 API | `stockhot/limit_up/` | AKShare `stock_zt_pool_em` |

To change a threshold, modify the module source directly. This skill will not add a configuration layer on top.
