---
name: industry-prosperity
description: Use this skill when applying 景气度 (prosperity) investment methodology to A-share stocks or industries. Trigger phrases include '景气度', 'G+ΔG', '六维指标', '山峰理论', '成长股投资时钟', '位置感', '景气预期', '二次点火', 'ΔG', '景气拐点', '景气加速', 'prosperity scoring'.
---

# Industry Prosperity Investment Methodology

This skill gives agents a repeatable framework for applying prosperity-driven (景气度) investment analysis to A-share industries and individual stocks. The core insight is that the market's "pricing anchor" for growth shifts across eras. In 2016 to 2017, high ROE meant high prosperity. In 2020 to 2021, high absolute growth rates dominated. From 2024 onward, the anchor is G plus ΔG combined: growth must be high AND accelerating.

The central principle: prosperity investing is never about buying "high growth." It is about buying "growth that is still accelerating." The marginal change in growth (ΔG) matters more than the absolute level of growth (G). This skill operationalizes that insight through a structured set of frameworks, indicator definitions, and engine mappings.

## 1. Overview

### What this skill does

The agent applies a multi-layered prosperity analysis framework to stocks or industries, classifies their position in the growth cycle, and produces a structured assessment of whether the current prosperity is real, accelerating, or decelerating. Every framework conclusion must be supported by named data sources.

The skill covers five connected layers:

```
    Layer 1                Layer 2              Layer 3
    G + ΔG Framework  →    Six-Dimensional  →   Mountain Theory
    (growth + acceleration) Indicators           (left/right side)
                               |
                               ↓
    Layer 5                Layer 4
    Position Sense    ←     Growth Stock
    Framework                Investment Clock
    (position/space/         (four quadrants)
     tracking/coordinates)
```

### When to use this skill

Use when the target meets ANY of these conditions:

- Assessing whether an industry or stock is in an accelerating, decelerating, or inflection phase of its growth cycle
- Classifying a growth stock into a cycle stage to decide hold, reduce, or add
- Building an industry comparison ranked by prosperity strength and acceleration
- Validating whether a "high growth" stock is a genuine prosperity opportunity or a deceleration trap (ΔG already negative while G still looks high)
- Pricing a turnaround or cyclical stock where the growth direction matters more than the absolute level
- Applying the "secondary ignition" (二次点火) screen: finding stocks where both G and ΔG are positive

### When NOT to use this skill

Do NOT use this skill when any of the following apply:

| Condition | Why not | What to use instead |
|-----------|---------|---------------------|
| Market is purely liquidity-driven (like the 2014 "water bull") | Prosperity signals have near-zero pricing power when liquidity dominates | Macro-driven strategy, liquidity analysis, or momentum/trend-following |
| Pre-revenue company with no growth history | G and ΔG are undefined without at least 2 quarters of comparable data | Pre-revenue frameworks, PS or DCF valuation |
| The question is "will the market go up or down" | Prosperity methodology answers "which industry or stock," not "which direction for the index" | Macro mainline strategy, DDM-based timing frameworks |
| The question is "is this stock cheap" | Prosperity answers "is growth accelerating," not "is valuation reasonable" | Valuation frameworks (PE/PB percentile, DCF) |
| Single-quarter snapshot without trend data | ΔG requires at least 2 to 3 quarters of comparable growth data to be meaningful | Wait for more data, or use analyst expectation frameworks |

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

2. **Tushare API token is configured.** A valid `TUSHARE_TOKEN` must be present in `.env` at the repository root. The token must have sufficient API tier to access financial statements (income, fina_indicator, daily_basic) for at least 3 to 4 quarters of history. Verify:
   ```bash
   python -c "from davis_analyzer.config import get_config; print(get_config().tushare_token[:8] + '...')"
   ```

3. **Target stock code or industry classification is identified.** The agent needs either a `ts_code` for a single stock, or a set of `ts_code` values representing an industry for sector-level analysis.

4. **At least 4 quarters of financial history are available.** The ΔG calculation engine requires a minimum of 2 quarters (simple delta) or ideally 4+ quarters (3-quarter moving average) of YoY revenue and profit growth data. Fewer than 2 quarters means ΔG cannot be computed reliably.

5. **Python 3.12 or later** is active in the project environment.

6. **Optional: Analyst consensus expectation data.** If applying the prosperity expectation (景气预期) framework, the agent needs access to analyst consensus revenue and profit forecasts (FY1/FY2) from Wind or equivalent sources. Tushare does not provide this directly.

## 3. G plus ΔG Framework

The G+ΔG framework is the analytical core of prosperity investing. It separates the absolute growth level (G) from the marginal change in growth (ΔG), because the market prices the second derivative more aggressively than the first.

### Core definitions

| Variable | Meaning | Calculation |
|----------|---------|-------------|
| **G (Growth)** | The absolute level of YoY growth rate for revenue or profit | G = (current_period / prior_year_same_period - 1) * 100 |
| **ΔG (Delta-G)** | The marginal change in the growth rate (second derivative of the underlying metric) | ΔG = current_G - previous_G (in percentage points) |

Positive ΔG means growth is accelerating. Negative ΔG means growth is decelerating. Zero ΔG means growth is stable but neither accelerating nor decelerating.

### The 30 percent threshold effect

Research across 2009 to 2026 A-share data reveals a critical pattern: when industry net profit growth drops below 30 percent, excess returns for high-prosperity sectors decline significantly. This is the most important single threshold in prosperity investing.

| Condition | Impact on excess returns |
|-----------|-------------------------|
| Growth above 30 percent and ΔG positive | Excess returns at their strongest (both G and ΔG favorable) |
| Growth above 30 percent but ΔG negative | Returns begin to compress (deceleration phase) |
| Growth drops below 30 percent | Excess returns decline sharply regardless of ΔG |
| Growth drops below 30 percent AND decline exceeds 50 percent | Returns deteriorate further |
| Growth drops below 30 percent with steep deceleration slope | Worst outcome (peak-to-trough transition) |
| Special case: absolute growth above 100 percent | Tolerance for decline extends to roughly 60 percent before returns suffer badly |

Three conditions converging (growth below 30 percent, decline exceeding 50 percent, steep deceleration slope) is the strongest signal that a prosperity inflection has arrived.

### Why ΔG matters more than G

Empirical ranking of indicators by correlation with stock price movements (strongest to weakest):

1. **Δ profit growth rate** (strongest predictor)
2. **Δ revenue growth rate**
3. **Δ ROE**
4. **Revenue growth rate** (absolute level)
5. **Profit growth rate** (absolute level)

The marginal change indicators (Δ-type) consistently outperform the absolute level indicators. This is the empirical foundation for the "G plus ΔG combined" principle: a stock is a genuine prosperity opportunity only when growth is high AND still accelerating.

### The prosperity expectation layer

Financial data is inherently lagged. The prosperity expectation framework (景气预期) addresses this by tracking the second derivative of analyst consensus estimates:

- Collect monthly analyst consensus revenue and profit forecasts for each industry
- Compute the YoY change rate of the consensus estimate (not the actual financial result)
- Track the slope of this change rate month over month
- "Slope matters more than position": a steepening upward slope in analyst expectations is a stronger signal than a high absolute expectation level

The consensus estimate change rate is computed as:

```
Prosperity expectation YoY rate = (current_consensus - prior_year_same_consensus) / |prior_year_same_consensus|
```

This framework is validated against 31 first-level industries (71 percent effectiveness) and 134 second-level industries (70 percent effectiveness).

## 4. Six-Dimensional Prosperity Indicator System

The six-dimensional indicator system (景气度六维指标体系) provides a "dashboard" for measuring industry prosperity from multiple independent angles. All six indicators should be assessed together. Convergence across all six dimensions strengthens the prosperity signal. Divergence is itself a warning.

Each indicator has a definition, a data source type, and a directional reading. The skill defines the framework and calculation logic. It does NOT auto-scrape indicator data. The agent must collect values from appropriate data sources.

### Indicator definitions

| # | Indicator | Definition | Prosperity signal | Data source type |
|---|-----------|------------|-------------------|------------------|
| 1 | **BB Ratio** (Book-to-Bill) | Current-period orders divided by current-period shipments | BB greater than 1 means orders exceed shipments (prosperity up). BB greater than 1.3 signals a super-cycle supply-demand imbalance | Vendor earnings calls, distributor market reports |
| 2 | **Capacity Utilization** (稼动率) | Percentage of total production capacity actually in use | Above 90 percent signals price-upward pressure. Below 70 percent signals oversupply | Vendor earnings calls, industry capacity tracking |
| 3 | **Lead Time** (交期) | Time from order placement to delivery, in weeks | Normal range 8 to 10 weeks. Extension beyond 16 weeks signals shortage. Beyond 30 weeks signals severe shortage | Distributor publications, industry trade reports |
| 4 | **Distributor Inventory** (库存) | Months of inventory held in the distribution channel | 3 to 6 months is normal. Dropping to 1 to 1.5 months signals tightness. Below 1 month signals extreme shortage | Distributor surveys, industry research |
| 5 | **Production Schedule Horizon** (排产) | How far into the future the production schedule (backlog) is filled | Normal is 6 to 12 months. 12 to 18 months signals strong prosperity. Beyond 24 months signals extreme demand certainty | Vendor earnings calls, supply chain research |
| 6 | **LTA** (Long-Term Agreement) | Number and scale of 1 to 3 year supply contracts signed by downstream customers | Increasing LTA adoption signals that shortage is real and confirmed by industrial capital. LTA diffusion provides a floor for the pricing cycle | Vendor disclosures, supply chain interviews |

### Interpretation rules

**Positive feedback loop:** The six indicators reinforce each other. Lead time extension causes customers to order early, pushing BB Ratio higher, which drains inventory, extending production schedules, and increasing LTA adoption. This self-reinforcing mechanism means prosperity upswings have strong internal persistence.

**Six dimensions all pointing up** is the strongest possible prosperity signal. When all six align upward with BB above 1.3, the industry is in an "acceleration phase," not an "early upswing phase."

**Early warning signal:** When lead times begin to shorten (even while other indicators still look strong), it is often the first sign of a prosperity peak. Lead time is the most forward-looking of the six indicators.

### Scoring approach

Each dimension can be scored on a 0 to 10 scale based on its current reading relative to historical norms and thresholds. A weighted composite score across all six dimensions provides a single prosperity intensity rating. The weighting should emphasize BB Ratio (most sensitive leading indicator) and lead time (most forward-looking).

## 5. Mountain Theory (山峰理论)

The Mountain Theory (山峰理论), developed by the open-source securities strategy team, is the most concise tool for classifying a growth stock's position in its cycle. It uses two variables: G (growth rate) and Δg (acceleration of growth rate).

```
                 Growth Rate G (%) — Mountain Peak
                          /\
                         /  \
           Left Side     /    \     Right Side
           (Δg > 0)     /      \    (Δg < 0)
           Davis       /        \    De-rating
           Double      /          \   Risk Zone
           Opportunity /            \
                      /              \
        ———————————————————————————————————————→ Time
        Upstart    Accelerate   Peak   Decelerate   Inflection
```

### Three core variables

| Variable | Meaning | Judgment criteria |
|----------|---------|-------------------|
| **G (growth rate)** | YoY growth rate absolute level for profit or revenue | G above 30 percent = high prosperity. G below 10 percent = low prosperity |
| **Δg (acceleration)** | Marginal change in growth rate (second derivative) | Δg positive = growth accelerating. Δg negative = growth decelerating |
| **Mountain position** | The combination of G and Δg | Left side (G up, Δg positive) / Right side (G up but Δg negative) / Behind mountain (G declining) |

### Three zones and their investment meaning

**Left side of the mountain (Δg positive) = Davis Double opportunity zone:** Growth is accelerating. The market pays not just for current earnings but for the expectation of accelerating earnings. Both EPS and PE expand. This is the sweetest phase of prosperity investing.

**Right side of the mountain (Δg negative but G still high) = de-rating risk zone:** Growth is still high but decelerating. The market begins pricing in "prosperity peak." EPS continues up but PE contracts. Stock prices stagnate or fall despite high headline growth. This is the truth behind the 2022 "prosperity investing failed" narrative. It did not fail. Δg simply turned negative.

**Behind the mountain (G declining) = Davis Double sell zone:** Growth rate itself turns negative. Both EPS and PE decline. Avoid.

### Investment rule

Buy on the left side (acceleration phase). Sell at the start of the right side (when deceleration just begins). The critical transition signal is Δg crossing from positive to negative.

## 6. Growth Stock Investment Clock (成长股投资时钟)

The Growth Stock Investment Clock, developed by Huatai Securities, classifies growth stocks into four quadrants using two axes: speed (G) and acceleration (ΔG). The four quadrants map directly to the Mountain Theory zones.

### Four quadrants

| Quadrant | Speed G | Acceleration ΔG | Cycle stage | Investment strategy |
|----------|:-------:|:---------------:|-------------|---------------------|
| **Singularity** (奇点) | Low base, G just turned positive | ΔG turning from negative to positive, just starting to accelerate | Penetration rate breaks the critical threshold (around 5 percent) | Early positioning, high risk high reward |
| **Acceleration** (加速) | G rising rapidly to high levels | ΔG persistently positive and expanding | Penetration rate 5 to 15 percent, main upswing | Core allocation, Davis Double opportunity |
| **Deceleration** (减速) | G still at high levels | ΔG turns negative (growth rate peaks and begins declining) | Penetration rate 15 to 30 percent, growth slowing | Reduce or switch, increased speculation |
| **Anti-singularity** (反奇点) | G drops sharply or turns negative | ΔG turns from positive to negative, deep decline | Prosperity inflection confirmed | Avoid or wait for next singularity |

### Mapping to Mountain Theory

- Singularity approximates the base of the left side (prosperity just starting)
- **Acceleration approximates the upper left side (Davis Double core zone)**
- Deceleration approximates the peak transitioning to the right side (growth peaking, valuation beginning to contract)
- Anti-singularity approximates behind the mountain (Davis Double sell zone)

Both frameworks share the same mathematical foundation: the second derivative. The current stage (Acceleration or Left Side) is the best window for growth stock investing.

### Transition signals

- **Singularity to Acceleration:** Penetration rate crosses 5 percent, G turns firmly positive, ΔG begins expanding
- **Acceleration to Deceleration:** ΔG peaks and begins to narrow but remains positive. G is still high. This is the hardest transition to catch because headline numbers still look great.
- **Deceleration to Anti-singularity:** G drops below 30 percent threshold AND decline exceeds 50 percent. ΔG is deeply negative.
- **Anti-singularity to next Singularity:** G bottoms out. ΔG turns from deeply negative to less negative. Early signs of revenue stabilization.

## 7. Position Sense Framework (位置感)

The Position Sense framework, developed by the GF Securities strategy team, evolves prosperity tracking from a single-indicator approach into a systematized "position, space, tracking, coordinates" pipeline. It answers four sequential questions before building or adjusting a position.

### Four-step pipeline

**Step 1: Position (位置)**

Where is the industry or stock right now in its cycle? Use the Mountain Theory and Growth Stock Investment Clock to classify the current position. Is it in the Acceleration quadrant? The Left Side of the mountain? Or already in Deceleration?

This step requires computing G and ΔG from at least 4 quarters of financial data, then mapping the result to a quadrant and mountain zone.

**Step 2: Space (空间)**

If the current position is favorable (Acceleration or Left Side), how much room is left? Assess:

- Penetration rate: if below 15 percent, significant growth runway remains
- Valuation percentile: if PB or PE is already at historical 80th percentile or above, the upside is compressed even if prosperity continues
- Industry capacity: if major producers are ramping capacity aggressively, oversupply risk may arrive before the prosperity cycle naturally ends

**Step 3: Tracking (追踪)**

What signals will confirm or contradict the current positioning? Set up a monitoring system for:

- Monthly prosperity expectation slope changes (analyst consensus revision direction)
- Quarterly financial ΔG updates (the single most important tracking metric)
- Six-dimensional indicator dashboard updates (BB Ratio, lead time, inventory as the most forward-looking trio)
- Lead time trend as the earliest warning signal for cycle peak

**Step 4: Coordinates (坐标)**

What is the decision-relevant coordinate system? Map the stock or industry against:

- Peers within the same industry (relative prosperity strength via relative ΔG)
- Industries across the market (cross-sector prosperity ranking)
- Historical cycle norms (is the current acceleration duration consistent with the 2.5-year average, or is a strong secular trend extending it?)

### The 2.5-year statistical anchor

A-share prosperity upcycles historically average 2.5 years, with the first stage (acceleration) lasting 1.5 to 2.0 years and the second stage (deceleration buffer) lasting 0.5 to 1.0 years. This is a statistical anchor, not a hard rule. Strong secular trends (like the AI industrial cycle) can break through historical duration ceilings.

## 8. Mapping to davis_analyzer

The `davis_analyzer` package contains the computational engines that implement the G plus ΔG framework, the inflection classification, and the sector-level screening. This skill describes how to USE these engines. It does NOT reimplement them. Do not modify the engine source code.

### prosperity.py: The scoring engine

This module computes the four sub-scores and the composite prosperity score for a single stock or industry.

| Function | What it computes | Skill mapping |
|----------|------------------|----------------|
| `calculate_revenue_score` | Scores YoY revenue growth on a 0 to 100 scale using exponential decay weighting. Growth above 30 percent scores 80 to 100, 10 to 30 percent scores 50 to 80, 0 to 10 percent scores 25 to 50, negative scores 0 to 20 | Maps to the G dimension for revenue |
| `calculate_profit_score` | Scores YoY profit growth using higher thresholds (above 50 percent scores 80 to 100) because profit is more volatile | Maps to the G dimension for profit |
| `calculate_slope_score` | Scores the linear regression slope of the growth series using a sigmoid mapping. Strongly positive slope scores near 100, flat scores 50, strongly negative scores near 0 | Maps to the ΔG dimension (acceleration proxy) |
| `calculate_duration_score` | Scores consecutive quarters of positive growth plus a magnitude bonus | Maps to prosperity persistence |
| `calculate_delta_g` | Computes the raw ΔG value: current growth minus previous growth in percentage points | Maps directly to ΔG in the Mountain Theory |
| `calculate_prosperity_score` | Combines all four sub-scores into a composite using fixed weights: revenue 0.30 + profit 0.30 + slope 0.25 + duration 0.15 | The composite prosperity score for ranking and screening |
| `dupont_decomposition` | Classifies the dominant ROE driver: net margin (pricing power), asset turnover (demand efficiency), or leverage (warning) | Maps to ROE quality assessment within prosperity analysis |
| `batch_prosperity` | Runs `calculate_prosperity_score` across a dict of ts_code to FinancialData lists | Batch scoring for industry-level analysis |

### prosperity_inflection.py: The cycle classification engine

This module identifies inflection quarters, assesses catalysts and risk factors, and generates narratives for the four-stage classification used by the Growth Stock Investment Clock.

| Function | What it computes | Skill mapping |
|----------|------------------|----------------|
| `analyze_inflection` | Builds a full inflection analysis (catalysts, narrative) for a stock given its ProsperityScore and stage | The entry point for cycle classification |
| `identify_inflection_quarter` | Finds the most recent quarter where YoY revenue or profit growth crossed zero (sign change) | Maps to the Singularity to Acceleration or Anti-singularity transition signals |
| `assess_catalysts` | Identifies recovery catalysts: ROE improving, operating cash flow positive, debt ratio declining, revenue stabilizing | Maps to the Tracking step in the Position Sense framework |
| `generate_inflection_narrative` | Produces a human-readable summary of the inflection analysis | Report-ready text for the cycle classification conclusion |

The four stages produced by this engine are: Acceleration (加速期), Deceleration (减速期), Rising Inflection (上升拐点), Declining Inflection (下降拐点). These map to the Growth Stock Investment Clock quadrants.

### prosperity_sector.py: The industry-level screening engine

This module aggregates individual stock scores to the industry level and applies the "secondary ignition" screen.

| Function | What it computes | Skill mapping |
|----------|------------------|----------------|
| `aggregate_industry_prosperity` | Groups stocks by industry and computes weighted average composite scores, median ΔG, and top stock lists | Maps to the Coordinates step (cross-industry ranking) |
| `classify_stock_stage` | Classifies a single stock into Acceleration, Deceleration, Rising Inflection, or Declining Inflection using score thresholds and relative ΔG | Maps to the Position step |
| `classify_industry_stage` | Same classification at the industry aggregate level | Maps to industry-level Position |
| `screen_g_delta_g_ignition` | Returns the set of stocks where growth is high AND relative ΔG is positive AND operating cash flow is positive | The "secondary ignition" (二次点火) screen: G and ΔG combined |
| `compute_relative_delta_g` | Computes each stock's ΔG minus its industry median ΔG | Maps to peer-relative positioning within the Coordinates step |
| `generate_ignition_reasons` | Produces human-readable reasons explaining why a stock qualifies as secondary ignition | Report-ready text for ignition qualification |
| `generate_risk_warnings` | Produces risk-warning labels: growth slowing, growth insufficient, trend declining, persistence questionable, negative operating cash flow | Maps to risk monitoring in the Tracking step |

### Execution flow

When applying this skill, the typical computation flow is:

```
1. Fetch financial data (4+ quarters)
   via davis_analyzer.financial_fetcher
         ↓
2. Calculate ProsperityScore per stock
   via prosperity.calculate_prosperity_score
         ↓
3. Compute relative ΔG (stock vs industry)
   via prosperity_sector.compute_relative_delta_g
         ↓
4. Classify cycle stage per stock
   via prosperity_sector.classify_stock_stage
         ↓
5. Screen for secondary ignition candidates
   via prosperity_sector.screen_g_delta_g_ignition
         ↓
6. Generate inflection analysis and narratives
   via prosperity_inflection.analyze_inflection
         ↓
7. Aggregate to industry level (optional)
   via prosperity_sector.aggregate_industry_prosperity
         ↓
8. Produce risk warnings and ignition reasons
   via prosperity_sector.generate_risk_warnings
   via prosperity_sector.generate_ignition_reasons
```

The skill does NOT auto-scrape the six-dimensional indicator data (BB Ratio, lead time, etc.). Those require manual collection from vendor disclosures and industry research. The engine handles the financial-data-driven scoring and classification. The agent supplements with manually collected indicator data.

## 9. Limitations

### Inherent limitations of prosperity methodology

**1. Financial data lag.** Quarterly reports arrive 1 to 4 months after the period ends. The prosperity expectation framework (tracking analyst consensus revisions) partially addresses this, but does not eliminate it. Post-earnings announcement drift provides a 30 to 35 trading day window where the information still has predictive value, but this window decays.

**2. Pricing anchor shifts across eras.** What the market is willing to pay for changes over time. In 2016 to 2017 it was ROE. In 2020 to 2021 it was absolute growth. From 2024 onward it is G plus ΔG combined. A framework calibrated to one era's anchor may underperform when the anchor shifts.

**3. Crowding risk.** High prosperity sectors attract concentrated capital. When crowding reaches extreme levels (such as the top 5 percent of stocks by turnover approaching 45 percent of total volume), any negative catalyst can trigger a stampede unrelated to fundamentals.

**4. Expectation gap trap.** When consensus expectations are already extremely high, even beating estimates may not move the stock (good news already priced in). Conversely, low-expectation sectors that modestly beat can generate outsized returns.

**5. Policy intervention risk.** A-share policy interventions can instantly zero out an industry's prosperity (education "double reduction" in 2021, platform antitrust in 2022, procurement expansion in pharmaceuticals). These events are not predictable through prosperity data.

**6. Historical data dependency.** All thresholds (the 30 percent threshold, the 2.5-year average cycle, the 50 percent decline criterion) are derived from historical backtests. Market structure changes can render historical patterns unreliable.

### When prosperity methodology is least effective

| Market environment | Effectiveness | Best alternative |
|---------------------|---------------|------------------|
| Full bull market driven by liquidity | Lowest | Liquidity and momentum strategies |
| Style switching (growth to value or vice versa) | Low | Style timing frameworks |
| Black swan events | Near zero | Risk management and position sizing |
| Structural market (sector divergence) | Highest | This skill is optimal here |
| Range-bound market | High | Combine prosperity with mean reversion |

## 10. Relationship with Other Methodologies

Prosperity investing is one layer in a complete A-share investment system. It answers "which industry or stock" but not "which direction for the market," "when to buy or sell," or "is this stock cheap."

| Related methodology | Relationship | Boundary |
|---------------------|--------------|----------|
| **Valuation frameworks** (PE/PB percentile, DCF) | Prosperity answers "is growth accelerating." Valuation answers "is the price reasonable." High prosperity with extreme valuation (PE above 80x) compresses returns even if growth continues | Use valuation as a constraint filter on prosperity candidates |
| **Distress reversal** (困境反转) | Prosperity is right-sided (growth already confirmed up). Distress reversal is left-sided (growth not yet confirmed but expected to bottom). They complement each other temporally | Use distress reversal for early-stage turnaround candidates. Switch to prosperity once ΔG confirms positive |
| **Macro mainline strategy** (DDM-based) | Macro determines the overall risk appetite and pricing logic (numerator-driven vs denominator-driven). Prosperity operates within the macro context | Run macro analysis first to determine if the environment favors prosperity strategies at all |
| **Factor models** (multi-factor quant) | Factor models systematize stock selection across many dimensions. Prosperity is a specific factor subset focused on growth and acceleration | Factor models can incorporate prosperity signals as growth and momentum factors |
| **Track investing** (赛道投资, penetration rate) | Prosperity focuses on "current growth." Track investing focuses on "future space." They converge during the Growth phase (penetration 5 to 30 percent) | Use track investing for import phase (penetration below 5 percent). Switch to prosperity for growth phase. Use both for maturity phase |
| **valuation-loss-making-targets** skill | That skill values companies that are losing money. Prosperity methodology applies to companies with positive, growing earnings. A stock transitioning from loss-making to profitable crosses from the valuation skill's domain into prosperity methodology's domain | Use valuation-loss-making-targets when net income is negative. Switch to prosperity methodology once growth turns positive and the 30 percent threshold is in play |

### Skill boundaries

This skill produces a **prosperity assessment and cycle classification**. It does not:

- Execute trades or issue trading instructions
- Predict market direction or index levels
- Replace the investment decision (the human reader decides)
- Provide real-time price targets (the assessment is a point-in-time classification)
- Auto-scrape the six-dimensional indicator data (the agent collects those manually)
- Value loss-making companies (use valuation-loss-making-targets instead)

The assessment's classification (for example, "Acceleration phase" or "Left side of mountain") reflects the prosperity analysis. The user reads the assessment and combines it with valuation, macro context, and risk management to make the final decision.
