"""Shared utility functions for StockHot-CN."""

from typing import Any


def safe_float(val: Any, default: float = 0.0) -> float:
    """Convert a value to float, stripping common suffixes.

    Handles int/float, strings with commas/%/亿/whitespace,
    and sentinel values like '-', '', 'nan', None.
    """
    if isinstance(val, (int, float)):
        return float(val)
    if val is None:
        return default
    if isinstance(val, str):
        cleaned = val.replace(",", "").replace("%", "").replace("亿", "").strip()
        if not cleaned or cleaned == "-" or cleaned.lower() == "nan":
            return default
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return default
    return default


def safe_optional_float(val: Any) -> float | None:
    """Convert a value to float or None for genuinely missing values."""
    if val in (None, "", "-"):
        return None
    if isinstance(val, str):
        cleaned = val.replace(",", "").replace("%", "").strip()
        if not cleaned or cleaned.lower() == "nan":
            return None
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return None
    if isinstance(val, (int, float)):
        return float(val)
    return None


def safe_text(val: Any) -> str:
    """Convert a value to a stripped string, returning '' for None/nan."""
    if val is None:
        return ""
    text = str(val).strip()
    return "" if text.lower() == "nan" else text


def fund_flow_scope_label(item: dict | None) -> str:
    """Return a short label describing the fund-flow item's scope."""
    if not item:
        return ""
    source = item.get("source")
    category = item.get("category")
    if source == "ths" and category == "industry":
        return "THS行业"
    if source == "ths" and category == "concept":
        return "THS概念"
    return ""


def fund_flow_direction_phrase(item: dict) -> str:
    """Return a human-readable phrase describing net fund flow direction."""
    amount = safe_float(item.get("net_inflow"))
    if amount < 0:
        return f"净流出约{abs(amount):.2f}亿"
    return f"净流入约{amount:.2f}亿"
