---
name: multi-factor-screening
description: Use this skill when building a multi-factor quantitative stock ranking (多因子量化选股). Trigger phrases include '多因子', '量化选股', '分域选股', '三层结构', '因子打分', 'multi-factor screening'.
---

# Multi-Factor Quantitative Stock Screening Methodology

This skill gives agents a repeatable methodology for ranking a universe of stocks using a three-layer pipeline: hard filter, scoring, and enhancement. It replaces ad-hoc stock picking with a systematic framework that combines five factor families (growth, quality, valuation, technical, capital sentiment) and applies domain-specific weighting to produce a ranked candidate list.

The core principle: this skill does present-day cross-sectional ranking only. It does not run backtests, compute information coefficients, or plot factor decay curves. The output is a ranked list of stocks that pass the hard filter and score highest within their domain, with bonus signals layered on top. The ranked list is a starting point for deep research, not a final buy decision.

## 1. Overview

### What this skill does

The agent applies the three-layer structure to a stock universe, assigns a composite score to every stock that survives the hard filter, and outputs a ranked candidate list grouped by domain. Every factor input and every weight used must be traceable to the methodology defined here.

### The three-layer structure

Three sequential layers filter and rank stocks. Each layer has a single, non-overlapping responsibility. The layers run in strict order: a stock that fails layer one never reaches layer two.

```
    Stock Universe (all candidates)
              |
              v
+---------------------------+
|  Layer 1: Hard Filter     |  Binary 0/1 gate. Rejects high-risk stocks.
+---------------------------+
              |
              v
+---------------------------+
|  Layer 2: Scoring         |  0/1/2 quantile scoring. Ranks survivors.
+---------------------------+
              |
              v
+---------------------------+
|  Layer 3: Enhancement     |  Bonus points for scarce signals.
+---------------------------+
              |
              v
    Ranked Candidate List
```

- **Layer 1 (Hard Filter):** Binary gate. Any stock failing any single condition is eliminated. No partial credit. This layer keeps only stocks that are liquid, not distressed, and meet minimum quality and growth thresholds.
- **Layer 2 (Scoring):** Quantile-based scoring. Each factor maps to 0, 1, or 2 points based on where the stock ranks within its industry peer group. Factor scores are weighted and summed into a composite score.
- **Layer 3 (Enhancement):** Add-only layer. Scarce positive signals (institutional buying, insider purchases, ESG leadership) add bonus points. Absence of a bonus signal never penalizes a stock.

### When to use this skill

Use when the task requires systematic ranking or screening of a stock universe:

- Screening a broad universe (hundreds or thousands of stocks) to produce a ranked candidate list
- Building a watchlist for a specific domain (dividend, growth, value, or cyclical)
- Quarterly portfolio rebalancing where stocks must be re-scored and re-ranked
- Comparing stocks cross-sectionally within an industry or domain

### When NOT to use this skill

Do NOT use this skill when any of the following apply:

| Condition | Why not | What to use instead |
|-----------|---------|---------------------|
| Deep research on a single stock | Multi-factor screening is a cross-sectional comparison tool, not a single-stock deep dive | `valuation-loss-making-targets` skill for loss-making targets, or standard fundamental analysis for profitable companies |
| Backtesting a factor strategy | This skill does present-day ranking only, no historical simulation | Dedicated backtesting frameworks with rolling-window validation |
| High-frequency or intraday trading | Factor signals operate on monthly to quarterly time scales | Technical analysis or order-flow tools |
| Extreme market conditions (crash, bubble) | Factor models can break down entirely during regime shifts | Risk management and capital preservation frameworks |
| Pure thematic or concept-driven investing | Theme rallies are driven by narrative, not factor signals | Thematic research and event-driven analysis |

## 2. Prerequisites

Before running this skill, verify the following:

1. **Stock universe is defined.** The agent needs a starting list of candidate stocks (for example, CSI 300 plus CSI 500 constituents, or all non-ST A-share stocks). The universe should exclude ST, suspended, and recently delisted stocks by default.

2. **Financial and market data source is available.** The agent needs access to fundamental data (revenue, net income, ROE, cash flow, debt ratios) and market data (PE, PB, turnover, price history). If `davis_analyzer` is installed, it can supply this via Tushare. Verify:
   ```bash
   python -c "import davis_analyzer; print('OK')"
   ```

3. **Industry classification is available.** Domain-specific selection (分域选股) requires each stock to be tagged with an industry code. The classification should support grouping into at least the four default domains (dividend, growth, value, cyclical).

4. **Capital flow data is accessible** if the sentiment factor family is to be scored. This includes northbound capital holdings, margin balance, and block trade data.

5. **Python 3.12 or later** is active in the project environment.

## 3. Layer 1: Hard Filter (硬过滤)

The hard filter layer (硬过滤层) enforces non-negotiable minimum standards. A stock must pass every condition to enter the scoring layer. These thresholds are hardcoded defaults, not user-configurable parameters.

### Filter conditions

| Category | Condition | Threshold | Rationale |
|----------|-----------|-----------|-----------|
| Liquidity | Average daily turnover | Above a minimum (domain-dependent) | Ensures the stock is tradable without excessive slippage |
| Risk flag | ST or delisting risk | Excluded entirely | Prevents landing in distressed or low-quality names |
| Quality | ROE (TTM) | ROE greater than or equal to 12 percent | Confirms the company has real profitability, not just revenue |
| Growth | Revenue 3-year CAGR | CAGR greater than or equal to 15 percent | Confirms the company is actually growing over a meaningful period |
| Leverage | Net debt / EBITDA | Below 2.5 | Excludes over-leveraged balance sheets |
| Cash flow | Operating cash flow (TTM) | Positive | Excludes companies where paper profits do not convert to cash |
| Valuation | PE (TTM) | Below 25 (and not negative) | Excludes valuation bubbles and loss-making companies |

### Domain-specific adjustments

Some domains relax or tighten specific thresholds:

- **Cyclical domain:** PE filter is replaced by a PB filter (PB below the industry median). Cyclical stocks at cycle peaks often show deceptively low PE, so PB is the safer anchor.
- **Value domain:** The PE ceiling is loosened to 30, because value traps sometimes sit at moderate PE. Quality filters (ROE, cash flow) are tightened to compensate.
- **Financial sector (banks, securities):** Net debt / EBITDA does not apply. Replace with a non-performing loan ratio or capital adequacy filter specific to the institution type.

### Filter output

The filter outputs a binary pass-or-fail for each stock. Stocks that pass proceed to Layer 2. Stocks that fail are removed from the candidate pool for this screening cycle. The filter does not rank, score, or prioritize among survivors.

## 4. Layer 2: Scoring (打分层)

The scoring layer (打分层) assigns a composite score to every stock that passed the hard filter. Scoring uses the 0/1/2 three-tier quantile method, weighted by factor family and adjusted by domain.

### Factor families and default weights

Five factor families contribute to the composite score. The default weights sum to 100 percent:

| Factor family | Default weight | Core question | Representative indicators |
|---------------|:--------------:|---------------|---------------------------|
| Growth | 30% | Can this company keep growing? | ROE (TTM), revenue 3-year CAGR, gross margin YoY change |
| Quality | (folded into growth) | Is this a well-run company? | Operating cash flow ratio, accruals ratio, debt stability |
| Valuation | 20% | Is the price reasonable? | PE (TTM) inverse, EV/EBITDA, PB percentile |
| Technical | 25% | What does the market think? | Momentum (3 to 6 month), turnover rate, moving-average alignment |
| Capital sentiment | 25% | Where is the smart money going? | Northbound capital flow, margin balance change, block trade premium |

The growth and quality families are combined into a single 30 percent bucket because their indicators overlap heavily (ROE appears in both). Quality-specific signals (cash flow quality, accruals, leverage stability) act as secondary checks within that bucket rather than carrying independent weight.

### 0/1/2 quantile scoring method

Each factor is scored against the stock's industry peer group, not the full market. The three-tier mapping:

| Score | Meaning | Position rule |
|:-----:|---------|---------------|
| 0 | Below standard | Factor value falls below the industry median |
| 1 | Meets standard | Factor value falls between the industry median and the 80th percentile |
| 2 | Excellent | Factor value falls in the top 20 percent of the industry |

For negative-direction factors (PE, EV/EBITDA, turnover rate, leverage), the mapping is inverted: lower is better, so the bottom 20 percent gets 2 points.

The three-tier method is intentionally coarse. It sacrifices granularity for robustness, avoiding overfitting to small differences in factor values that are often noise.

### Composite score calculation

```
composite = (
    growth_score    * 0.30
  + valuation_score * 0.20
  + technical_score  * 0.25
  + sentiment_score  * 0.25
)
```

Each family score is the weighted average of its constituent factor scores (each on the 0-to-2 scale). The composite is then normalized to a 0-to-100 scale for readability.

### Factor preprocessing requirements

Before scoring, every factor value must go through these steps:

1. **Missing values:** Forward-fill from the most recent reporting period. If a core factor (ROE, revenue, PE) is missing entirely, drop the stock from the candidate pool.
2. **Extreme values:** Apply MAD (median absolute deviation) winsorization. Cap values beyond 5 times the MAD from the median. MAD is preferred over 3-sigma because A-share financial data is often skewed.
3. **Standardization:** Rank within industry peer group, then map to the 0/1/2 tiers.
4. **Industry neutralization:** All scoring happens within industry groups. A bank with 12 percent ROE scores 2 points within banking. A consumer staples company with the same ROE might score 1 point. This prevents comparing apples to oranges.

## 5. Domain-Specific Selection (分域选股)

One set of factor weights does not fit all industries. The domain-specific selection framework groups stocks into domains and applies different weight emphases for each.

### The four default domains

| Domain | Representative industries | Weight emphasis | Key differentiator |
|--------|--------------------------|-----------------|---------------------|
| Dividend (红利型) | Banks, utilities, transportation, coal, steel | Dividend yield weighted highest; quality and valuation weighted up; growth weighted down | Cash flow stability and payout consistency matter more than growth speed |
| Growth (成长型) | Electronics, software, new energy, biotech, advanced manufacturing | Growth and R&D intensity weighted highest; leverage filter relaxed; dividend yield weighted down | Revenue acceleration and R&D pipeline matter more than current dividend |
| Value (价值型) | Real estate, construction, traditional manufacturing | Valuation percentile weighted highest; quality filter tightened to avoid value traps | Low price relative to fundamentals, but only if the fundamentals are genuine |
| Cyclical (周期型) | Steel, non-ferrous metals, coal, chemicals, building materials | PB replaces PE in both filter and scoring; technical and sentiment weighted up at cycle turning points | Cycle position drives returns, not steady-state fundamentals |

### Domain weight overrides

When scoring within a domain, the factor family weights shift from the defaults. The table below shows how the default 30/20/25/25 split adjusts:

| Domain | Growth | Valuation | Technical | Sentiment | Key change |
|--------|:------:|:---------:|:---------:|:---------:|------------|
| Dividend | 20% | 25% | 25% | 30% | Sentiment (northbound + institutional flows) gets highest weight because dividend stocks attract stable institutional positioning |
| Growth | 40% | 15% | 20% | 25% | Growth weight rises sharply; valuation weight drops because high-growth names rarely look cheap |
| Value | 25% | 35% | 20% | 20% | Valuation dominates; quality signals within the growth bucket are the trap-avoidance check |
| Cyclical | 20% | 30% (PB-based) | 25% | 25% | PB-based valuation; technical momentum matters more at inflection points |

### Operational steps

1. **Classify** every stock in the filtered pool into one of the four domains using industry codes.
2. **Score** each stock using its domain-specific weights.
3. **Rank** stocks within each domain separately. Do not mix domains in a single ranking, because the composite scores are not comparable across domains.
4. **Output** four ranked lists (one per domain), each with the top N candidates.

## 6. Layer 3: Enhancement (加分层)

The enhancement layer (加分层) adds bonus points for scarce, high-information signals. The design principle is strict: a signal that is present adds points. A signal that is absent does nothing. No stock is penalized for lacking a bonus signal.

### Bonus signals

| Signal | Source | Bonus | Logic |
|--------|--------|:-----:|-------|
| Institutional accumulation | Fund quarterly reports, top-10 shareholders | +5% | Smart money is entering |
| Insider purchase | Executive or major shareholder filing | +3% | The people closest to the company are buying |
| ESG leadership | Third-party ESG rating (A or above) | +2% | Long-term sustainability signal |
| Supply chain spread | Upstream vs downstream price data | +2% | Profit margin shift leading indicator |

### How bonus points combine

Bonus points are added to the normalized composite score (on the 0-to-100 scale) after domain ranking is complete. A stock can accumulate multiple bonus signals, but the total bonus is capped at 10 percent of the composite score to prevent any single enhancement from dominating the ranking.

The final ranked list reflects: hard-filter survivors, scored by domain-specific weights, with enhancement bonuses layered on top.

## 7. Mapping to davis_analyzer

The `davis_analyzer` package implements a related but distinct scoring engine. This skill references the engine for data retrieval and lower-level scoring, but does not modify it. The two systems serve different purposes:

| Dimension | This skill (multi-factor-screening) | davis_analyzer (Davis Double Play) |
|-----------|--------------------------------------|------------------------------------|
| Purpose | Cross-sectional ranking for candidate screening | Distress and valuation scoring for deep-dive reports |
| Output | Ranked candidate list, grouped by domain | Per-stock distress score, valuation percentile, prosperity score |
| Factor coverage | 5 families, 4 domains | 4 dimensions (valuation, trend, prosperity, distress) |
| Time orientation | Present-day snapshot | Present-day snapshot with 3-year historical percentile |

### Functions to reference

When implementing this skill's scoring layer, the agent may call these `davis_analyzer` functions for data and sub-scores. Do not modify these functions. Call them as-is.

**Valuation percentile** (`davis_analyzer.valuation`):
- `batch_valuation` computes PE and PB 3-year percentiles for the stock universe. Use the PE percentile for the valuation factor score. For cyclical-domain stocks, use the PB percentile instead.
- `fetch_valuation_history` returns the daily PE/PB series used for technical momentum scoring.

**Prosperity score** (`davis_analyzer.prosperity`):
- `batch_prosperity` computes a composite prosperity score from revenue growth, profit growth, trend slope, and duration. Map this to the growth factor family score.

**Pipeline reference** (`davis_analyzer.pipeline`):
- `run_screening_pipeline` implements an 8-step process: create client, build universe, fetch valuation data, pre-filter (valuation score above 50), fetch financial data, calculate prosperity scores, calculate distress plus trend scores, calculate final Davis Double scores and rank.
- This skill's three-layer structure is a generalization. The pipeline's step 4 (pre-filter) maps to this skill's hard filter layer. Steps 6 and 7 map to the scoring layer. There is no enhancement layer equivalent in the pipeline.

**Davis Double scoring** (`davis_analyzer.scoring`):
- `calculate_davis_double_score` combines four sub-scores: valuation (0.30), trend (0.15), prosperity (0.30), distress (0.25). These weights are fixed in `constants.py` and are different from this skill's 30/20/25/25 split. The Davis Double score is useful as a secondary validation signal but should not replace this skill's composite score.

### What this skill does NOT call from davis_analyzer

- The distress scoring layer (`davis_analyzer.distress`) targets loss-making reversal candidates, which is the domain of the `valuation-loss-making-targets` skill. This skill's hard filter already excludes distressed stocks.
- The report generator (`davis_analyzer.report_generator`) produces per-stock deep-dive reports. This skill outputs a ranked list, not individual reports.

## 8. Limitations and Risk Awareness

### What this skill cannot do

| Limitation | Why | Mitigation |
|------------|-----|------------|
| No backtesting | The skill ranks stocks at a single point in time. It does not simulate historical performance, compute IC, or test factor decay. | Use a separate backtesting framework to validate factor choices before relying on this skill's output for live decisions. |
| No factor effectiveness monitoring | The skill assumes the five factor families are currently effective. It does not detect when a factor has stopped working. | Run quarterly factor effectiveness reviews (IC, ICIR, long-short spread) as a separate process. |
| No regime detection | The skill applies fixed domain weights. It does not detect whether the market is in a growth-favorable or value-favorable regime. | Use the domain weights as defaults. Override weights based on explicit macro analysis if the current regime strongly favors one factor family. |
| No transaction cost modeling | The ranked list does not account for slippage, impact cost, or commission when stocks enter or leave the list. | When acting on the ranked list, factor in turnover and liquidity constraints separately. |

### Factor failure risk

Factors stop working. The reasons fall into three categories:

1. **Structural failure:** Market microstructure changes (registration reform eroding shell value, rising foreign ownership changing pricing dynamics). These changes are permanent until another structural shift occurs.
2. **Cyclical failure:** Factor performance oscillates with market style rotation. Growth dominates in some quarters, value in others. These are temporary.
3. **Crowding failure:** When too many participants use the same factor, the signal gets priced away. Turnover-rate factors in A-shares have already shown this pattern.

The ranked list this skill produces is only as good as the factors behind it. If a factor family has been decaying, the ranking quality degrades. The agent should flag known factor decay in the output notes.

### Overfitting risk

The 0/1/2 scoring method and the hardcoded domain weights are intentionally simple to resist overfitting. Even so, the risk remains. The defense is the quality of the economic logic behind each factor choice. A factor selected because it has a clear economic story (ROE measures profitability, northbound flow tracks smart money) is more trustworthy than a factor selected purely because it had a high IC in a specific backtest period.

### Data quality dependency

The ranking is only as reliable as the input data. A-share financial data carries known quality issues: earnings management, goodwill impairment timing, related-party transactions, and restatements. The hard filter's operating-cash-flow requirement and the quality signals inside the growth bucket partially mitigate this, but they cannot fully eliminate data risk.

## 9. Companion Files

This skill directory may contain companion files for checklists and references. As of this writing, the SKILL.md is the methodology overview. Companion files (checklists, references, script templates) may be added in future updates.

### When companion files are missing

If a companion file does not yet exist, follow the methodology described in this SKILL.md directly and note which reference was unavailable. Do not block on missing companion files. The SKILL.md is self-contained for methodology purposes.

### Relationship to other skills

| Related skill | Relationship |
|---------------|--------------|
| `valuation-loss-making-targets` | Deep-dive valuation for loss-making targets. Use multi-factor screening to build the candidate list, then use the valuation skill on specific shortlisted targets. |
| `local-development-environment` | Environment setup. Run this first if `davis_analyzer` import fails or the data source is missing. |

### Skill boundaries

This skill produces a **ranked candidate list**. It does not:

- Execute trades or issue trading instructions
- Replace fundamental deep research on individual stocks
- Predict future returns or guarantee outperformance
- Manage existing portfolio positions or trigger rebalancing signals automatically

The ranked list is a starting point. A human reader takes the candidates, runs deep research, and makes the final decision.
