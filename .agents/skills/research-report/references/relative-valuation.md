# Relative Valuation Cross-Sectional Methodology

> When to use: every individual-stock report MUST include a **relative valuation section** in addition to the stock's own historical PE/PB percentile. This file defines the methodology and the exact code to call.

## Core Problem

A stock's PE may sit at its own historical median (e.g., 50th percentile), but if the market index is also at a 90th-percentile high, the stock has been **systematically re-rated upward** and may actually be expensive relative to its benchmark. Absolute PE percentile alone is insufficient — you must anchor against the market.

## Three Methods (all computed by `stockhot/valuation/`)

### Method 1: Relative PE Premium Ratio

```
relative_pe_ratio = stock_pe / benchmark_index_pe
```

**Benchmark auto-selection** (`stockhot.valuation._benchmark_for`):
- 主板 stock → 沪深300 (000300.SH)
- 创业板 stock → 创业板指 (399006.SZ)
- 科创板 stock → 科创50 (000688.SH)

**Judgement**: use the **3-year percentile of the ratio** (not the absolute PE percentile).
- ratio percentile < 40% → 相对折价（便宜）
- 40-75% → 相对中性
- > 75% → 相对溢价（贵）

### Method 2: ERP (Equity Risk Premium)

```
ERP = earnings_yield - risk_free_rate
    = (1 / stock_pe) × 100 - (Shibor_1Y + 0.7)
```

**Judgement**:
- ERP ≥ 3% → 股票明显便宜
- 1.5-3% → 吸引力合理
- 0-1.5% → 吸引力偏弱
- < 0 → 股票偏贵（不如国债）

### Method 3: PE-Band Quadrant

| Quadrant | Stock PE pct | Market PE pct | Meaning |
|:--------:|:----------:|:------------:|---------|
| **Q1** | High | High | 两者都贵（最危险） |
| **Q2** | High | Low | 个股贵/市场便宜 |
| **Q3** | Low | Low | **最佳买点** |
| **Q4** | Low | High | 个股便宜/市场贵（相对折价） |

Threshold for "High": ≥ 60th percentile.

## Code to Call (mandatory in every stock report)

```python
import tushare as ts
from stockhot.valuation import analyze_relative_valuation

ts.set_token(os.environ["TUSHARE_TOKEN"])
pro = ts.pro_api()

rv = analyze_relative_valuation(pro, "300274.SZ", "阳光电源")
# rv.pe_ratio, rv.pe_ratio_pct, rv.erp, rv.quadrant, rv.quadrant_label,
# rv.composite_verdict, rv.signals
```

For batch comparison:
```python
from stockhot.valuation import format_valuation_table
md = format_valuation_table([rv1, rv2, rv3, ...])
```

## Report Section Template

Every individual-stock report must include this section (in the valuation chapter or as a new subsection):

```markdown
### 横向市场估值锚定（相对基准）

> 基准：{沪深300/创业板指/科创50} PE_TTM={X}（{Y}%分位），无风险利率≈{Z}%

| 方法 | 结果 | 判断 |
|------|------|------|
| 相对PE溢价率 | {ratio}x（{pct}%分位） | {相对折价/中性/溢价} |
| ERP | {erp}% | {便宜/合理/偏弱/偏贵} |
| PE-Band象限 | Q{n} | {象限含义} |
| **综合** | — | **{相对低估/中性/高估}** |

{signals bullet list}
```

## Verdict Logic

```
相对低估: ratio_pct < 40% AND erp >= 1.5% AND quadrant in {3,4}
相对高估: ratio_pct >= 75% OR erp < 0 OR quadrant == 1
估值中性: otherwise
```
