"""Relative valuation module for stockhot.

Implements three cross-sectional valuation methods that anchor an individual
stock's PE against market benchmarks, eliminating the illusion of "cheap"
when the entire market has re-rated higher.

Methods:
1. **相对PE溢价率** (Relative PE Premium) — stock PE / benchmark index PE,
   compared against its own 3-year ratio history.
2. **ERP法** (Equity Risk Premium) — earnings yield (1/PE) minus the risk-free
   rate (10Y treasury or Shibor 1Y proxy).
3. **PE-Band二维定位** — stock PE percentile × index PE percentile, mapped
   to one of four quadrants.

All data comes from Tushare (no scraping). Index PE via ``index_dailybasic``,
stock PE via ``daily_basic``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd
import tushare as ts
from dotenv import load_dotenv

from stockhot.core.logging import logger


# Benchmark index mapping by market board
BENCHMARK_MAP = {
    "主板": "000300.SH",   # 沪深300
    "创业板": "399006.SZ",  # 创业板指
    "科创板": "000688.SH",  # 科创50 (approx)
    "default": "000300.SH",
}

# Board classification by ts_code prefix
def _board_of(ts_code: str) -> str:
    """Return the market board name for a ts_code."""
    code = ts_code.split(".")[0]
    if ts_code.startswith("688"):
        return "科创板"
    elif ts_code.startswith("300") or ts_code.startswith("301"):
        return "创业板"
    else:
        return "主板"


def _benchmark_for(ts_code: str) -> str:
    """Return the benchmark index ts_code for a given stock."""
    return BENCHMARK_MAP.get(_board_of(ts_code), BENCHMARK_MAP["default"])


def _get_pro_api():
    """Return an authenticated Tushare pro_api, or None."""
    load_dotenv()
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        return None
    ts.set_token(token)
    return ts.pro_api()


def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def _years_ago_str(years: float) -> str:
    return (datetime.now() - timedelta(days=years * 365)).strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def fetch_stock_pe_series(pro, ts_code: str, lookback_years: float = 3) -> pd.DataFrame:
    """Daily PE(TTM) series for a stock over the lookback period."""
    df = pro.daily_basic(
        ts_code=ts_code,
        start_date=_years_ago_str(lookback_years),
        end_date=_today_str(),
        fields="ts_code,trade_date,pe,pe_ttm,pb",
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=["trade_date", "pe_ttm", "pb"])
    return df.sort_values("trade_date").reset_index(drop=True)


def fetch_index_pe_series(pro, index_code: str, lookback_years: float = 3) -> pd.DataFrame:
    """Daily PE(TTM) series for a benchmark index via index_dailybasic."""
    df = pro.index_dailybasic(
        ts_code=index_code,
        start_date=_years_ago_str(lookback_years),
        end_date=_today_str(),
        fields="ts_code,trade_date,pe_ttm,pb",
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=["trade_date", "pe_ttm"])
    return df.sort_values("trade_date").reset_index(drop=True)


def fetch_risk_free_rate(pro) -> float:
    """Approximate the 10Y risk-free rate.

    Tushare's ``shibor`` only goes up to 1Y. We use Shibor 1Y as a proxy
    and add a term premium spread (~0.7pp) to approximate the 10Y CGB yield.
    Falls back to 2.5% if data unavailable.
    """
    try:
        df = pro.shibor(
            start_date=(datetime.now() - timedelta(days=7)).strftime("%Y%m%d"),
            end_date=_today_str(),
        )
        if df is not None and not df.empty and "1y" in df.columns:
            shibor_1y = float(df.iloc[-1]["1y"])
            return round(shibor_1y + 0.7, 2)  # approximate 10Y = 1Y + term spread
    except Exception:
        pass
    return 2.5  # conservative fallback


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

@dataclass
class RelativeValuation:
    """Result of cross-sectional valuation analysis for one stock."""
    ts_code: str = ""
    name: str = ""
    board: str = ""
    benchmark: str = ""

    # Method 1: Relative PE premium
    stock_pe: float | None = None
    index_pe: float | None = None
    pe_ratio: float | None = None         # stock_pe / index_pe
    pe_ratio_pct: float | None = None     # 3-year percentile of the ratio
    pe_ratio_label: str = ""

    # Method 2: ERP
    earnings_yield: float | None = None    # 1/PE, in %
    risk_free_rate: float | None = None    # %
    erp: float | None = None               # earnings_yield - risk_free_rate
    erp_label: str = ""

    # Method 3: PE-Band
    stock_pe_pct: float | None = None      # stock PE 3-year percentile
    index_pe_pct: float | None = None      # index PE 3-year percentile
    quadrant: int = 0                      # 1-4
    quadrant_label: str = ""

    # Composite
    composite_verdict: str = ""
    signals: list[str] = field(default_factory=list)


def analyze_relative_valuation(
    pro,
    ts_code: str,
    stock_name: str = "",
    lookback_years: float = 3,
) -> RelativeValuation:
    """Run all three relative-valuation methods for a single stock.

    Args:
        pro: Authenticated Tushare pro_api.
        ts_code: Stock code, e.g. "300274.SZ".
        stock_name: Display name (optional).
        lookback_years: History window for percentile calculations.

    Returns:
        A RelativeValuation dataclass with all fields populated.
    """
    rv = RelativeValuation(ts_code=ts_code, name=stock_name)
    rv.board = _board_of(ts_code)
    rv.benchmark = _benchmark_for(ts_code)

    # Fetch data
    stock_df = fetch_stock_pe_series(pro, ts_code, lookback_years)
    index_df = fetch_index_pe_series(pro, rv.benchmark, lookback_years)

    if stock_df.empty:
        rv.signals.append("个股PE数据不可用（可能亏损或未上市足够久）")
        rv.composite_verdict = "数据不足"
        return rv

    # Latest readings
    rv.stock_pe = stock_df.iloc[-1].get("pe_ttm") or stock_df.iloc[-1].get("pe")
    if index_df.empty:
        rv.signals.append(f"基准指数({rv.benchmark})PE数据不可用")
    else:
        rv.index_pe = index_df.iloc[-1].get("pe_ttm")

    # --- Method 1: Relative PE premium ---
    if rv.stock_pe and rv.index_pe and rv.stock_pe > 0 and rv.index_pe > 0:
        rv.pe_ratio = rv.stock_pe / rv.index_pe
        # Align on trade_date and compute ratio history
        merged = pd.merge(
            stock_df[["trade_date", "pe_ttm"]],
            index_df[["trade_date", "pe_ttm"]],
            on="trade_date",
            suffixes=("_stock", "__idx"),
        )
        merged = merged.dropna()
        valid = merged[(merged["pe_ttm_stock"] > 0) & (merged["pe_ttm__idx"] > 0)]
        if not valid.empty:
            ratios = valid["pe_ttm_stock"] / valid["pe_ttm__idx"]
            rv.pe_ratio_pct = round((ratios < rv.pe_ratio).mean() * 100, 1)
            if rv.pe_ratio_pct >= 75:
                rv.pe_ratio_label = "相对溢价（贵）"
            elif rv.pe_ratio_pct >= 40:
                rv.pe_ratio_label = "相对中性"
            else:
                rv.pe_ratio_label = "相对折价（便宜）"
            rv.signals.append(
                f"相对PE溢价率 {rv.pe_ratio:.2f}x，近{lookback_years:.0f}年{rv.pe_ratio_pct}%分位 → {rv.pe_ratio_label}"
            )

    # --- Method 2: ERP ---
    rv.risk_free_rate = fetch_risk_free_rate(pro)
    if rv.stock_pe and rv.stock_pe > 0:
        rv.earnings_yield = round(100 / rv.stock_pe, 2)
        rv.erp = round(rv.earnings_yield - rv.risk_free_rate, 2)
        if rv.erp >= 3:
            rv.erp_label = "股票明显便宜（ERP>3%）"
        elif rv.erp >= 1.5:
            rv.erp_label = "吸引力合理（ERP 1.5-3%）"
        elif rv.erp >= 0:
            rv.erp_label = "吸引力偏弱（ERP 0-1.5%）"
        else:
            rv.erp_label = "股票偏贵（ERP<0，不如国债）"
        rv.signals.append(
            f"ERP {rv.erp}%（盈利收益率{rv.earnings_yield}% - 无风险利率{rv.risk_free_rate}%）→ {rv.erp_label}"
        )

    # --- Method 3: PE-Band ---
    stock_valid = stock_df[(stock_df["pe_ttm"] > 0)] if "pe_ttm" in stock_df.columns else pd.DataFrame()
    if not stock_valid.empty and rv.stock_pe and rv.stock_pe > 0:
        rv.stock_pe_pct = round((stock_valid["pe_ttm"] < rv.stock_pe).mean() * 100, 1)
    if not index_df.empty and rv.index_pe and rv.index_pe > 0:
        idx_valid = index_df[index_df["pe_ttm"] > 0]
        rv.index_pe_pct = round((idx_valid["pe_ttm"] < rv.index_pe).mean() * 100, 1)

    if rv.stock_pe_pct is not None and rv.index_pe_pct is not None:
        stock_high = rv.stock_pe_pct >= 60
        index_high = rv.index_pe_pct >= 60
        if stock_high and index_high:
            rv.quadrant = 1
            rv.quadrant_label = "①两者都贵（最危险）"
        elif stock_high and not index_high:
            rv.quadrant = 2
            rv.quadrant_label = "②个股贵/市场便宜（相对溢价）"
        elif not stock_high and not index_high:
            rv.quadrant = 3
            rv.quadrant_label = "③两者都便宜（最佳买点）"
        else:
            rv.quadrant = 4
            rv.quadrant_label = "④个股便宜/市场贵（相对折价）"
        rv.signals.append(
            f"PE-Band 第{rv.quadrant}象限：个股PE分位{rv.stock_pe_pct}% / 市场PE分位{rv.index_pe_pct}% → {rv.quadrant_label}"
        )

    # --- Composite verdict ---
    if rv.pe_ratio_pct is not None and rv.erp is not None and rv.quadrant > 0:
        # Simple heuristic: cheap if ratio pct < 40 AND erp >= 1.5 AND quadrant in {3,4}
        if rv.pe_ratio_pct < 40 and rv.erp >= 1.5 and rv.quadrant in (3, 4):
            rv.composite_verdict = "★ 相对低估（横向比较有吸引力）"
        elif rv.pe_ratio_pct >= 75 or rv.erp < 0 or rv.quadrant == 1:
            rv.composite_verdict = "★ 相对高估（横向比较偏贵）"
        else:
            rv.composite_verdict = "估值中性（横向无显著偏离）"

    return rv


def format_valuation_table(results: list[RelativeValuation]) -> str:
    """Format multiple RelativeValuation results as a markdown comparison table."""
    lines = [
        "## 横向估值对比（相对市场基准）\n",
        f"> 基准：沪深300 PE_TTM / 创业板指 PE_TTM / 科创50 PE_TTM，无风险利率≈Shibor1Y+0.7pp\n",
        "| 标的 | 板块 | 个股PE | 溢价率 | 溢价分位 | ERP | 象限 | 综合判断 |",
        "|------|:---:|:---:|:---:|:---:|:---:|:---:|------|",
    ]
    for rv in results:
        pe = f"{rv.stock_pe:.1f}" if rv.stock_pe else "N/A"
        ratio = f"{rv.pe_ratio:.2f}x" if rv.pe_ratio else "N/A"
        ratio_pct = f"{rv.pe_ratio_pct}%" if rv.pe_ratio_pct is not None else "N/A"
        erp = f"{rv.erp}%" if rv.erp is not None else "N/A"
        q = f"Q{rv.quadrant}" if rv.quadrant else "N/A"
        verdict = rv.composite_verdict or "-"
        name = rv.name or rv.ts_code
        lines.append(f"| {name} | {rv.board} | {pe} | {ratio} | {ratio_pct} | {erp} | {q} | {verdict} |")

    # Add signal detail for stocks flagged as under/overvalued
    flagged = [r for r in results if "低估" in r.composite_verdict or "高估" in r.composite_verdict]
    if flagged:
        lines.append("\n### 信号详情\n")
        for rv in flagged:
            lines.append(f"**{rv.name or rv.ts_code}** ({rv.composite_verdict}):")
            for s in rv.signals:
                lines.append(f"- {s}")
            lines.append("")

    return "\n".join(lines)
