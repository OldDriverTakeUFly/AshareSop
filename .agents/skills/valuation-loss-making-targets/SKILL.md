---
name: valuation-loss-making-targets
description: Use this skill when valuing loss-making acquisition targets (亏损标的估值). Trigger phrases include '估值分析', '亏损标的', '困境反转估值', 'DCF for loss-making companies', 'target valuation'.
---

# Loss-Making Target Valuation Methodology

This skill gives agents a repeatable methodology for valuing companies that are currently losing money. Traditional PE-based valuation breaks down entirely when net income is negative, so this skill replaces it with a three-angle triangulation framework that combines quantitative distress scoring, dual-model valuation (PS plus DCF), and global peer anchoring.

The core principle: for a loss-making company, the goal is never to compute a single target price. The goal is to compute a probability-weighted range across three scenarios (pessimistic, neutral, optimistic), with the scenario weights themselves adjusted by objective distress signals.

## 1. Overview

### What this skill does

The agent applies the Triangle Framework to a single loss-making target, produces a structured valuation report, and outputs a probability-weighted target price range. Every number in the report must be traceable to a named source.

### The Triangle Framework

Three independent angles attack the valuation problem from different directions. Conclusions gain confidence when all three converge. Divergence between angles is itself a risk signal.

```
                    Probability-Weighted
                    Target Price Range
                           /\
                          /  \
                         /    \
                        /      \
               ________/        \________
              /                           \
             /                             \
    [Angle A]                        [Angle C]
  Quantitative                    Global Peer
  Distress Engine                   Anchoring
  (davis_analyzer)                (PS band calibration)
             \                             /
              \_______           _________/
                      \         /
                       \       /
                   [Angle B]
                PS + DCF Dual Model
              (relative + intrinsic)
```

- **Angle A (Quantitative Distress Engine):** Uses `davis_analyzer` to score how deep the distress is, whether the balance sheet supports a turnaround, and whether inflection momentum has arrived. Outputs scenario probability adjustments.
- **Angle B (PS plus DCF Dual Model):** Price-to-Sales for relative valuation during the loss period, DCF for intrinsic value once cash flows normalize. Cross-validated to avoid single-method blind spots.
- **Angle C (Global Peer Anchoring):** Compares the target against domestic and international peers to calibrate the reasonable PS band. Catches cases where a sector trades at abnormal multiples.

### When to use this skill

Use when the target meets ALL of these conditions:

- The company has reported net losses for at least two consecutive reporting periods
- PE (TTM) is negative, meaningless, or above 500x
- The company has meaningful revenue (not pre-revenue)
- The losses are cyclical or structural but potentially reversible

### When NOT to use this skill

Do NOT use this skill when any of the following apply:

| Condition | Why not | What to use instead |
|-----------|---------|---------------------|
| Growth-driven losses (burning cash to capture market) | The distress framework misreads deliberate investment as distress | Standard growth/DCF valuation, revenue-multiple with growth adjustments |
| Zero or near-zero revenue | PS is undefined; DCF cash flows cannot be projected | Pre-revenue frameworks, asset-based valuation, or comparable transaction multiples |
| Permanent decline (no turnaround thesis) | The three-angle framework assumes reversal is possible | Liquidation value, breakup value, or distressed-asset valuation |
| Profitable company with normal PE | No need to bypass PE | Standard PE/PB/PEG relative valuation |

## 2. Prerequisites

Before running this skill, verify the following:

1. **`davis_analyzer` package is installed.** Check by running:
   ```bash
   python -c "import davis_analyzer; print('OK')"
   ```
   If missing, install from the repository root:
   ```bash
   pip install -e .
   ```

2. **Tushare API token is configured.** A valid `TUSHARE_TOKEN` must be present in `.env` at the repository root. The token must have sufficient API tier to access financial statements and chip-structure endpoints. Verify:
   ```bash
   python -c "from davis_analyzer.config import get_config; print(get_config().tushare_token[:8] + '...')"
   ```

3. **Target stock code is identified.** The agent needs the `ts_code` for the loss-making target (for example, `688XXX.SH` or `300XXX.SZ`).

4. **At least 3 peer stock codes are identified.** The agent needs `ts_code` values for a minimum of 3 comparable peers. Ideally this includes both domestic listed peers and international listed peers. More peers improve the anchoring quality.

5. **Target is confirmed loss-making.** Pull the latest two reporting periods of net income to confirm negative earnings before proceeding.

6. **Python 3.12 or later** is active in the project environment.

## 3. Triangle Framework

### Angle A: Quantitative Distress Engine

The distress engine (`davis_analyzer.distress`) computes a three-layer score using continuous signals (not binary triggers). Each signal produces a value between 0.0 and 1.0.

**Layer composition:**

| Layer | Name | Signals | Weight |
|-------|------|---------|:------:|
| L1 | Distress Confirmation | eps_decline, pe_pb_percentile, financial_health | 0.30 |
| L2 | Reversal Foundation | balance_sheet, operating_cf, roe_trend | 0.30 |
| L3 | Turning Momentum | revenue_inflection, profit_inflection, delta_g_positive | 0.40 |

Each layer score equals the average of its signal values multiplied by 100. The total distress score is:

```
Total = L1 * 0.30 + L2 * 0.30 + L3 * 0.40
```

The engine also produces a Davis Double composite score that combines four dimensions:

```
Davis Double = valuation * 0.30 + trend * 0.15 + prosperity * 0.30 + distress * 0.25
```

The distress score feeds directly into scenario probability adjustment (see Section 6).

### Angle B: PS plus DCF Dual Model

**PS (Price-to-Sales) valuation** is the primary relative valuation method during the loss period. Revenue is positive even when earnings are negative, so PS remains defined. The target's forward-year revenue estimate is multiplied by scenario-specific PS multiples derived from peer calibration.

**DCF (Discounted Cash Flow) valuation** measures intrinsic value. Free cash flows are projected over an explicit forecast horizon, then a terminal value is computed using a perpetual growth rate. A 5-by-5 WACC-by-perpetual-growth sensitivity matrix replaces single-point assumptions.

The two models are combined with equal weighting (50 percent PS, 50 percent DCF) within each scenario. This prevents over-reliance on either method.

### Angle C: Global Peer Anchoring

The peer set should span three categories when possible:

1. **Domestic listed direct peers** (same product, same market)
2. **International listed direct peers** (same product, global market)
3. **Unlisted or recently IPO'd direct peers** (transaction multiples, if data exists)

For each peer, collect: market cap, revenue, PS, PB, gross margin, and market share. The peer PS distribution calibrates the scenario-specific PS tiers (see Section 7).

```
Peer PS Distribution:
   low quartile  ──────►  median  ──────►  high quartile
        |                     |                  |
   pessimistic tier      neutral tier       optimistic tier
```

## 4. Data Sources

### Tushare endpoints

The skill pulls structured financial and market data through the `davis_analyzer.tushare_client` module. The following endpoints are required:

**Core financial statements (5 endpoints):**

| Endpoint | Purpose | Key fields |
|----------|---------|------------|
| `income` | Revenue, net income, R&D expense, operating costs | total_revenue, n_income, rd_exp, oper_costs |
| `balancesheet` | Assets, liabilities, contract liabilities, construction-in-progress | total_assets, total_liab, contract_liab, cip |
| `cashflow` | Operating cash flow, investing, financing | n_cashflow_act, n_cashflow_inv_act |
| `fina_indicator` | Gross margin, ROE, debt ratio, quarterly metrics | grossprofit_margin, roe, debt_to_assets |
| `daily_basic` | Market cap, PE, PB, PS, circulation metrics | total_mv, pe_ttm, pb, ps_ttm |

**Chip-structure and capital-flow endpoints (6 endpoints):**

| Endpoint | Purpose |
|----------|---------|
| `top10_holders` | Top 10 shareholders (concentration, controlling shareholder) |
| `top10_floatholders` | Top 10 floating shareholders (tradable concentration) |
| `stk_holdernumber` | Shareholder count changes (retail vs institutional crowding) |
| `share_float` | Lockup expiry schedule (unlocking pressure timeline) |
| `top_list` | Dragon-tiger list (institutional and hot-money flows) |
| `hsgt_top10` | Northbound capital holdings (foreign capital positioning) |

### Web research sources

Tushare does not cover everything. The following require manual web research:

| Data type | Source | What to extract |
|-----------|--------|-----------------|
| Annual reports | Official disclosure portal (cninfo/juchao) | Production volume, capacity utilization, product mix, segment revenue |
| Broker research reports | Sell-side coverage | Forward estimates, industry forecasts, target prices |
| Industry association data | Sector-specific associations, market research firms | Industry size, growth rate, pricing trends, capacity supply |
| International peer filings | SEC EDGAR, foreign exchange filings | Global peer revenue, margins, capacity, market share |
| Pricing and order data | Industry trade publications | Spot prices, order backlogs, contract structures |

All web-sourced data must cite the publication name and date in the report.

## 5. Execution Flow

The valuation pipeline runs in 4 waves plus a final review wave. Each stage has a defined input, mechanism, and output.

```
Wave 1 (3 parallel)          Wave 2 (4 parallel)         Wave 3 (2 serial)        Wave 4 (1)
+---------------+            +---------------+           +---------------+        +---------------+
| Stage 1: Data |  ------->  | Stage 4: Fin. |  -------> | Stage 8: PS + | -----> | Stage 10:     |
|   Collection  |            |   Deep Analysis|          |   DCF Model   |        |   Report      |
+---------------+            +---------------+           +---------------+        |   Writing     |
+---------------+            +---------------+           +---------------+        +---------------+
| Stage 2:      |  ------->  | Stage 5: Peer |  -------> | Stage 9: 3-   |
|   Distress    |            |   Comparison  |           |   Scenario    |
|   Scoring     |            +---------------+           |   Synthesis   |
+---------------+            +---------------+           +---------------+
+---------------+            | Stage 6: Chip |
| Stage 3:      |  ------->  |   Structure   |
|   Existing    |            +---------------+
|   Report Read |            +---------------+
+---------------+            | Stage 7:      |
                             |   Industry    |
                             |   Beta        |
                             +---------------+
                                                         Wave FINAL (4 parallel + push)
                                                         F1: Chapter completeness audit
                                                         F2: Calculation verification
                                                         F3: Readability and logic QA
                                                         F4: Differentiation and scope guard
```

### Stage details

| Stage | Wave | Input | Mechanism | Output |
|:-----:|:----:|-------|-----------|--------|
| 1 | W1 | Target ts_code, peer ts_codes | Query all 11 tushare endpoints, fetch 3+ years of financial history | Raw data package (financials, market data, chip structure) |
| 2 | W1 | Financial data from Stage 1 | Run `calculate_distress_score` and `calculate_davis_double_score` | Distress score (3 layers + total), Davis Double composite |
| 3 | W1 | Existing reports on target | Read and extract prior valuation methods, conclusions, data gaps | Differentiation document identifying what this report adds |
| 4 | W2 | Financial data from Stage 1 | Compute gross margin trend, R&D ratio, contract liability trend, inventory turnover, quarterly breakdowns | Financial deep analysis with inflection signal assessment (green/yellow/red) |
| 5 | W2 | Peer financial data from Stage 1 | Build comparison matrix: market cap, revenue, PS, PB, margin, market share | Peer valuation matrix with PS distribution and tier calibration |
| 6 | W2 | Chip-structure data from Stage 1 | Analyze shareholder count trend, lockup expiry, institutional positioning, northbound flow | Chip structure assessment (selling pressure vs absorption signals) |
| 7 | W2 | Industry research (web) | Collect industry pricing, capacity, order data not in tushare | Industry beta context and supply-demand balance assessment |
| 8 | W3 | Outputs from Stages 4, 5, 6, 7 | Build PS valuation with peer-calibrated tiers; build DCF with 5x5 WACC matrix | Per-scenario PS targets, per-scenario DCF targets |
| 9 | W3 | Stage 8 outputs + Stage 2 distress score | Apply distress-adjusted probabilities to combine PS and DCF per scenario; compute probability-weighted target | Three-scenario summary table, probability-weighted target price range |
| 10 | W4 | All prior outputs | Write 10-chapter valuation report with full source traceability | Final report markdown file |

## 6. Distress to Probability Adjustment

The base scenario probabilities for loss-making targets are:

| Scenario | Base probability |
|----------|:---------------:|
| Pessimistic | 20% |
| Neutral | 50% |
| Optimistic | 30% |

These base probabilities are adjusted by four trigger rules derived from the distress engine output. Each rule fires independently based on the computed distress layer scores and growth momentum.

### Trigger rules

**Rule 1: Layer 1 (Distress Confirmation) above 50**

When L1 score exceeds 50, the distress is confirmed as deep and real. Shift probability mass toward the pessimistic scenario.

- Pessimistic probability: +10 percentage points
- Optimistic probability: -10 percentage points

**Rule 2: Layer 2 (Reversal Foundation) below 33**

When L2 score falls below 33, the balance sheet and cash flow efficiency do not yet support a turnaround. Downgrade the optimistic scenario.

- Optimistic probability: -5 percentage points (shifted to neutral)

**Rule 3: Layer 3 (Turning Momentum) in midrange**

When L3 score sits in the midrange (roughly 33 to 66), inflection signals are mixed. No adjustment is warranted.

- No probability adjustment

If L3 is above 66 (momentum confirmed) or below 33 (momentum absent), apply adjustments documented in `references/distress-probability-rules.md`.

**Rule 4: Growth acceleration (delta_g positive)**

When the prosperity delta_g indicator is positive, revenue growth is accelerating. This is a favorable micro-signal that partially offsets pessimistic shifts.

- Optimistic probability: +3 percentage points
- Pessimistic probability: -3 percentage points

### Adjustment application

Rules are applied cumulatively. After all applicable rules fire, renormalize so the three scenario probabilities sum to 100 percent. Record the pre-adjustment base probabilities, each rule's effect, and the final adjusted probabilities in the report so the adjustment chain is fully auditable.

Example adjustment chain (generic):

```
Base:      Pessimistic 20% | Neutral 50% | Optimistic 30%
Rule 1:    Pessimistic 30% | Neutral 50% | Optimistic 20%   (L1 > 50)
Rule 2:    Pessimistic 30% | Neutral 55% | Optimistic 15%   (L2 < 33)
Rule 4:    Pessimistic 27% | Neutral 55% | Optimistic 18%   (delta_g > 0)
```

## 7. Valuation Model Schema

This section gives a brief overview. The full schema with field definitions and validation rules lives in `references/valuation-model-schema.md`.

### PS valuation tiers

The PS multiple applied to forward-year revenue depends on the scenario. These tiers are starting defaults that should be calibrated against the actual peer PS distribution from Angle C.

| Scenario | PS multiple range | Rationale |
|----------|:-----------------:|-----------|
| Pessimistic | 6 to 10x | Sector under pressure, peers trading at distressed multiples |
| Neutral | 10 to 15x | Normalized conditions, median peer multiple |
| Optimistic | 15 to 25x | Turnaway confirmed, sector premium, growth acceleration |

If peer calibration shows the target's sector consistently trades outside these defaults, override with the peer-derived bands and document the override.

### DCF sensitivity matrix

The DCF model uses a 5-by-5 matrix varying WACC and perpetual growth rate. This replaces single-point assumptions that hide sensitivity.

```
              Perpetual Growth Rate
              2%      3%      4%      5%      6%
         +--------+--------+--------+--------+--------+
   9%   |        |        |        |        |  MAX   |
   10%  |        |        |        |        |        |
WACC 11% |        |  CENTRAL RANGE   |        |        |
   12%  |        |        |        |        |        |
   13%  |  MIN   |        |        |        |        |
         +--------+--------+--------+--------+--------+
```

Each cell contains a DCF enterprise value (in the target's currency). The central cell (around WACC 11 percent, perpetual 4 percent) is the reference point. Extreme corners test the boundaries of optimism and pessimism.

### Scenario combination

Within each scenario, combine PS and DCF targets with equal weighting:

```
Combined scenario target = (PS target + DCF target) / 2
```

Then apply the distress-adjusted probabilities to produce the final probability-weighted target:

```
Weighted target = P_pess * target_pess + P_neut * target_neut + P_opt * target_opt
```

The output is a single probability-weighted point estimate with the full three-scenario range reported alongside it. The range is the decision-relevant output, not the point estimate.

## 8. Report Anatomy

The valuation report has 10 chapters. Every chapter must cite its data sources inline.

### 10-chapter structure

| Chapter | Title | Primary data source | Key output |
|:-------:|-------|---------------------|------------|
| 1 | Overview and Valuation Methodology | All angles | Triangle Framework summary, PE failure explanation, differentiation from prior reports |
| 2 | Financial Deep Analysis | tushare income/balancesheet/fina_indicator | Gross margin trend, R&D ratio, contract liability trend, quarterly breakdowns, inflection signals |
| 3 | Distress Quantitative Scoring | davis_analyzer output | Three-layer signal detail, distress score, Davis Double composite, score interpretation |
| 4 | Peer Comparison | tushare + web research | Peer valuation matrix (domestic + international), PS distribution, tier calibration |
| 5 | Chip Structure and Capital Flow | 6 chip-structure endpoints | Shareholder count trend, lockup status, institutional positioning, northbound flow |
| 6 | Industry Beta and Capacity/Price/Orders | Web research | Industry supply-demand, pricing trends, capacity, order backlogs |
| 7 | PS Valuation Modeling | Peer calibration + revenue forecast | Per-scenario PS targets with peer justification |
| 8 | DCF and WACC Sensitivity Matrix | Financial projections | 5x5 matrix, WACC decomposition, per-scenario DCF targets |
| 9 | Three-Scenario Target and Probability Weighting | Stages 8 + 9 | Adjustment chain, scenario summary table, probability-weighted target |
| 10 | Risk Factors and Conclusion | All prior | Key risks, rating, entry criteria, comparison with prior reports |

### Source traceability rule

Every quantitative claim in the report must carry an inline source tag. The target density is 80 percent or higher, meaning at least 80 percent of numbers in the report have an explicit source citation.

Source tag format:

```
(number, source)
```

Examples of acceptable source tags:

- `(12.30, tushare income 20251231)` for a financial figure
- `(8.5x, tushare daily_basic YYYY-MM-DD)` for a market multiple
- `(42.15, Stage 2 distress score)` for a computed metric
- `(55.00, target annual report p.47)` for a web-sourced figure

Unsourced numbers fail the final audit (Wave FINAL, F1). If a number cannot be sourced, it must not appear in the report.

## 9. Decision Boundaries (CRITICAL)

Read this section before starting any valuation work.

### Guardrails

**1. Do NOT modify the davis_analyzer engine.** The distress scoring functions, weights, and signal thresholds are fixed in `davis_analyzer/constants.py` and `davis_analyzer/distress.py`. The agent calls these functions as-is. If the engine output seems wrong, document the concern in the report but do not change the source code.

**2. Do NOT copy tables from prior reports.** Every table in the valuation report must be freshly constructed from current data. Reusing a table from an existing report without re-querying the data violates source traceability.

**3. Do NOT include unsourced data.** Every number needs an inline source tag. If the source cannot be identified, the number is omitted. No exceptions, no estimates, no interpolations to fill gaps.

**4. Do NOT output a single target price.** The report must present a three-scenario range with probability weights. A single point estimate without the supporting range and probability distribution is incomplete and misleading.

**5. Do NOT fabricate peers.** Peer companies must be real, verifiable entities with accessible financial data. If fewer than 3 suitable peers exist, follow the edge case below rather than inventing comparables.

**6. Do NOT use PE as the primary valuation metric.** For loss-making targets, PE is either negative or distorted. PS is the primary relative metric. PE may appear in the report only as a diagnostic showing why it fails.

### Edge cases

| Situation | Response |
|-----------|----------|
| No suitable peers found | Use the target's own historical PS bands (3-year range) as the calibration anchor instead of peer PS. Document that peer anchoring was unavailable and historical bands were substituted. |
| PE fails (negative or extreme) | Switch to PS-primary. Document the PE value and why it is not usable. This is the default case for this skill, not an exception. |
| PS also fails (revenue near zero) | Switch to EV-based or research-DCF-only valuation. Flag that the target may not fit this skill's scope (see Section 1, When NOT to use). |
| Data incomplete (missing quarters) | Run a completeness check. If more than 25 percent of required data points are missing, halt and report the gap. Do not proceed with partial data dressed up as complete. |
| All three angles diverge sharply | Report the divergence explicitly. Divergence is a risk signal, not an error to resolve by picking one angle. State which angle the agent weights most and why. |
| Target has positive net income in latest quarter | The target may be exiting the loss-making phase. Still apply the framework, but note the inflection in the distress interpretation. |
| Contract liabilities collapsing | Treat as a leading indicator of order weakness. Flag in Chapter 2 and factor into the pessimistic scenario revenue estimate. |

## 10. Companion Files

This skill directory contains companion files that provide checklists, reference schemas, and script templates. The SKILL.md you are reading is the methodology overview. The companion files hold the operational detail.

### File index

| File | Type | Purpose |
|------|------|---------|
| `checklists/differentiation-audit.md` | Checklist | Pre-publication audit to confirm the report differs structurally from any prior report on the same target |
| `checklists/source-traceability.md` | Checklist | Verifies that every number in the report carries an inline source tag and that density exceeds 80 percent |
| `references/valuation-model-schema.md` | Reference | Full field-level schema for the PS and DCF models, including validation ranges and override rules |
| `references/distress-probability-rules.md` | Reference | Complete trigger rule definitions, edge-case thresholds for L3, and worked adjustment examples |
| `references/study-script-templates/quant_data_template.py` | Script template | Template for querying all 11 tushare endpoints for a single target plus peers |
| `references/study-script-templates/scoring_template.py` | Script template | Template for running `calculate_distress_score` and `calculate_davis_double_score` on the target |
| `references/study-script-templates/financial_deep_template.py` | Script template | Template for 8-indicator financial deep dive analysis |

### When companion files are missing

If a companion file listed above does not yet exist, the agent should follow the methodology described in this SKILL.md directly and note which reference was unavailable. Do not block on missing companion files. The SKILL.md is self-contained for methodology purposes.

### Relationship to other skills

| Related skill | Relationship |
|---------------|--------------|
| `invest-sop-pre-market` | Pre-market analysis SOP. Uses a different decision framework (matrix-based) for active holdings. This skill is for valuation of new targets, not position management. |
| `local-development-environment` | Environment setup. Run this first if `davis_analyzer` import fails or Tushare token is missing. |

### Skill boundaries

This skill produces a **valuation report**. It does not:

- Execute trades or issue trading instructions
- Manage existing portfolio positions
- Replace the investment decision (the human reader decides)
- Provide real-time price targets (the report is a point-in-time assessment)

The report's rating (for example, "underweight" or "watch") reflects the valuation analysis. The user reads the report and makes the final decision.
