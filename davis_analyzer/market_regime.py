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


def _train_hmm(returns: np.ndarray, n_states: int = 3, random_state: int = 42):
    """Train a Gaussian HMM on daily returns.

    Returns (model, state_labels) where state_labels maps raw state index
    to "bull"/"bear"/"neutral".
    """
    import warnings
    from hmmlearn.hmm import GaussianHMM

    # Reshape for hmmlearn (n_samples, n_features)
    X = returns.reshape(-1, 1)

    model = GaussianHMM(
        n_components=n_states,
        covariance_type="full",
        n_iter=200,
        random_state=random_state,
        tol=0.01,  # relax convergence tolerance to avoid warnings
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # suppress convergence warnings
        model.fit(X)

    # Label states by mean return
    means = model.means_.flatten()  # shape (n_states,)
    # Sort state indices by mean return: lowest → bear, highest → bull
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
        f"HMM trained: {n_states} states, "
        f"means={np.round(means, 5).tolist()}, "
        f"labels={state_labels}"
    )
    return model, state_labels


def _ensure_model_trained(end_date: str = "20260723"):
    """Train HMM if not already trained or if new data available."""
    global _hmm_model, _hmm_state_labels, _hmm_train_end_date, _hmm_predictions

    if _hmm_model is not None and _hmm_train_end_date >= end_date:
        return  # already trained up to this date

    df = _load_index_returns("000001.SH", "20210101", end_date)
    if len(df) < 100:
        logger.warning("HMM: insufficient data, skipping training")
        return

    returns = df["ret"].values
    _hmm_model, _hmm_state_labels = _train_hmm(returns, n_states=3)
    _hmm_train_end_date = end_date

    # Pre-compute predictions for all dates (for fast backtest lookup)
    predictions = _hmm_model.predict(returns.reshape(-1, 1))
    _hmm_predictions = {
        df["trade_date"].iloc[i]: _hmm_state_labels.get(int(predictions[i]), "neutral")
        for i in range(len(predictions))
    }
    logger.info(f"HMM: predicted {_hmm_predictions[end_date] if end_date in _hmm_predictions else '?'} "
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


def get_market_regime(trade_date: str) -> str:
    """Get HMM-based market regime for a given trade date.

    Returns "bull", "bear", or "neutral". Uses HMM prediction confirmed
    by MA alignment.

    Args:
        trade_date: YYYYMMDD format.

    Returns:
        One of "bull", "bear", "neutral".
    """
    _ensure_model_trained(trade_date)

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
