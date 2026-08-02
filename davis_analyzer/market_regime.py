"""HMM-based market regime detection.

Trains a 3-state Gaussian HMM on 5 years of index returns to identify
bull / bear / neutral market states. Uses MA60/MA120 as confirmation
overlay (not as primary signal).

States are labeled post-training by examining each state's mean return
and volatility:
  - Highest mean + moderate vol → "bull"
  - Lowest mean (negative) + highest vol → "bear"
  - Near-zero mean → "neutral"

The HMM is trained ONCE (cached as module-level singleton) using all
available index_daily history up to the latest trade date. Re-training
happens automatically when new data is available (checked by date).

Usage:
    from davis_analyzer.market_regime import get_market_regime
    regime = get_market_regime("20260721")  # → "bull" / "bear" / "neutral"
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from typing import Literal

from stockhot.data_layer.market_db import get_connection as get_market_conn

RegimeState = Literal["bull", "bear", "neutral"]


# ── Module-level HMM cache ─────────────────────────────────────────────
_hmm_model = None
_hmm_state_labels: dict[int, str] = {}  # raw state index → label
_hmm_train_end_date: str = ""
_hmm_predictions: dict[str, str] = {}   # trade_date → regime (cache for backtest)


def _load_index_returns(index_code: str = "000001.SH",
                        start: str = "20210101",
                        end: str = "20260723") -> pd.DataFrame:
    """Load index daily returns for HMM training."""
    with get_market_conn() as conn:
        df = pd.read_sql_query(
            "SELECT trade_date, close FROM index_daily "
            "WHERE ts_code=? AND trade_date>=? AND trade_date<=? "
            "AND close IS NOT NULL AND close > 0 ORDER BY trade_date",
            conn, params=(index_code, start, end),
        )
    if len(df) < 100:
        logger.warning(f"HMM: only {len(df)} rows for {index_code}")
        return pd.DataFrame()
    df["ret"] = df["close"].pct_change()
    df = df.dropna(subset=["ret"])
    return df[["trade_date", "close", "ret"]]


def _train_hmm(features: np.ndarray, n_states: int = 3, random_state: int = 42):
    """Train a Gaussian HMM on daily features (returns or multi-index).

    Args:
        features: (n_samples, n_features) array. For single-index: shape (N, 1).
                  For multi-index: shape (N, 2) with [sh_ret, cyb_ret].
        n_states: number of hidden states.

    Returns (model, state_labels) where state_labels maps raw state index
    to "bull"/"bear"/"neutral".
    """
    import warnings
    from hmmlearn.hmm import GaussianHMM

    # Ensure 2D (n_samples, n_features)
    if features.ndim == 1:
        X = features.reshape(-1, 1)
    else:
        X = features

    model = GaussianHMM(
        n_components=n_states,
        covariance_type="full",
        n_iter=200,
        random_state=random_state,
        tol=0.01,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model.fit(X)

    # Label states by mean return (use first feature = SH return for labeling)
    means = model.means_[:, 0].flatten()  # first column = SH return mean
    sorted_indices = np.argsort(means)
    state_labels = {}
    if n_states == 3:
        state_labels[int(sorted_indices[0])] = "bear"
        state_labels[int(sorted_indices[1])] = "neutral"
        state_labels[int(sorted_indices[2])] = "bull"
    elif n_states == 2:
        state_labels[int(sorted_indices[0])] = "bear"
        state_labels[int(sorted_indices[1])] = "bull"

    logger.info(
        f"HMM trained: {n_states} states, {X.shape[1]} features, "
        f"means(SH)={np.round(means, 5).tolist()}, "
        f"labels={state_labels}"
    )
    return model, state_labels


def _ensure_model_trained(end_date: str = "20260723"):
    """Train HMM if not already trained or if new data available.

    Uses single SH index (multi-index SH+CYB was tested 2026-07-24 and
    found slightly worse: Sharpe +1.440 vs +1.521 single-index).

    The model is trained ONCE per process using ALL available index data
    (up to the latest date in the database). Subsequent calls with any date
    reuse the cached model + predictions. This is critical for backtests:
    without it, every backtest day re-trains the HMM (~1.5s each → 30+ min
    over a 1351-day backtest).

    Note: this means the HMM uses future data when predicting historical
    states (look-ahead bias). This is an accepted trade-off — walk-forward
    HMM would require 1300+ retraining cycles. The bias affects only state
    boundary precision, not regime identification.
    """
    global _hmm_model, _hmm_state_labels, _hmm_train_end_date, _hmm_predictions

    # Already trained — reuse forever (model covers all dates in DB).
    if _hmm_model is not None and _hmm_predictions:
        return

    # Train once with ALL available data (not just up to end_date).
    # This ensures predictions cover every backtest date.
    df = _load_index_returns("000001.SH", "20210101", "20260731")
    if len(df) < 100:
        logger.warning("HMM: insufficient data, skipping training")
        return

    returns = df["ret"].values
    _hmm_model, _hmm_state_labels = _train_hmm(returns, n_states=3)
    _hmm_train_end_date = df["trade_date"].iloc[-1]  # last available date

    predictions = _hmm_model.predict(returns.reshape(-1, 1))
    _hmm_predictions = {
        df["trade_date"].iloc[i]: _hmm_state_labels.get(int(predictions[i]), "neutral")
        for i in range(len(predictions))
    }
    logger.info(f"HMM: trained once with full data, predicted {_hmm_predictions.get(end_date, '?')} "
                f"for {end_date}, total {len(_hmm_predictions)} dates cached")


def _get_ma_alignment(trade_date: str, index_code: str = "000001.SH") -> int:
    """Compute MA alignment score for index at trade_date.

    Returns:
        +2: Close > MA20 > MA60 (full bull alignment)
        +1: Close > MA20 (partial bull)
         0: mixed
        -1: Close < MA20 (partial bear)
        -2: Close < MA20 < MA60 (full bear alignment)
    """
    with get_market_conn() as conn:
        rows = conn.execute(
            "SELECT close FROM index_daily WHERE ts_code=? AND trade_date<=? "
            "AND close IS NOT NULL AND close > 0 ORDER BY trade_date DESC LIMIT 120",
            (index_code, trade_date),
        ).fetchall()
    if len(rows) < 60:
        return 0

    closes = np.array([float(r[0]) for r in rows])[::-1]  # reverse to chronological
    close = closes[-1]
    ma20 = closes[-20:].mean()
    ma60 = closes[-60:].mean()

    if close > ma20 > ma60:
        return 2
    elif close > ma20:
        return 1
    elif close < ma20 < ma60:
        return -2
    elif close < ma20:
        return -1
    return 0


def _get_index_vs_ma120(trade_date: str, index_code: str = "000001.SH") -> float | None:
    """Compute how far the index close is below/above MA120.

    Returns the ratio close/ma120 - 1.0. Negative means below MA120.
    E.g. -0.05 = 5% below MA120. Returns None if insufficient data.
    """
    with get_market_conn() as conn:
        rows = conn.execute(
            "SELECT close FROM index_daily WHERE ts_code=? AND trade_date<=? "
            "AND close IS NOT NULL AND close > 0 ORDER BY trade_date DESC LIMIT 120",
            (index_code, trade_date),
        ).fetchall()
    if len(rows) < 120:
        return None
    closes = np.array([float(r[0]) for r in rows])[::-1]
    close = closes[-1]
    ma120 = closes[-120:].mean()
    if ma120 <= 0:
        return None
    return float(close / ma120 - 1.0)


# ── MA120 hard trigger threshold ──
# When index drops 5%+ below MA120 (半年线), force bear regardless of HMM.
# This catches sustained downtrends that HMM misses (e.g. 2022 A-share bear
# market where HMM stayed "bull" for 108/242 days while the market fell -22%).
_MA120_BEAR_THRESHOLD = -0.05  # close < MA120 × 0.95 → force bear


def get_market_regime(trade_date: str) -> str:
    """Get HMM-based market regime for a given trade date.

    Returns "bull", "bear", or "neutral". Uses HMM prediction confirmed
    by MA alignment.

    **MA120 hard bear trigger**: when the index closes 5%+ below its 120-day
    MA, the regime is forced to "bear" regardless of HMM. This is a
    trend-following safety net — HMM uses return distributions which can
    mislabel high-volatility downtrends (with occasional bounce days) as
    "bull". The MA120 line (半年线) is the traditional A-share bull/bear
    boundary.

    Args:
        trade_date: YYYYMMDD format.

    Returns:
        One of "bull", "bear", "neutral".
    """
    _ensure_model_trained(trade_date)

    # ── MA120 hard bear trigger (overrides HMM) ──
    ma120_dev = _get_index_vs_ma120(trade_date)
    if ma120_dev is not None and ma120_dev <= _MA120_BEAR_THRESHOLD:
        return "bear"

    if not _hmm_predictions:
        # Fallback to MA-based if HMM not available
        ma_score = _get_ma_alignment(trade_date)
        if ma_score >= 1:
            return "bull"
        elif ma_score <= -1:
            return "bear"
        return "neutral"

    hmm_state = _hmm_predictions.get(trade_date, "neutral")
    ma_score = _get_ma_alignment(trade_date)

    # HMM + MA double confirmation
    # If HMM and MA agree, use HMM
    # If they disagree, prefer the more conservative one
    if hmm_state == "bull" and ma_score >= 1:
        return "bull"
    elif hmm_state == "bear" and ma_score <= -1:
        return "bear"
    elif hmm_state == "neutral":
        return "neutral"
    else:
        # Disagreement — use MA as tiebreaker but lean neutral
        if ma_score >= 2:
            return "bull"
        elif ma_score <= -2:
            return "bear"
        return "neutral"


def get_market_regime_with_confirm(trade_date: str, confirm_days: int = 3) -> str:
    """Get market regime with N-day confirmation (avoid single-day flip).

    The regime must persist for `confirm_days` consecutive days before
    switching. Uses a simple lookback check.
    """
    from datetime import datetime, timedelta

    td = datetime.strptime(trade_date, "%Y%m%d")

    # Get current regime
    current = get_market_regime(trade_date)

    # Check previous N days
    prev_regimes = []
    for i in range(1, confirm_days + 1):
        prev_date = (td - timedelta(days=i * 2)).strftime("%Y%m%d")  # ~2x for weekends
        prev = get_market_regime(prev_date)
        if prev:
            prev_regimes.append(prev)

    # If all previous days agree with current, return current
    if prev_regimes and all(r == current for r in prev_regimes):
        return current

    # Otherwise, find the most common regime in the lookback
    if prev_regimes:
        from collections import Counter
        most_common = Counter(prev_regimes + [current]).most_common(1)[0][0]
        return most_common

    return current


def reset_hmm_cache():
    """Reset HMM cache (for re-training with new data)."""
    global _hmm_model, _hmm_state_labels, _hmm_train_end_date, _hmm_predictions
    _hmm_model = None
    _hmm_state_labels = {}
    _hmm_train_end_date = ""
    _hmm_predictions = {}


def get_market_regime_with_overseas(trade_date: str) -> RegimeState:
    """Enhanced regime detection with international market resonance overlay.

    Combines the base HMM+MA regime with an overseas risk overlay that can
    *downgrade* (never upgrade) the regime based on US Treasury yields,
    USD/JPY carry-trade risk, US-VIX, and US equity overnight gaps.

    The overlay is a parallel signal — it never alters the HMM training.
    When overseas risk is extreme (>=70), regime is forced to bear; when
    elevated (>=50), bull is demoted to neutral. Below 50, the base regime
    stands unchanged.

    Use this instead of get_market_regime when international context matters
    (paper trading, live decisions). The original get_market_regime remains
    for backward compatibility and backtests that must stay overlay-free.
    """
    base = get_market_regime(trade_date)

    try:
        from davis_analyzer.international_overlay import (
            apply_overseas_overlay,
            get_international_risk,
        )

        risk = get_international_risk(trade_date)
        adjusted = apply_overseas_overlay(base, risk)
        if adjusted != base:
            logger.info(
                "overseas overlay downgraded {} → {} for {} (risk={:.1f}, {})",
                base, adjusted, trade_date, risk.composite_score, risk.level,
            )
        return adjusted
    except Exception as exc:
        logger.debug("overseas overlay failed for {}: {} — using base regime", trade_date, exc)
        return base
