"""Technical data-source wrappers for the advisor.

Wraps ``composite_technical_score`` and ``stock_zh_a_spot_em`` into
:class:`UnifiedSignal` / structured dict outputs so the aggregator (T4)
never touches raw indicator functions.

These wrappers activate previously dead code: ``composite_technical_score``
had zero production callers before this module.
"""

from __future__ import annotations

from datetime import date

import akshare as ak
import pandas as pd

from stockhot.advisor.types import UnifiedSignal
from stockhot.core.rate_limiter import safe_akshare_call
from stockhot.technical_analyzer.scoring import composite_technical_score


def _compute_data_age(data_timestamp: str | None) -> int | None:
    if data_timestamp is None:
        return None
    try:
        parsed = date.fromisoformat(data_timestamp[:10])
        return (date.today() - parsed).days
    except (ValueError, TypeError):
        return None


def fetch_technical_signal(code: str, ohlcv_df: pd.DataFrame) -> UnifiedSignal:
    if ohlcv_df is None or ohlcv_df.empty:
        return UnifiedSignal(
            name="technical",
            value=50.0,
            polarity="higher_is_better",
            data_timestamp=None,
            data_age_days=None,
            source="technical_analyzer",
            details={"error": "empty_ohlcv"},
        )

    result = composite_technical_score(ohlcv_df)

    ts_str = str(ohlcv_df.index[-1]) if len(ohlcv_df.index) > 0 else None
    ts_short = ts_str[:10] if ts_str else None

    return UnifiedSignal(
        name="technical",
        value=result["score"],
        polarity="higher_is_better",
        data_timestamp=ts_short,
        data_age_days=_compute_data_age(ts_short),
        source="technical_analyzer",
        details={
            "state": result["state"],
            "signals": result["signals"],
        },
    )


def fetch_realtime_price(code: str) -> dict:
    df = safe_akshare_call(ak.stock_zh_a_spot_em)

    if df is None or df.empty:
        return {
            "code": code,
            "current_price": None,
            "change_pct": None,
            "volume": None,
            "timestamp": date.today().isoformat(),
        }

    row = df[df["代码"] == code]
    if row.empty:
        return {
            "code": code,
            "current_price": None,
            "change_pct": None,
            "volume": None,
            "timestamp": date.today().isoformat(),
        }

    r = row.iloc[0]
    return {
        "code": code,
        "current_price": float(r["最新价"]) if pd.notna(r.get("最新价")) else None,
        "change_pct": float(r["涨跌幅"]) if pd.notna(r.get("涨跌幅")) else None,
        "volume": float(r["成交量"]) if pd.notna(r.get("成交量")) else None,
        "timestamp": date.today().isoformat(),
    }
