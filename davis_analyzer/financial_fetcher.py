"""Financial data fetching and parsing for Davis Double Play analysis."""

from datetime import date

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta
from loguru import logger

from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import FinancialData

_MERGE_KEY = "end_date"


def _compute_date_range(periods: int, as_of: date | None = None) -> tuple[str, str]:
    """Return the ``(start, end)`` date window for *periods* quarters.

    When *as_of* is None (the live pipeline), the window is anchored to today.
    When *as_of* is a historical date (the backtest engine), the window is
    anchored to that date so the fetch is point-in-time correct.
    """
    ref = as_of if as_of is not None else date.today()
    start = ref - relativedelta(months=periods * 3)
    return start.strftime("%Y%m%d"), ref.strftime("%Y%m%d")


def _safe_float(value) -> float | None:
    """Coerce *value* to float, returning ``0.0`` for non-finite numbers.

    Returns ``None`` **only** when *value* is itself ``None`` — used for YoY
    growth fields where ``None`` is a sentinel meaning "no prior-year base,
    skip this period" (see :func:`_extract_yoy_series`). All other parse
    failures fall back to ``0.0`` so downstream division never hits a
    ``TypeError`` on ``None``.
    """
    if value is None:
        return None
    try:
        f = float(value)
        if np.isnan(f) or np.isinf(f):
            return 0.0
        return f
    except (ValueError, TypeError):
        return 0.0


def _calculate_yoy_growth(df: pd.DataFrame, col: str) -> pd.Series:
    result = pd.Series([None] * len(df), index=df.index, dtype=object)

    if col not in df.columns or "report_period" not in df.columns:
        return result

    sorted_idx = df.sort_values("report_period").index
    sorted_vals = df.loc[sorted_idx, col].astype(float)

    prev_vals = sorted_vals.shift(4)

    has_base = (prev_vals.notna()) & (prev_vals != 0)
    growth = (sorted_vals - prev_vals) / prev_vals

    for idx in sorted_idx:
        if has_base.loc[idx]:
            g = float(growth.loc[idx])
            if np.isfinite(g):
                result.loc[idx] = g

    return result


def _merge_financial_dfs(
    income: pd.DataFrame,
    balancesheet: pd.DataFrame,
    cashflow: pd.DataFrame,
    fina_indicator: pd.DataFrame,
) -> pd.DataFrame:
    merge_on = ["ts_code", _MERGE_KEY]

    def _safe_merge(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
        if right.empty:
            return left
        right = right.drop_duplicates(subset=merge_on)
        # ``ann_date`` is returned by every financial endpoint but is not a
        # merge key.  Drop the right-side copy before merging so pandas does
        # not raise on duplicate columns (ann_date_x / ann_date_y).  The
        # left-side value is authoritative and identical for the same end_date.
        if "ann_date" in right.columns and "ann_date" in left.columns:
            right = right.drop(columns=["ann_date"])
        if left.empty:
            return right
        return pd.merge(left, right, on=merge_on, how="outer")

    df = income.copy()
    if not df.empty:
        df = df.drop_duplicates(subset=merge_on)

    df = _safe_merge(df, balancesheet)
    df = _safe_merge(df, cashflow)
    df = _safe_merge(df, fina_indicator)

    return df


def fetch_financial_data(
    client: TushareClient,
    ts_code: str,
    periods: int = 8,
    as_of: date | None = None,
) -> list[FinancialData]:
    """Fetch and merge financial data for a single stock.

    Args:
        client: TushareClient instance.
        ts_code: Stock code, e.g. '000001.SZ'.
        periods: Number of quarters to fetch.
        as_of: Anchor date for the look-back window **and** the point-in-time
            disclosure filter.  When ``None`` (the live pipeline) today is used
            and no disclosure filtering is applied.  When a historical date is
            passed (the backtest engine), only rows whose ``ann_date <= as_of``
            survive — this prevents the look-ahead bias of using a report that
            was filed but not yet disclosed as of the backtest date.

    Returns:
        List of FinancialData sorted by (ann_date, report_period) descending,
        so ``result[0]`` is the most recently *disclosed* quarter as of *as_of*.
    """
    start_date, end_date = _compute_date_range(periods, as_of)

    income_df = client.get_income(ts_code, start_date, end_date)
    balance_df = client.get_balancesheet(ts_code, start_date, end_date)
    cashflow_df = client.get_cashflow(ts_code, start_date, end_date)
    fina_df = client.get_fina_indicator(ts_code, start_date, end_date)

    merged = _merge_financial_dfs(income_df, balance_df, cashflow_df, fina_df)

    if merged.empty:
        logger.warning("No financial data returned for {}", ts_code)
        return []

    merged = merged.rename(columns={_MERGE_KEY: "report_period"})

    # ── Point-in-time disclosure filter ──
    # Keep only rows whose disclosure date (ann_date) is on or before *as_of*.
    # Rows with no ann_date (older cached payloads) are retained — the live
    # path passes as_of=None and skips this block entirely.
    if as_of is not None and "ann_date" in merged.columns:
        as_of_str = as_of.strftime("%Y%m%d")
        ann = merged["ann_date"]
        merged = merged[ann.isna() | (ann.astype(str).str.slice(0, 8) <= as_of_str)]

    if merged.empty:
        logger.debug("No disclosed financial data for {} as of {}", ts_code, as_of)
        return []

    merged["yoy_revenue_growth"] = _calculate_yoy_growth(merged, "total_revenue")
    merged["yoy_profit_growth"] = _calculate_yoy_growth(merged, "n_income")

    results: list[FinancialData] = []
    for _, row in merged.iterrows():
        gross_margin = _safe_float(row.get("grossprofit_margin"))
        rd_exp = _safe_float(row.get("rd_exp"))
        revenue_val = _safe_float(row.get("total_revenue")) or 0.0
        # Derive gross profit (元) from the margin % when available.
        gross_profit = None
        if gross_margin is not None and revenue_val:
            gross_profit = gross_margin / 100.0 * revenue_val
        # ann_date may be missing from legacy cached payloads; default None.
        ann_raw = row.get("ann_date")
        ann_date = str(ann_raw)[:8] if ann_raw is not None and str(ann_raw) != "nan" else None
        fd = FinancialData(
            ts_code=ts_code,
            report_period=str(row.get("report_period", "")),
            revenue=_safe_float(row.get("total_revenue")),
            net_profit=_safe_float(row.get("n_income")),
            eps=_safe_float(row.get("eps")),
            roe=_safe_float(row.get("roe")),
            operating_cf=_safe_float(row.get("n_cashflow_act")),
            total_debt=_safe_float(row.get("total_liab")),
            total_assets=_safe_float(row.get("total_assets")),
            yoy_revenue_growth=_safe_float(row.get("yoy_revenue_growth")),
            yoy_profit_growth=_safe_float(row.get("yoy_profit_growth")),
            gross_profit=gross_profit,
            grossprofit_margin=gross_margin,
            rd_exp=rd_exp,
            ann_date=ann_date,
        )
        results.append(fd)

    # Sort by (ann_date, report_period) descending so result[0] is the most
    # recently *disclosed* quarter.  ann_date is a more accurate "as of when
    # was this public" signal than report_period; the two usually agree, but
    # amended/supplementary filings can make report_period misleading.
    results.sort(key=lambda x: (x.ann_date or "", x.report_period), reverse=True)
    return results


def fetch_batch_financial(
    client: TushareClient,
    ts_codes: list[str],
    periods: int = 8,
    as_of: date | None = None,
) -> dict[str, list[FinancialData]]:
    """Fetch financial data for multiple stocks.

    Args:
        client: TushareClient instance.
        ts_codes: List of stock codes.
        periods: Number of quarters per stock.
        as_of: Point-in-time anchor (see :func:`fetch_financial_data`).

    Returns:
        Dict mapping ts_code -> list of FinancialData. Failed stocks are skipped.
    """
    batch: dict[str, list[FinancialData]] = {}

    for code in ts_codes:
        try:
            data = fetch_financial_data(client, code, periods, as_of=as_of)
            batch[code] = data
        except Exception as exc:
            logger.error("Failed to fetch financial data for {}: {}", code, exc)
            continue

    return batch
