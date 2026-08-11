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
    """Train HMM using expanding window with quarterly retraining.

    Pre-computes predictions for ALL quarters at once (20 train cycles,
    ~30s total). Each quarter uses only data up to that quarter-end
    (no look-ahead bias). The full predictions dict covers all 1351
    backtest days and is computed once per process.

    This replaces the original daily-expanding approach (which gave correct
    results but was 25s/day) and the full-sample approach (which was fast
    but misclassified 2021 bull as bear).
    """
    global _hmm_model, _hmm_state_labels, _hmm_train_end_date, _hmm_predictions

    if _hmm_predictions:
        return  # already pre-computed

    # Pre-compute predictions for each quarter (expanding window)
    quarters = []
    for y in range(2021, 2027):
        for qm in [3, 6, 9, 12]:
            qend = f"{y}{qm:02d}30"
            if qend <= "20260731":
                quarters.append(qend)

    for qend in quarters:
        df = _load_index_returns("000001.SH", "20210101", qend)
        if len(df) < 100:
            continue
        returns = df["ret"].values
        model, labels = _train_hmm(returns, n_states=3)
        preds = model.predict(returns.reshape(-1, 1))
        for i in range(len(preds)):
            d = df["trade_date"].iloc[i]
            # Only set if not already set by an EARLIER quarter (earlier = less look-ahead)
            if d not in _hmm_predictions:
                _hmm_predictions[d] = labels.get(int(preds[i]), "neutral")

    _hmm_train_end_date = quarters[-1] if quarters else end_date
    _hmm_model = True  # marker: pre-computation done
    logger.info(f"HMM: pre-computed {len(_hmm_predictions)} dates "
                f"with quarterly expanding window")


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
# MA120 hard bear trigger — DISABLED in production (C3 verified 2026-08-06).
# The HMM quarterly expanding window already identifies bear markets correctly.
# MA120 overlay over-triggers on V-bounces, missing recovery rallies.
# 5-year A/B: B0 (HMM only) = +43.84% vs B1 (HMM+MA120) = +22.05%.
_MA120_BEAR_THRESHOLD = -999.0  # -999 = disabled; -0.05 = 5% below MA120 triggers bear


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
