"""Financial data fetching and parsing for Davis Double Play analysis."""

from datetime import datetime

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta
from loguru import logger

from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import FinancialData

_MERGE_KEY = "end_date"


def _compute_date_range(periods: int) -> tuple[str, str]:
    today = datetime.now()
    start = today - relativedelta(months=periods * 3)
    return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")


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
) -> list[FinancialData]:
    """Fetch and merge financial data for a single stock.

    Args:
        client: TushareClient instance.
        ts_code: Stock code, e.g. '000001.SZ'.
        periods: Number of quarters to fetch.

    Returns:
        List of FinancialData sorted by report_period descending.
    """
    start_date, end_date = _compute_date_range(periods)

    income_df = client.get_income(ts_code, start_date, end_date)
    balance_df = client.get_balancesheet(ts_code, start_date, end_date)
    cashflow_df = client.get_cashflow(ts_code, start_date, end_date)
    fina_df = client.get_fina_indicator(ts_code, start_date, end_date)

    merged = _merge_financial_dfs(income_df, balance_df, cashflow_df, fina_df)

    if merged.empty:
        logger.warning("No financial data returned for {}", ts_code)
        return []

    merged = merged.rename(columns={_MERGE_KEY: "report_period"})

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
        )
        results.append(fd)

    results.sort(key=lambda x: x.report_period, reverse=True)
    return results


def fetch_batch_financial(
    client: TushareClient,
    ts_codes: list[str],
    periods: int = 8,
) -> dict[str, list[FinancialData]]:
    """Fetch financial data for multiple stocks.

    Args:
        client: TushareClient instance.
        ts_codes: List of stock codes.
        periods: Number of quarters per stock.

    Returns:
        Dict mapping ts_code -> list of FinancialData. Failed stocks are skipped.
    """
    batch: dict[str, list[FinancialData]] = {}

    for code in ts_codes:
        try:
            data = fetch_financial_data(client, code, periods)
            batch[code] = data
        except Exception as exc:
            logger.error("Failed to fetch financial data for {}: {}", code, exc)
            continue

    return batch
