"""Technical data-source wrappers for the advisor.

Wraps ``composite_technical_score`` and ``stock_zh_a_spot_em`` into
:class:`UnifiedSignal` / structured dict outputs so the aggregator (T4)
never touches raw indicator functions.

These wrappers activate previously dead code: ``composite_technical_score``
and ``support_resistance`` / ``volume_price_analysis`` had zero production
callers before this module.
"""

from __future__ import annotations

from datetime import date, timedelta

import akshare as ak
import pandas as pd

from stockhot.advisor.types import UnifiedSignal
from stockhot.core.rate_limiter import safe_akshare_call
from stockhot.technical_analyzer.data_loader import fetch_ohlcv
from stockhot.technical_analyzer.indicators import (
    support_resistance,
    volume_price_analysis,
)


def _to_ts_code_adv(code: str) -> str:
    """6 位纯代码 → Tushare ts_code（推断交易所后缀）。"""
    if code.startswith(("60", "68", "90", "11", "13")):
        return f"{code}.SH"
    elif code.startswith(("43", "83", "87", "88")):
        return f"{code}.BJ"
    return f"{code}.SZ"
from stockhot.technical_analyzer.scoring import composite_technical_score

# How many calendar days of OHLCV history to fetch for indicator computation.
# 90 calendar days ≈ 60 trading days, enough for MA20 + 60-day support lookup.
_OHLCV_LOOKBACK_DAYS = 90


def _compute_data_age(data_timestamp: str | None) -> int | None:
    if data_timestamp is None:
        return None
    try:
        parsed = date.fromisoformat(data_timestamp[:10])
        return (date.today() - parsed).days
    except (ValueError, TypeError):
        return None


def fetch_ohlcv_for_advisor(code: str, days: int = _OHLCV_LOOKBACK_DAYS) -> pd.DataFrame:
    """Fetch OHLCV for advisor consumption.

    Thin wrapper around ``technical_analyzer.data_loader.fetch_ohlcv`` that
    derives date bounds from today. Returns an empty DataFrame on any failure
    (network, akshare error, malformed payload) — callers (notably
    ``fetch_technical_signal``) degrade to a neutral 50.0 score in that case.

    Reused rather than reimplemented — this module never touches akshare's
    OHLCV endpoint directly.
    """
    end = date.today()
    start = end - timedelta(days=days)
    try:
        return fetch_ohlcv(code, start.isoformat(), end.isoformat())
    except Exception:
        return pd.DataFrame()


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

    # Populate support/resistance + volume context so prompts and the T-trade
    # detector receive decision-relevant fields instead of "N/A". Key names
    # align with what recommendation_engine._build_context reads from
    # technical.details (support_levels / resistance_levels / volume_ratio /
    # volume_trend). Wrapped per-indicator so one failing indicator does not
    # blank out the whole signal.
    details: dict = {
        "state": result["state"],
        "signals": result["signals"],
    }
    try:
        sr = support_resistance(ohlcv_df)
        details["support_levels"] = sr.get("support", [])
        details["resistance_levels"] = sr.get("resistance", [])
    except Exception:
        details["support_levels"] = []
        details["resistance_levels"] = []

    try:
        vp = volume_price_analysis(ohlcv_df)
        details["volume_ratio"] = vp.get("volume_ratio", 0.0)
        details["volume_trend"] = vp.get("volume_trend", "flat")
    except Exception:
        details["volume_ratio"] = 0.0
        details["volume_trend"] = "flat"

    return UnifiedSignal(
        name="technical",
        value=result["score"],
        polarity="higher_is_better",
        data_timestamp=ts_short,
        data_age_days=_compute_data_age(ts_short),
        source="technical_analyzer",
        details=details,
    )


def fetch_realtime_price(code: str) -> dict:
    """获取个股最新价。

    2026-07-07 调整：Tushare ``daily_basic`` 优先（最新交易日收盘），AKShare ``stock_zh_a_spot_em`` 兜底。
    注意：Tushare daily_basic 是收盘数据（非盘中实时），盘后/盘前场景够用；
    若需盘中实时价，AKShare spot 路径在盘中调用时仍有效。
    """
    # Tushare 优先
    from stockhot.core.tushare_client_safe import safe_tushare_call

    ts_code = code if "." in code else _to_ts_code_adv(code)
    df_ts = safe_tushare_call("daily_basic", ts_code=ts_code, limit=1)
    if df_ts is not None and not df_ts.empty:
        r = df_ts.iloc[0]
        return {
            "code": code,
            "current_price": float(r["close"]) if pd.notna(r.get("close")) else None,
            "change_pct": float(r.get("pct_chg")) if pd.notna(r.get("pct_chg")) else None,
            "volume": float(r.get("vol")) if pd.notna(r.get("vol")) else None,
            "timestamp": str(r.get("trade_date", date.today().isoformat())),
        }

    # AKShare 兜底
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
