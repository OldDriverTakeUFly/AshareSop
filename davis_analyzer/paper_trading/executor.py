"""Daily execution engine for the paper-trading system.

The executor runs one "trading day" at a time:

1. Fetch close prices for held + candidate stocks (via DAL repository).
2. Generate factor signals (run scoring pipeline or read cached factors).
3. Evaluate the strategy → produce buy/sell signals.
4. Execute virtual trades (sells first, then buys).
5. Record NAV snapshot.

For backfill mode, this loops over historical dates using cached prices.
For live mode, it runs once for the latest trading day.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
from loguru import logger

from stockhot.core.config import DB_PATH
from stockhot.data_layer import get_repository
from stockhot.storage.database import get_connection

from davis_analyzer.paper_trading.account import PaperAccount, Position
from davis_analyzer.paper_trading.strategy import (
    DavisDoubleStrategy,
    FactorThresholdStrategy,
    MarketSnapshot,
    Signal,
    Strategy,
    create_strategy,
)

# Use market_data.db for trading calendar derivation
from stockhot.data_layer.market_db import get_connection as get_market_conn


def _get_trading_days(start: str, end: str) -> list[str]:
    """Get sorted list of trading days (YYYYMMDD) from the daily_price table.

    Derives the calendar from the union of all cached stock trade dates
    (more robust than relying on a single anchor stock which may not be cached).
    """
    with get_market_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM daily_price "
            "WHERE trade_date >= ? AND trade_date <= ? "
            "ORDER BY trade_date",
            (start, end),
        ).fetchall()
    return [r[0] for r in rows]


def _get_close_prices(ts_codes: list[str], trade_date: str) -> dict[str, float]:
    """Fetch close prices for multiple stocks on a given date (YYYYMMDD).

    If a stock has no price on *trade_date* (suspended, not yet listed, or
    data gap), falls back to the most recent prior trading day's close.
    This prevents NAV from collapsing to zero when one holding's price is
    temporarily missing.
    """
    if not ts_codes:
        return {}
    repo = get_repository()
    # Look back up to 10 calendar days to find a fallback price
    lookback_start = (
        datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=10)
    ).strftime("%Y%m%d")
    prices = {}
    for code in ts_codes:
        try:
            df = repo.get_daily_prices(code, lookback_start, trade_date)
            if df is not None and not df.empty:
                df = df.sort_values("trade_date")
                close = pd.to_numeric(df["close"], errors="coerce").dropna()
                if len(close) > 0:
                    prices[code] = float(close.iloc[-1])
        except Exception:
            logger.debug(f"price fetch failed for {code} on {trade_date}")
    return prices


def _get_stock_name(ts_code: str) -> str:
    """Get stock name from market_data.db."""
    with get_market_conn() as conn:
        row = conn.execute(
            "SELECT name FROM stock_basic WHERE ts_code=?", (ts_code,)
        ).fetchone()
    return row[0] if row else ts_code


def _get_davis_scores(trade_date: str) -> dict[str, dict]:
    """Get Davis Double scores for the universe.

    For now, this reads from a pre-computed scoring run. In production, it
    would call run_screening_pipeline(). For backfill, scores should be
    point-in-time (as-of the signal date).
    """
    # Placeholder: in live mode, run the pipeline. In backfill, scores are
    # computed by the caller and passed in. For now return empty — strategies
    # that depend on davis_scores will produce no buy signals until this is
    # wired to a real scoring source.
    return {}


def _get_factor_scores(trade_date: str, ts_codes: list[str]) -> dict[str, dict]:
    """Get supplementary factor scores for given stocks.

    Calls the individual factor engines. This is the live-mode path.
    For backfill, factor computation should be point-in-time.
    """
    from davis_analyzer.tushare_client import TushareClient
    from davis_analyzer.momentum import analyze_momentum
    from davis_analyzer.holder_concentration import analyze_holder_concentration
    from davis_analyzer.dividend import analyze_dividend
    from davis_analyzer.forecast import analyze_forecast

    client = TushareClient()
    as_of = datetime.strptime(trade_date, "%Y%m%d").date()
    scores: dict[str, dict] = {}

    for code in ts_codes:
        try:
            entry: dict[str, Any] = {}
            mom = analyze_momentum(client, code, today=as_of)
            if mom:
                entry["momentum"] = mom.momentum_score
            hc = analyze_holder_concentration(client, code, today=as_of)
            if hc:
                entry["holder"] = hc.concentration_score
                entry["holder_trend"] = hc.trend
            div = analyze_dividend(client, code, today=as_of)
            if div:
                entry["dividend"] = div.dividend_score
            fc = analyze_forecast(client, code, today=as_of)
            if fc:
                entry["forecast_leading"] = fc.leading_score
            scores[code] = entry
        except Exception:
            pass

    return scores


_BEARISH_STAGES = {"主跌浪", "下跌中反弹", "高位震荡筑顶"}
_BULLISH_STAGES = {"主升浪", "上涨中回调", "低位筑底"}


# ── Limit-up fill probability model ────────────────────────────────────

def _get_daily_pct_chg(ts_code: str, trade_date: str) -> float | None:
    """Get the daily pct change for a stock on trade_date from daily_price."""
    with get_market_conn() as conn:
        row = conn.execute(
            "SELECT pct_chg FROM daily_price WHERE ts_code=? AND trade_date=?",
            (ts_code, trade_date),
        ).fetchone()
    if row and row[0] is not None:
        return float(row[0])
    return None


def _limit_up_fill_probability(pct_chg: float | None) -> float:
    """Estimate the probability of successfully buying at close on a given day.

    A-share limit-up rules: 10% for main board, 20% for STAR/ChiNext, 30% for BSE.
    When a stock closes at limit-up, it means it was locked at the ceiling price
    all day (or opened at ceiling = 一字板), and buying is nearly impossible.

    Model:
      pct < 5%       → 100% (normal, definitely fillable)
      5% ≤ pct < 8%  → 95% (strong but not ceiling)
      8% ≤ pct < 9.5%→ 80% (near limit, partial fills common)
      9.5% ≤ pct < 19% → 20% (main board limit-up, mostly locked)
      19% ≤ pct < 29% → 20% (STAR/ChiNext limit-up)
      pct ≥ 29%      → 15% (BSE 30cm limit-up)
      None           → 100% (unknown, assume normal)
    """
    if pct_chg is None:
        return 1.0
    if pct_chg < 5.0:
        return 1.0
    if pct_chg < 8.0:
        return 0.95
    if pct_chg < 9.5:
        return 0.80
    if pct_chg < 19.0:
        return 0.20
    if pct_chg < 29.0:
        return 0.20
    return 0.15


def _get_market_regime(trade_date: str) -> str:
    """Determine market regime (bull/bear/mixed/panic) using ONLY data available
    on or before *trade_date* — no look-ahead bias.

    Multi-timeframe approach for sensitivity:
    - **Short-term (5-day return)**: catches trend reversal quickly.
    - **Medium-term (20-day return)**: confirms the broader trend.
    - **MA5 vs MA20**: structural trend confirmation.
    - **iVIX percentile**: panic overlay — when iVIX is historically high,
      downgrade regime one level (bull→mixed, mixed→bear) to reflect
      elevated risk even if price trends haven't fully reversed.

    Args:
        trade_date: YYYYMMDD format (no dashes).
    """
    import pandas as pd

    repo = get_repository()
    lookback_start = (
        datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=60)
    ).strftime("%Y%m%d")

    index_data = []
    for index_code in ("000001.SH", "399006.SZ"):
        try:
            df = repo.get_index_daily(index_code, lookback_start, trade_date)
            if df is None or df.empty:
                continue
            df = df.sort_values("trade_date")
            close = pd.to_numeric(df["close"], errors="coerce").dropna()
            if len(close) < 5:
                continue

            ret_5d = (close.iloc[-1] / close.iloc[-1 - min(5, len(close) - 1)] - 1) * 100
            window_20 = min(20, len(close) - 1)
            ret_20d = (close.iloc[-1] / close.iloc[-1 - window_20] - 1) * 100

            ma5 = close.rolling(5).mean().iloc[-1] if len(close) >= 5 else None
            ma20 = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else None
            ma_below = ma5 is not None and ma20 is not None and ma5 < ma20

            index_data.append({
                "code": index_code,
                "ret_5d": ret_5d,
                "ret_20d": ret_20d,
                "ma_below": ma_below,
            })
        except Exception:
            continue

    if not index_data:
        return "mixed"

    # ── Fast signal: any index with 5-day return < -5% → bear immediately ──
    if any(d["ret_5d"] < -5.0 for d in index_data):
        return "bear"

    # ── Confirmed bull: both indices positive on both timeframes ──
    if all(d["ret_5d"] > 0 and d["ret_20d"] > 0 for d in index_data):
        base_regime = "bull"
    # ── Confirmed bear: both indices negative on 20-day ──
    elif all(d["ret_20d"] < 0 for d in index_data):
        base_regime = "bear"
    # ── MA confirmation: both indices MA5 < MA20 → bear ──
    elif all(d["ma_below"] for d in index_data):
        base_regime = "bear"
    else:
        base_regime = "mixed"

    # ── iVIX panic overlay ──
    # When iVIX is historically elevated, downgrade regime to reflect risk
    ivix_pct = _get_ivix_percentile(trade_date)
    if ivix_pct is not None:
        if ivix_pct >= 75:
            # Extreme panic: force bear regardless of price trends
            return "bear"
        elif ivix_pct >= 60 and base_regime == "bull":
            # Elevated panic: downgrade bull → mixed
            return "mixed"

    return base_regime


def _get_ivix_percentile(trade_date: str) -> float | None:
    """Get iVIX historical percentile for *trade_date* (0-100).

    Returns the % of historical iVIX values below the current reading.
    High percentile = market is in a state of elevated fear/panic.
    Uses point-in-time data only (trade_date and before).
    """
    with get_market_conn() as conn:
        # Current iVIX on or before trade_date
        row = conn.execute(
            "SELECT close FROM ivix_history WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 1",
            (trade_date,),
        ).fetchone()
        if not row or row[0] is None:
            return None
        current_ivix = float(row[0])

        # Historical distribution (all data up to trade_date)
        rows = conn.execute(
            "SELECT close FROM ivix_history WHERE trade_date <= ? AND close IS NOT NULL",
            (trade_date,),
        ).fetchall()
        if len(rows) < 30:  # need enough history
            return None

        all_values = [float(r[0]) for r in rows if r[0] is not None]
        pct = sum(1 for v in all_values if v < current_ivix) / len(all_values) * 100
        return round(pct, 1)


def _get_industries(ts_codes: list[str]) -> dict[str, str]:
    """Build ts_code → industry lookup from stock_basic table."""
    if not ts_codes:
        return {}
    placeholders = ",".join("?" * len(ts_codes))
    with get_market_conn() as conn:
        rows = conn.execute(
            f"SELECT ts_code, industry FROM stock_basic WHERE ts_code IN ({placeholders})",
            ts_codes,
        ).fetchall()
    return {r[0]: r[1] for r in rows if r[1]}


def _compute_short_momentum(ts_codes: list[str], trade_date: str) -> dict[str, float]:
    """Compute 5-day return % for each stock. Positive = still rising recently."""
    if not ts_codes:
        return {}
    with get_market_conn() as conn:
        dates_row = conn.execute(
            "SELECT DISTINCT trade_date FROM daily_price WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 6",
            (trade_date,),
        ).fetchall()
    dates = [r[0] for r in dates_row]
    if len(dates) < 2:
        return {}
    today_str, past_str = dates[0], dates[-1]

    result: dict[str, float] = {}
    with get_market_conn() as conn:
        curr = {r[0]: float(r[1]) for r in conn.execute(
            "SELECT ts_code, close FROM daily_price WHERE trade_date=? AND close IS NOT NULL", (today_str,)
        ).fetchall()}
        past = {r[0]: float(r[1]) for r in conn.execute(
            "SELECT ts_code, close FROM daily_price WHERE trade_date=? AND close IS NOT NULL", (past_str,)
        ).fetchall()}
    for code in ts_codes:
        c = curr.get(code)
        p = past.get(code)
        if c and p and p > 0:
            result[code] = round((c / p - 1) * 100, 2)
    return result


def _compute_pe_percentiles(ts_codes: list[str], trade_date: str) -> dict[str, float]:
    """Compute PE historical percentile (0-100) for each stock on trade_date.

    Directly queries daily_basic (market_data.db) for 3-year PE history
    and computes percentile. This is a pure SQL+Python operation (~0.02ms
    per stock, ~5ms for 200 stocks) — fast enough for daily use.

    No separate cache table needed because daily_basic IS the cache.
    The bottleneck is having enough historical PE data in daily_basic;
    if data is sparse, results may be less accurate but won't error.

    Args:
        ts_codes: stocks to compute PE percentile for.
        trade_date: YYYYMMDD — percentile is computed against all PE values
            up to and including this date (point-in-time correct).

    Returns:
        {ts_code: percentile} where percentile is 0-100 (higher = more expensive).
    """
    if not ts_codes:
        return {}

    from datetime import datetime as _dt, timedelta as _td
    lookback = (_dt.strptime(trade_date, "%Y%m%d") - _td(days=1095)).strftime("%Y%m%d")
    result: dict[str, float] = {}

    with get_market_conn() as conn:
        for code in ts_codes:
            rows = conn.execute(
                "SELECT pe_ttm FROM daily_basic "
                "WHERE ts_code=? AND trade_date>=? AND trade_date<=? AND pe_ttm > 0 "
                "ORDER BY trade_date",
                (code, lookback, trade_date),
            ).fetchall()
            if len(rows) < 10:
                continue
            values = [float(r[0]) for r in rows]
            current = values[-1]  # latest PE on or before trade_date
            pct = sum(1 for v in values if v < current) / len(values) * 100
            result[code] = round(pct, 1)

    return result


def _ensure_daily_basic_history(client, ts_codes: list[str], trade_date: str) -> int:
    """Ensure daily_basic has 3-year PE history for all ts_codes.

    Fetches missing PE data via TushareClient and inserts into daily_basic.
    Called once during pool refresh to guarantee _compute_pe_percentiles
    has enough data to work with.

    Returns number of stocks with ≥10 PE data points after fetch.
    """
    from datetime import datetime as _dt, timedelta as _td
    import pandas as pd

    lookback = (_dt.strptime(trade_date, "%Y%m%d") - _td(days=1095)).strftime("%Y%m%d")
    sufficient = 0

    for code in ts_codes:
        try:
            # Check current data coverage
            with get_market_conn() as conn:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM daily_basic WHERE ts_code=? AND pe_ttm > 0",
                    (code,),
                ).fetchone()[0]

            if cnt >= 100:
                sufficient += 1
                continue  # already has enough history

            # Fetch 3-year PE history
            raw = client._call(
                "daily_basic",
                client._pro.daily_basic,
                {
                    "ts_code": code,
                    "start_date": lookback,
                    "end_date": trade_date,
                    "fields": "ts_code,trade_date,pe_ttm,pb,ps,total_mv",
                },
            )
            if raw is not None and not raw.empty:
                client._daily_basic_insert(code, raw)
                sufficient += 1
        except Exception:
            pass

    return sufficient


def _compute_volatilities(ts_codes: list[str], trade_date: str) -> dict[str, float]:
    """Compute 20-day annualized volatility (%) for each stock.

    vol = std(daily_returns) * sqrt(250) * 100
    """
    if not ts_codes:
        return {}
    import pandas as pd
    with get_market_conn() as conn:
        dates_row = conn.execute(
            "SELECT DISTINCT trade_date FROM daily_price WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 21",
            (trade_date,),
        ).fetchall()
    dates = [r[0] for r in dates_row]
    if len(dates) < 5:
        return {}
    start_str = dates[-1]

    result: dict[str, float] = {}
    with get_market_conn() as conn:
        for code in ts_codes:
            rows = conn.execute(
                "SELECT close FROM daily_price WHERE ts_code=? AND trade_date>=? AND trade_date<=? AND close IS NOT NULL ORDER BY trade_date",
                (code, start_str, trade_date),
            ).fetchall()
            if len(rows) < 5:
                continue
            closes = pd.Series([float(r[0]) for r in rows])
            rets = closes.pct_change().dropna()
            if len(rets) < 3:
                continue
            vol = float(rets.std() * (250 ** 0.5) * 100)
            result[code] = round(vol, 1)
    return result


# ── Volume-price signal computation ────────────────────────────────────
#
# Implements three classic Chinese-quant volume-price signals based on the
# 国盛证券《量价淘金》framework plus practitioner thresholds:
#
#   1. 平台突破 (platform breakout) — BUY
#      20-day box amplitude < 15% AND close > box_high * 1.01 AND vol > MA20 * 1.5
#
#   2. 低位放量 (low-position high-volume) — BUY
#      120-day price percentile ≤ 20% AND (vol > MA20 * 2 OR 120d vol percentile ≥ 80%)
#
#   3. 高位放量 (high-position high-volume) — SELL/REDUCE
#      120-day price percentile ≥ 80% AND (vol > MA20 * 2 OR 120d vol percentile ≥ 90%)
#
# Returns ``{ts_code: {score, signal_type, vol_ratio, position_pct, box_amplitude}}``.
# Score is 0-100: platform/low-position signals score 70-90, high-position 25-35,
# neutral cases 50-60. The strategy uses ``score`` as a composite-rating input
# (default weight 10%); the risk layer keys off ``signal_type == "high_vol"``.
#
# Reads only from ``daily_price`` (vol/amount are 100% covered there); does NOT
# depend on ``daily_basic.turnover_rate`` to avoid cross-table JOIN complexity.

_VOL_LOOKBACK_DAYS = 130        # pull 130 trading days to compute 120d percentile + 20d box
_VOL_MA_WINDOW = 20             # short-term volume baseline
_VOL_MIN_HISTORY = 60           # below this many days → return neutral
_BOX_WINDOW = 20                # platform consolidation window
_BOX_MAX_AMPLITUDE = 0.15       # max (high-low)/low for a valid platform
_BOX_BREAKOUT_BUFFER = 1.01     # close must exceed box_high by 1%
_PLATFORM_VOL_RATIO = 1.5       # vol/MA20 threshold for platform breakout
_EXTREME_VOL_RATIO = 2.0        # vol/MA20 threshold for low/high-position signals
_LOW_POS_PCT = 20.0             # 120d price percentile ≤ 20% → low position
_HIGH_POS_PCT = 80.0            # 120d price percentile ≥ 80% → high position
_LOW_VOL_PCTILE = 80.0          # 120d volume percentile ≥ 80% → qualifies low-position
_HIGH_VOL_PCTILE = 90.0         # 120d volume percentile ≥ 90% → qualifies high-position


def _compute_volume_signals(ts_codes: list[str], trade_date: str) -> dict[str, dict]:
    """Compute volume-price signals for each stock.

    Returns ``{ts_code: {score, signal_type, vol_ratio, position_pct,
    box_amplitude}}``. See module-level docstring above for signal definitions.
    """
    if not ts_codes:
        return {}
    import pandas as pd

    # Resolve the lookback window using actual trading days.
    with get_market_conn() as conn:
        date_rows = conn.execute(
            "SELECT DISTINCT trade_date FROM daily_price "
            "WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?",
            (trade_date, _VOL_LOOKBACK_DAYS),
        ).fetchall()
    dates = [r[0] for r in date_rows]
    if len(dates) < _VOL_MIN_HISTORY:
        return {}
    start_str = dates[-1]

    result: dict[str, dict] = {}
    with get_market_conn() as conn:
        for code in ts_codes:
            rows = conn.execute(
                "SELECT trade_date, high, low, close, vol FROM daily_price "
                "WHERE ts_code=? AND trade_date>=? AND trade_date<=? "
                "AND close IS NOT NULL ORDER BY trade_date",
                (code, start_str, trade_date),
            ).fetchall()
            if len(rows) < _VOL_MIN_HISTORY:
                # Not enough history → return neutral so we don't bias the score.
                result[code] = {
                    "score": 50.0,
                    "signal_type": "neutral",
                    "vol_ratio": 0.0,
                    "position_pct": 50.0,
                    "box_amplitude": 0.0,
                }
                continue

            df = pd.DataFrame(
                rows, columns=["trade_date", "high", "low", "close", "vol"]
            )
            df["high"] = df["high"].astype(float)
            df["low"] = df["low"].astype(float)
            df["close"] = df["close"].astype(float)
            df["vol"] = df["vol"].astype(float)

            today_close = float(df["close"].iloc[-1])
            today_vol = float(df["vol"].iloc[-1])

            # 120-day price percentile (using all available rows up to 130)
            position_pct = float(
                (df["close"] <= today_close).sum() / len(df) * 100
            )

            # 20-day volume MA (use the last _VOL_MA_WINDOW rows; exclude today to
            # avoid biasing the baseline with the very spike we are measuring).
            if len(df) > _VOL_MA_WINDOW:
                vol_ma = float(df["vol"].iloc[-_VOL_MA_WINDOW - 1 : -1].mean())
            else:
                vol_ma = float(df["vol"].iloc[:-1].mean()) if len(df) > 1 else today_vol
            vol_ratio = float(today_vol / vol_ma) if vol_ma > 0 else 0.0

            # 120-day volume percentile
            vol_pctile = float((df["vol"] <= today_vol).sum() / len(df) * 100)

            # 20-day box (platform) geometry
            if len(df) >= _BOX_WINDOW:
                box = df.iloc[-_BOX_WINDOW:]
                box_high = float(box["high"].max())
                box_low = float(box["low"].min())
                box_amplitude = (
                    float((box_high - box_low) / box_low) if box_low > 0 else 0.0
                )
            else:
                box_high = today_close
                box_low = today_close
                box_amplitude = 0.0

            # ── Classify signal ──
            signal_type = "neutral"
            score = 50.0  # neutral baseline

            # 1. Platform breakout — needs tight box + breakout + volume confirm
            is_platform = (
                box_amplitude <= _BOX_MAX_AMPLITUDE
                and len(df) >= _BOX_WINDOW
            )
            is_breakout = today_close >= box_high * _BOX_BREAKOUT_BUFFER
            if is_platform and is_breakout and vol_ratio >= _PLATFORM_VOL_RATIO:
                signal_type = "platform_breakout"
                # 70-90 range: base 70 + bonus for stronger volume (cap at 90)
                score = 70.0 + min((vol_ratio - _PLATFORM_VOL_RATIO) * 8, 20.0)

            # 2. Low-position high-volume (accumulation)
            # Position ≤ 20% AND (vol ratio ≥ 2 OR vol percentile ≥ 80%)
            elif (
                position_pct <= _LOW_POS_PCT
                and (vol_ratio >= _EXTREME_VOL_RATIO or vol_pctile >= _LOW_VOL_PCTILE)
            ):
                signal_type = "low_vol"
                # 60-85: base 60 + bonus for lower position + higher volume
                pos_bonus = (_LOW_POS_PCT - position_pct) * 0.5  # 0-10
                vol_bonus = min(max(vol_ratio - 1.0, 0.0) * 10, 15.0)  # 0-15
                score = 60.0 + pos_bonus + vol_bonus

            # 3. High-position high-volume (distribution)
            # Position ≥ 80% AND (vol ratio ≥ 2 OR vol percentile ≥ 90%)
            elif (
                position_pct >= _HIGH_POS_PCT
                and (vol_ratio >= _EXTREME_VOL_RATIO or vol_pctile >= _HIGH_VOL_PCTILE)
            ):
                signal_type = "high_vol"
                # 25-35: lower score → drags down composite rating
                pos_penalty = (position_pct - _HIGH_POS_PCT) * 0.2  # 0-2
                vol_penalty = min(max(vol_ratio - 1.0, 0.0) * 5, 8.0)
                score = 35.0 - pos_penalty - vol_penalty

            # 4. Neutral — slight tilt toward 50 + mild volume strength
            else:
                if vol_ratio >= 1.2:
                    # mild volume pick-up, neither extreme
                    score = 55.0 + min((vol_ratio - 1.2) * 5, 5.0)
                else:
                    score = 50.0

            result[code] = {
                "score": round(score, 1),
                "signal_type": signal_type,
                "vol_ratio": round(vol_ratio, 2),
                "position_pct": round(position_pct, 1),
                "box_amplitude": round(box_amplitude * 100, 1),  # as %
            }
    return result


# ── Event signal computation (实证驱动) ─────────────────────────────────
#
# Implements hard-gate filters based on the event CAR study
# (docs/方法论/A股事件因子实证研究方法论.md). Two empirically validated
# signals are included:
#
#   1. 股东减持 (holder reduction)
#      Empirical: 公告前 20 天 +2.58% (t=+14), 公告后 60 天 -1.76% (t=-7).
#      Rule: skip if last 60 days has a >1% reduction announcement.
#
#   2. 限售解禁 (lockup release)
#      Empirical: 解禁前 20 天 -2.79% (t=-2.87).
#      Rule: skip if next 20 calendar days has a >=5% unlock.
#
# Returns ``{ts_code: {"blocked": bool, "reason": str, "events": [...]}}``.
# Strategy uses ``blocked`` to filter; ``reason`` is included in signal_reason
# for traceability.

# Lookback/forward windows (calibrated to our 2025-2026 sample CAR study)
_REDUCTION_LOOKBACK_DAYS = 60       # 减持公告后 60 天负面影响期
_REDUCTION_MIN_RATIO = 1.0         # 减持比例 ≥1% 才考虑（实证：>1% 组冲击显著）
_UNLOCK_FORWARD_DAYS = 30          # 解禁前 30 天开始走弱（用 30 保守，实证是 20）
_UNLOCK_MIN_RATIO = 5.0            # 解禁规模 ≥5%（"大非"标准）


def _compute_event_signals(ts_codes: list[str], trade_date: str) -> dict[str, dict]:
    """Compute event-based hard-gate flags for each stock.

    Returns ``{ts_code: {"blocked": bool, "reason": str}}``.
    ``blocked=True`` means the stock should be excluded from buy candidates.
    """
    if not ts_codes:
        return {}

    # Date arithmetic (YYYYMMDD strings)
    from datetime import datetime, timedelta
    td = datetime.strptime(trade_date, "%Y%m%d")
    reduction_start = (td - timedelta(days=_REDUCTION_LOOKBACK_DAYS)).strftime("%Y%m%d")
    unlock_end = (td + timedelta(days=_UNLOCK_FORWARD_DAYS)).strftime("%Y%m%d")

    result: dict[str, dict] = {}

    with get_market_conn() as conn:
        # Bulk query 1: holder reductions in [reduction_start, trade_date]
        # Filter by direction=negative (减持) and magnitude (change_ratio) ≥ threshold
        reduction_rows = conn.execute(
            "SELECT ts_code, ann_date, magnitude, details_json "
            "FROM corp_event "
            "WHERE event_type='holder_trade' AND direction='negative' "
            "AND magnitude IS NOT NULL AND magnitude >= ? "
            "AND ann_date >= ? AND ann_date <= ?",
            (_REDUCTION_MIN_RATIO, reduction_start, trade_date),
        ).fetchall()
        reductions_by_code: dict[str, list] = {}
        for r in reduction_rows:
            reductions_by_code.setdefault(r[0], []).append({
                "ann_date": r[1], "ratio": r[2],
            })

        # Bulk query 2: upcoming unlocks in [trade_date, unlock_end]
        # NOTE: corp_event.share_float uses ann_date as the unlock announcement date.
        # We treat "upcoming" as ann_date in the next N days. If the company has
        # ALREADY announced (ann_date <= trade_date) an unlock dated in the future,
        # that's also captured because we look at ann_date range.
        unlock_rows = conn.execute(
            "SELECT ts_code, ann_date, magnitude, details_json "
            "FROM corp_event "
            "WHERE event_type='share_float' "
            "AND magnitude IS NOT NULL AND magnitude >= ? "
            "AND ann_date >= ? AND ann_date <= ?",
            (_UNLOCK_MIN_RATIO, trade_date, unlock_end),
        ).fetchall()
        unlocks_by_code: dict[str, list] = {}
        for r in unlock_rows:
            unlocks_by_code.setdefault(r[0], []).append({
                "ann_date": r[1], "ratio": r[2],
            })

    for code in ts_codes:
        reasons = []
        penalty = 0.0  # 0-30 penalty for soft-gate use

        reductions = reductions_by_code.get(code, [])
        if reductions:
            max_ratio = max(r["ratio"] for r in reductions)
            most_recent = max(r["ann_date"] for r in reductions)
            n_red = len(reductions)
            reasons.append(
                f"减持(ratio={max_ratio:.1f}%,{n_red}次,最近{most_recent})"
            )
            # 减持软门槛：基础分按 max_ratio 映射
            #   1% → 3 分；3% → 9 分；5% → 15 分；10%+ → 20 分（封顶）
            reduction_penalty = min(max_ratio * 3.0, 20.0)
            # 多次减持加成：每次额外 +1，封顶 +5
            reduction_penalty += min(n_red - 1, 5)
            penalty += reduction_penalty

        unlocks = unlocks_by_code.get(code, [])
        if unlocks:
            max_ratio = max(u["ratio"] for u in unlocks)
            nearest = min(u["ann_date"] for u in unlocks)
            n_unl = len(unlocks)
            reasons.append(
                f"解禁(ratio={max_ratio:.1f}%,{n_unl}次,最近{nearest})"
            )
            # 解禁软门槛：按 max_ratio 映射（解禁通常更大，所以系数小一些）
            #   5% → 3 分；10% → 6 分；20% → 12 分；50%+ → 20 分（封顶）
            unlock_penalty = min(max_ratio * 0.6, 20.0)
            penalty += unlock_penalty

        # Cap total penalty at 30 (so a strong stock can still rank, just lower)
        penalty = min(penalty, 30.0)

        result[code] = {
            "blocked": bool(reasons),
            "penalty": round(penalty, 1),
            "reason": "；".join(reasons) if reasons else "",
        }

    return result


def _load_tech_scores(ts_codes: list[str], trade_date: str) -> dict[str, float]:
    """Load tech_score from tech_factor table (computed offline by tech_research).

    The tech_factor table is keyed by (ts_code, trade_date). We look up each
    stock's score on the exact trade_date; if missing, return nothing (the
    strategy defaults to 50.0 in the composite formula).
    """
    if not ts_codes:
        return {}
    result: dict[str, float] = {}
    # Batch query in chunks of 500 (SQLite parameter limit safety)
    with get_market_conn() as conn:
        for i in range(0, len(ts_codes), 500):
            chunk = ts_codes[i : i + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT ts_code, tech_score FROM tech_factor "
                f"WHERE trade_date=? AND ts_code IN ({placeholders}) "
                f"AND tech_score IS NOT NULL",
                (trade_date, *chunk),
            ).fetchall()
            for r in rows:
                result[r[0]] = float(r[1])
    return result


def _full_market_sector_trends(trade_date: str) -> dict[str, str]:
    """Compute per-industry trend from ALL market stocks' 5-day returns.

    For each industry in stock_basic, calculates the average 5-trading-day
    return of all stocks in that industry using daily_price data up to
    *trade_date*. This gives a true sector-level signal:
      - avg 5d return > +2% → "up" (sector is rallying)
      - avg 5d return < -2% → "down" (sector is selling off)
      - otherwise → "flat"

    Only industries with ≥5 stocks are scored (statistical significance).
    """
    import pandas as pd

    # Find the trade_date and 5 trading days before it
    with get_market_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM daily_price "
            "WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 6",
            (trade_date,),
        ).fetchall()
    dates = [r[0] for r in rows]
    if len(dates) < 2:
        return {}
    today_str = dates[0]
    past_str = dates[-1]

    # Get all stocks' returns over this window
    with get_market_conn() as conn:
        conn.row_factory = __import__("sqlite3").Row
        # Current close
        curr = conn.execute(
            "SELECT ts_code, close FROM daily_price WHERE trade_date = ?",
            (today_str,),
        ).fetchall()
        curr_map = {r["ts_code"]: float(r["close"]) for r in curr if r["close"]}

        # Past close (5 trading days ago)
        past = conn.execute(
            "SELECT ts_code, close FROM daily_price WHERE trade_date = ?",
            (past_str,),
        ).fetchall()
        past_map = {r["ts_code"]: float(r["close"]) for r in past if r["close"]}

        # Industry mapping
        ind_rows = conn.execute(
            "SELECT ts_code, industry FROM stock_basic WHERE industry IS NOT NULL AND industry != ''"
        ).fetchall()
        code2ind = {r["ts_code"]: r["industry"] for r in ind_rows}

    # Group returns by industry
    industry_returns: dict[str, list[float]] = {}
    for code, curr_close in curr_map.items():
        past_close = past_map.get(code)
        industry = code2ind.get(code)
        if past_close and past_close > 0 and industry:
            ret = (curr_close / past_close - 1) * 100
            industry_returns.setdefault(industry, []).append(ret)

    # Score each industry
    trends: dict[str, str] = {}
    for industry, rets in industry_returns.items():
        if len(rets) < 5:
            trends[industry] = "flat"
            continue
        avg_ret = sum(rets) / len(rets)
        if avg_ret > 2.0:
            trends[industry] = "up"
        elif avg_ret < -2.0:
            trends[industry] = "down"
        else:
            trends[industry] = "flat"

    return trends


def _infer_industry_trends(
    factor_data: dict[str, dict], industries: dict[str, str],
    trade_date: str | None = None,
) -> dict[str, str]:
    """Determine per-industry price trend using full-market sector scoring.

    When *trade_date* is provided, computes each industry's average 5-day
    return across ALL stocks in that industry (from daily_price + stock_basic),
    not just the small candidate pool. This gives an accurate sector-level
    signal: if 半导体 as a whole (195 stocks) is up 5% in 5 days, that's a
    strong sector uptrend regardless of what our 8-stock portfolio shows.

    Falls back to the old momentum-based inference if trade_date is None or
    the full-market query fails.

    Args:
        factor_data: per-stock factor scores (fallback path).
        industries: ts_code → industry mapping.
        trade_date: YYYYMMDD — if given, uses full-market sector calc.

    Returns:
        industry → "up" / "down" / "flat"
    """
    # ── Full-market sector scoring (preferred) ──
    if trade_date:
        try:
            return _full_market_sector_trends(trade_date)
        except Exception:
            logger.debug(f"full-market sector trends failed for {trade_date}, fallback")

    # ── Fallback: infer from candidate pool momentum ──
    industry_momentums: dict[str, list[float]] = {}
    for code, factors in factor_data.items():
        mom = factors.get("momentum")
        industry = industries.get(code, "")
        if mom is not None and industry:
            industry_momentums.setdefault(industry, []).append(mom)

    trends: dict[str, str] = {}
    for industry, moms in industry_momentums.items():
        if len(moms) < 2:
            trends[industry] = "flat"
            continue
        avg = sum(moms) / len(moms)
        if avg > 55:
            trends[industry] = "up"
        elif avg < 45:
            trends[industry] = "down"
        else:
            trends[industry] = "flat"
    return trends


class DailyExecutor:
    """Execute one trading day for a paper-trading account."""

    # ── Dynamic stop-loss / take-profit rule table ──
    # (market_regime, sector_trend) → (hard_stop_pct, take_profit_pct)
    # take_profit_pct of 0.0 means "no take-profit, only stop-loss"
    _RISK_RULES: dict[tuple[str, str], tuple[float, float]] = {
        ("bear", "down"):  (0.07, 0.0),   # 熊市弱赛道：快速止损，不止盈
        ("bear", "up"):    (0.10, 0.15),  # 熊市强赛道：保守
        ("bear", "flat"):  (0.08, 0.10),  # 熊市中性：偏紧
        ("bull", "up"):    (0.12, 0.30),  # 牛市强赛道：给足空间
        ("bull", "down"):  (0.08, 0.15),  # 牛市弱赛道：收紧
        ("bull", "flat"):  (0.10, 0.20),  # 牛市中性：标准
        ("mixed", "up"):   (0.10, 0.20),  # 分化强赛道：标准
        ("mixed", "down"): (0.08, 0.12),  # 分化弱赛道：偏紧
        ("mixed", "flat"): (0.10, 0.20),  # 分化中性：标准
    }
    _DEFAULT_RISK = (0.10, 0.20)

    def __init__(self, account: PaperAccount, strategy: Strategy) -> None:
        self.account = account
        self.strategy = strategy
        self.commission_bps = 2.5
        self.stamp_tax_bps = 10.0
        # T-trading config
        self.enable_t_trading = True
        self.t_trim_threshold = 0.08   # trim 1/3 when up 8%+
        self.t_add_threshold = -0.05   # add 1/4 when down 5%+ (buy the dip)
        self.t_trim_ratio = 1.0 / 3    # sell 1/3 of position
        self.t_add_ratio = 1.0 / 4     # buy 25% of current position

    def _get_risk_thresholds(
        self, market_regime: str, sector_trend: str,
        volatility: float | None = None,
    ) -> tuple[float, float]:
        """Look up dynamic stop-loss / take-profit, optionally vol-adjusted.

        When *volatility* (annualized %) is provided, the stop-loss is
        widened proportionally: a stock with 2x average volatility gets
        ~1.5x wider stop (not fully 2x to avoid excessive risk).
        Base stop is from _RISK_RULES, then scaled by vol multiplier.
        """
        base_stop, base_tp = self._RISK_RULES.get(
            (market_regime, sector_trend), self._DEFAULT_RISK
        )

        if volatility is not None and volatility > 0:
            # Average A-share annualized vol ~35%. Scale linearly but clamp.
            # vol_mult = clamp(volatility / 35, 0.8, 2.0)
            vol_mult = max(0.8, min(2.0, volatility / 35.0))
            # Apply sqrt scaling (not linear) to avoid over-widening
            adj_stop = base_stop * (vol_mult ** 0.5)
            adj_tp = base_tp * (vol_mult ** 0.5)
            # Cap at reasonable bounds
            adj_stop = min(adj_stop, 0.20)   # never wider than 20%
            adj_tp = min(adj_tp, 0.50)       # never wider than 50%
            return (adj_stop, adj_tp)

        return (base_stop, base_tp)

    def _check_risk_signals(
        self,
        positions: list[Position],
        prices: dict[str, float],
        trade_date: str,
        market_regime: str = "mixed",
        industries: dict[str, str] | None = None,
        industry_trend: dict[str, str] | None = None,
        volatilities: dict[str, float] | None = None,
        volume_signals: dict[str, dict] | None = None,
    ) -> list[Signal]:
        """Check stop-loss / take-profit with DYNAMIC + VOL-ADJUSTED thresholds.

        Also includes the **高位放量 (high-position high-volume)** risk sell:
        when a position is already profitable AND the volume-price engine flags
        ``signal_type == "high_vol"``, we treat it as a distribution event and
        emit a SELL. This catches "riding a winner into a top" scenarios where
        price is at 120d-high and turnover is spiking.
        """
        industries = industries or {}
        industry_trend = industry_trend or {}
        volatilities = volatilities or {}
        volume_signals = volume_signals or {}
        signals: list[Signal] = []
        for pos in positions:
            px = prices.get(pos.ts_code)
            if px is None or px <= 0:
                continue
            pnl_pct = (px / pos.avg_cost - 1) if pos.avg_cost > 0 else 0

            industry = industries.get(pos.ts_code, "")
            sector_trend = industry_trend.get(industry, "flat")
            vol = volatilities.get(pos.ts_code)
            hard_stop, take_profit = self._get_risk_thresholds(
                market_regime, sector_trend, volatility=vol
            )

            if pnl_pct <= -hard_stop:
                signals.append(
                    Signal(
                        ts_code=pos.ts_code,
                        name=pos.name,
                        action="SELL",
                        signal_reason=f"硬止损 P&L={pnl_pct*100:.1f}% (止损线{hard_stop*100:.0f}% {market_regime}/{sector_trend})",
                    )
                )
            elif take_profit > 0 and pnl_pct >= take_profit:
                signals.append(
                    Signal(
                        ts_code=pos.ts_code,
                        name=pos.name,
                        action="SELL",
                        signal_reason=f"止盈 P&L=+{pnl_pct*100:.1f}% (止盈线{take_profit*100:.0f}% {market_regime}/{sector_trend})",
                    )
                )
            else:
                # 高位放量 (high-position high-volume) — distribution risk.
                # Only trigger when already profitable AND volume-price engine
                # explicitly flags "high_vol" signal type. This is a take-profit
                # variant that fires earlier than the static take_profit line
                # when the volume-price pattern suggests distribution.
                # Skip entirely if strategy disabled the volume-risk path.
                if not getattr(self.strategy, "enable_volume_risk", True):
                    continue
                vol_sig = volume_signals.get(pos.ts_code)
                if (
                    vol_sig is not None
                    and vol_sig.get("signal_type") == "high_vol"
                    and pnl_pct >= 0.10  # only reduce winners (≥10% gain)
                ):
                    signals.append(
                        Signal(
                            ts_code=pos.ts_code,
                            name=pos.name,
                            action="SELL",
                            signal_reason=(
                                f"高位放量 P&L=+{pnl_pct*100:.1f}% "
                                f"量比={vol_sig.get('vol_ratio', 0):.1f} "
                                f"价格分位={vol_sig.get('position_pct', 0):.0f}%"
                            ),
                        )
                    )
        return signals

    def _execute_t_trades(
        self,
        positions: list[Position],
        prices: dict[str, float],
        trade_date: str,
        market_regime: str = "mixed",
        industries: dict[str, str] | None = None,
        industry_trend: dict[str, str] | None = None,
        factor_data: dict[str, dict] | None = None,
    ) -> list:
        """Execute T-trades: trim profits and add on dips.

        T-trading is position management within the holding period:
        - **Trim** (partial sell): when a position is up ≥8%, sell 1/3 to
          lock in partial profit while keeping the core position. This
          reduces average cost and de-risks without fully exiting.
        - **Add** (partial buy): when a position is down 5-8% AND prosperity
          stage is still 加速期/上升拐点 (fundamentals still healthy),
          buy 25% more to lower average cost. This is "buying the dip" for
          quality holdings whose growth thesis hasn't broken.
        - **No T-add** when:
          - market is bear (don't add in downtrend)
          - sector is declining
          - prosperity stage is 下降拐点/减速期 (fundamentals deteriorating)
          - already added once for this position (frequency limit)

        Returns list of TradeRecord from T-trades.
        """
        from davis_analyzer.paper_trading.account import TradeRecord

        industries = industries or {}
        industry_trend = industry_trend or {}
        factor_data = factor_data or {}
        t_trades: list[TradeRecord] = []
        # Track which positions we've already added to today (freq limit)
        added_today: set[str] = set()

        for pos in positions:
            px = prices.get(pos.ts_code)
            if px is None or px <= 0 or pos.avg_cost <= 0:
                continue

            pnl_pct = (px / pos.avg_cost - 1)
            industry = industries.get(pos.ts_code, "")
            sector_trend = industry_trend.get(industry, "flat")

            # ── Trim: partial take-profit ──
            if pnl_pct >= self.t_trim_threshold:
                trim_shares = int(pos.shares * self.t_trim_ratio // 100) * 100
                if trim_shares >= 100:
                    trade = self.account.sell(
                        ts_code=pos.ts_code,
                        name=pos.name,
                        shares=trim_shares,
                        price=px,
                        trade_date=trade_date,
                        signal_reason=f"T+减仓{self.t_trim_ratio:.0%} P&L=+{pnl_pct*100:.1f}%",
                    )
                    if trade:
                        t_trades.append(trade)

            # ── Add: buy the dip (with prosperity confirmation + freq limit) ──
            elif (self.t_add_threshold <= pnl_pct < 0
                  and market_regime != "bear"
                  and sector_trend != "down"
                  and pos.ts_code not in added_today):
                # Prosperity gate: only add if fundamentals still healthy
                factors = factor_data.get(pos.ts_code, {})
                stage = factors.get("stage", "")
                prosperity = factors.get("prosperity")

                # Must be in 加速期 or 上升拐点 (growth thesis intact)
                if stage not in ("加速期", "上升拐点"):
                    continue
                # Prosperity score must still be decent
                if prosperity is not None and prosperity < 40:
                    continue
                # Panic gate: don't add when iVIX is elevated (market fearful)
                ivix_pct = _get_ivix_percentile(trade_date)
                if ivix_pct is not None and ivix_pct >= 60:
                    continue

                add_shares = int(pos.shares * self.t_add_ratio // 100) * 100
                if add_shares >= 100:
                    cost_estimate = add_shares * px * (1 + self.commission_bps / 1e4)
                    if self.account.cash >= cost_estimate:
                        trade = self.account.buy(
                            ts_code=pos.ts_code,
                            name=pos.name,
                            shares=add_shares,
                            price=px,
                            trade_date=trade_date,
                            signal_reason=f"T+加仓{self.t_add_ratio:.0%} P&L={pnl_pct*100:.1f}% 景气{stage} 逢低买入",
                        )
                        if trade:
                            t_trades.append(trade)
                            added_today.add(pos.ts_code)

        return t_trades

    def run_day(self, trade_date: str, factor_scores: dict | None = None) -> dict:
        """Execute one trading day. Returns a summary dict.

        Args:
            trade_date: YYYYMMDD format.
            factor_scores: pre-computed factor scores (for backfill mode).
                If None, factors are computed live (slow but real-time).
        """
        if self.account.has_run_on(trade_date):
            logger.info(f"[{self.account.name}] {trade_date} already executed, skipping")
            return {"status": "skipped", "trade_date": trade_date}

        positions = self.account.get_positions()
        held_codes = [p.ts_code for p in positions]

        # ── 1. Fetch prices ──
        # We need prices for held stocks + any candidates the strategy wants.
        # For factor strategy, we compute factors on a broader set.
        codes_to_price = held_codes[:]

        # ── 2. Get factor/davis scores ──
        if factor_scores is None:
            # Live mode: compute factors for a candidate universe
            # For simplicity, compute for held + some top stocks
            # (In production, this would use the pipeline output)
            factor_scores = {}

        davis_scores = factor_scores.get("_davis_scores", {})
        factor_data = factor_scores.get("_factor_scores", {})

        # Add candidate codes from scores to price list
        for code in list(davis_scores.keys())[:20] + list(factor_data.keys())[:20]:
            if code not in codes_to_price:
                codes_to_price.append(code)

        prices = _get_close_prices(codes_to_price, trade_date)
        if not prices:
            logger.warning(f"[{self.account.name}] {trade_date}: no prices available")
            return {"status": "no_prices", "trade_date": trade_date}

        # ── 2b. Market regime + industry context ──
        market_regime = _get_market_regime(trade_date)
        industries = _get_industries(codes_to_price)
        industry_trend = _infer_industry_trends(factor_data, industries, trade_date=trade_date)

        # ── 2c. Quality data: short momentum, PE percentile, volatility ──
        short_momentum = _compute_short_momentum(codes_to_price, trade_date)
        pe_percentiles = _compute_pe_percentiles(codes_to_price, trade_date)
        volatilities = _compute_volatilities(codes_to_price, trade_date)
        # Volume-price signal — used both by the strategy (composite rating)
        # and by the risk layer (high-position high-volume SELL).
        volume_signals = _compute_volume_signals(codes_to_price, trade_date)
        # Event signals (减持/解禁) — computed when hard filter OR soft penalty is on.
        if getattr(self.strategy, "enable_event_filter", False) or \
           getattr(self.strategy, "event_penalty_weight", 0) > 0:
            event_signals = _compute_event_signals(codes_to_price, trade_date)
        else:
            event_signals = {}
        # Technical factor (tech_score) — only loaded when tech_weight > 0.
        if getattr(self.strategy, "tech_weight", 0) > 0:
            tech_scores = _load_tech_scores(codes_to_price, trade_date)
        else:
            tech_scores = {}

        # ── 3a. Risk management: dynamic + vol-adjusted stop-loss/take-profit ──
        risk_signals = self._check_risk_signals(
            positions, prices, trade_date,
            market_regime=market_regime,
            industries=industries,
            industry_trend=industry_trend,
            volatilities=volatilities,
            volume_signals=volume_signals,
        )
        if risk_signals:
            logger.info(
                f"[{self.account.name}] {trade_date}: {len(risk_signals)} risk signals "
                f"(market={market_regime})"
            )

        # ── 3b. Build snapshot with smart context ──
        stock_names = {c: _get_stock_name(c) for c in codes_to_price}
        total_equity = self.account.market_value(prices)

        snapshot = MarketSnapshot(
            trade_date=trade_date,
            prices=prices,
            davis_scores=davis_scores,
            factor_scores=factor_data,
            stock_names=stock_names,
            market_regime=market_regime,
            industries=industries,
            industry_trend=industry_trend,
            short_momentum=short_momentum,
            pe_percentile=pe_percentiles,
            volatility=volatilities,
            volume_signal=volume_signals,
            event_signal=event_signals,
            tech_score=tech_scores,
        )

        # ── 4. Evaluate strategy ──
        strategy_signals = self.strategy.evaluate(positions, snapshot, total_equity)

        # Merge: risk signals take priority (a stock flagged for stop-loss
        # is sold regardless of what the strategy says)
        risk_codes = {s.ts_code for s in risk_signals if s.action == "SELL"}
        # Filter out strategy HOLD signals for stocks being risk-sold
        signals = risk_signals + [
            s for s in strategy_signals if s.ts_code not in risk_codes
        ]
        logger.info(
            f"[{self.account.name}] {trade_date}: {len(signals)} signals "
            f"({sum(1 for s in signals if s.action == 'BUY')} buy, "
            f"{sum(1 for s in signals if s.action == 'SELL')} sell)"
        )

        # ── 5a. T-trade: trim profits / add on dips (before full SELL/BUY) ──
        # For each held position, check if we should trim (sell partial) or
        # add (buy partial) based on short-term P&L.
        trades = []
        if self.enable_t_trading:
            t_trades = self._execute_t_trades(
                positions, prices, trade_date,
                market_regime=market_regime,
                industries=industries,
                industry_trend=industry_trend,
                factor_data=factor_data,
            )
            trades.extend(t_trades)
            # Refresh positions after T-trades
            positions = self.account.get_positions()

        # ── 5b. Execute main signals (sells first) ──
        # Sells
        for sig in signals:
            if sig.action == "SELL":
                trade = self.account.sell_all(
                    ts_code=sig.ts_code,
                    name=sig.name,
                    price=prices.get(sig.ts_code, 0),
                    trade_date=trade_date,
                    signal_reason=sig.signal_reason,
                )
                if trade:
                    trades.append(trade)

        # Recalculate equity after sells
        total_equity = self.account.market_value(prices)

        # Buys — with limit-up probability adjustment
        for sig in signals:
            if sig.action == "BUY":
                px = prices.get(sig.ts_code)
                if px is None or px <= 0:
                    continue
                # Check if today is a limit-up day → adjust buy probability
                buy_pct = _get_daily_pct_chg(sig.ts_code, trade_date)
                fill_prob = _limit_up_fill_probability(buy_pct)
                if fill_prob <= 0:
                    logger.info(f"[{self.account.name}] {trade_date}: skip {sig.name} — "
                                f"limit-up {buy_pct:+.1f}% fill_prob=0")
                    continue
                target_amount = total_equity * sig.target_weight
                target_shares = int(target_amount / px)
                # Apply probability haircut to share count
                if fill_prob < 1.0:
                    target_shares = int(target_shares * fill_prob)
                    logger.info(f"[{self.account.name}] {trade_date}: {sig.name} "
                                f"pct={buy_pct:+.1f}% fill_prob={fill_prob:.0%} "
                                f"shares {int(target_amount/px)}→{target_shares}")
                if target_shares < 100:
                    continue  # below board lot after haircut
                trade = self.account.buy(
                    ts_code=sig.ts_code,
                    name=sig.name,
                    shares=target_shares,
                    price=px,
                    trade_date=trade_date,
                    signal_reason=sig.signal_reason + (f" | 涨停概率{fill_prob:.0%}" if fill_prob < 1.0 else ""),
                )
                if trade:
                    trades.append(trade)

        # ── 5c. Shadow tracking: record rotation swaps ──
        # When a stock is sold via "轮动换仓" and another is bought on the
        # same day, record the pair for shadow tracking.
        rotation_sells = [s for s in signals if s.action == "SELL" and "轮动" in (s.signal_reason or "")]
        rotation_buys = [s for s in signals if s.action == "BUY" and s not in rotation_sells]
        for sell_sig in rotation_sells:
            # Find the matching buy (from the signal_reason which contains the target name)
            for buy_sig in rotation_buys:
                if buy_sig.ts_code in [sell_sig.ts_code]:
                    continue
                # Record shadow trade
                sold_price = prices.get(sell_sig.ts_code, 0)
                bought_price = prices.get(buy_sig.ts_code, 0)
                if sold_price > 0 and bought_price > 0:
                    # Extract score diff from reason if possible
                    score_diff = 0.0
                    import re
                    m = re.search(r'差值([\d.]+)', sell_sig.signal_reason or "")
                    if m:
                        score_diff = float(m.group(1))
                    _record_shadow_trade(
                        self.account.account_id, trade_date,
                        sell_sig.ts_code, sell_sig.name, sold_price,
                        buy_sig.ts_code, buy_sig.name, bought_price,
                        score_diff,
                    )
                    rotation_buys.remove(buy_sig)
                    break

        # ── 5d. Update shadow tracking for existing records ──
        _update_shadow_tracking(trade_date, prices)

        # ── 6. Record NAV ──
        # Re-fetch prices for all held stocks (may have changed after buys)
        final_positions = self.account.get_positions()
        final_prices = _get_close_prices([p.ts_code for p in final_positions], trade_date)
        nav = self.account.record_nav(trade_date, final_prices)

        return {
            "status": "ok",
            "trade_date": trade_date,
            "signals": len(signals),
            "trades": len(trades),
            "nav": nav.total_equity,
            "daily_return": nav.daily_return,
        }


def run_backfill(
    account: PaperAccount,
    strategy: Strategy,
    start_date: str,
    end_date: str | None = None,
    davis_scores_by_date: dict[str, dict] | None = None,
    factor_scores_by_date: dict[str, dict] | None = None,
) -> list[dict]:
    """Backfill: run the executor over a historical date range.

    Args:
        start_date / end_date: YYYYMMDD format.
        davis_scores_by_date: {date_str: {ts_code: {final_score, rank, name}}}
            Pre-computed point-in-time scores. If None and auto_score=True,
            scores are computed live via score_universe_at + factor engines.
        factor_scores_by_date: {date_str: {ts_code: {momentum, holder, ...}}}
            Pre-computed point-in-time factor scores.
    """
    end_date = end_date or datetime.now().strftime("%Y%m%d")
    trading_days = _get_trading_days(start_date, end_date)

    if not trading_days:
        logger.warning(f"No trading days found between {start_date} and {end_date}")
        return []

    logger.info(
        f"[{account.name}] Backfill {len(trading_days)} days "
        f"({trading_days[0]} → {trading_days[-1]})"
    )

    executor = DailyExecutor(account, strategy)
    results = []

    for i, day in enumerate(trading_days):
        scores = {}
        if davis_scores_by_date and day in davis_scores_by_date:
            scores["_davis_scores"] = davis_scores_by_date[day]
        if factor_scores_by_date and day in factor_scores_by_date:
            scores["_factor_scores"] = factor_scores_by_date[day]

        result = executor.run_day(day, factor_scores=scores if scores else None)
        results.append(result)

        if (i + 1) % 20 == 0:
            logger.info(f"  progress: {i+1}/{len(trading_days)} days")

    return results


# ─── Auto-scoring helpers ───────────────────────────────────────────────


def _compute_davis_scores_at(
    client,
    as_of: date,
    universe: list[str],
    stock_infos: dict,
) -> dict[str, dict]:
    """Compute Davis Double composite scores for *universe* at *as_of* date.

    Uses ``score_universe_at`` from backtest_factors (point-in-time correct).
    Returns ``{ts_code: {"final_score": float, "name": str}}``.
    """
    from davis_analyzer.backtest_factors import score_universe_at

    # Filter stock_infos to the requested universe
    filtered = {c: stock_infos[c] for c in universe if c in stock_infos}
    if not filtered:
        return {}

    raw_scores = score_universe_at(client, as_of, filtered)
    # Rank and format
    ranked = sorted(raw_scores.items(), key=lambda x: x[1], reverse=True)
    result: dict[str, dict] = {}
    for rank, (code, score) in enumerate(ranked, 1):
        name = stock_infos.get(code)
        name_str = name.name if hasattr(name, "name") else str(code)
        result[code] = {"final_score": round(score, 2), "rank": rank, "name": name_str}
    return result


def _compute_factor_scores_at(
    client,
    as_of: date,
    universe: list[str],
) -> dict[str, dict]:
    """Compute supplementary factor scores (momentum/holder/dividend/forecast/prosperity) at *as_of*.

    Returns ``{ts_code: {"momentum": float, "holder": float, "holder_trend": str, ...}}``.
    """
    from davis_analyzer.momentum import analyze_momentum
    from davis_analyzer.holder_concentration import analyze_holder_concentration
    from davis_analyzer.dividend import analyze_dividend
    from davis_analyzer.forecast import analyze_forecast
    from davis_analyzer.financial_fetcher import fetch_financial_data
    from davis_analyzer.prosperity import calculate_prosperity_score
    from davis_analyzer.prosperity_sector import classify_stock_stage

    scores: dict[str, dict] = {}
    for code in universe:
        try:
            entry: dict[str, Any] = {}
            mom = analyze_momentum(client, code, today=as_of)
            if mom:
                entry["momentum"] = mom.momentum_score
            hc = analyze_holder_concentration(client, code, today=as_of)
            if hc:
                entry["holder"] = hc.concentration_score
                entry["holder_trend"] = hc.trend
            div = analyze_dividend(client, code, today=as_of)
            if div:
                entry["dividend"] = div.dividend_score
            fc = analyze_forecast(client, code, today=as_of)
            if fc:
                entry["forecast_leading"] = fc.leading_score
            # Prosperity (景气度 G+ΔG)
            fin = fetch_financial_data(client, code, periods=12)
            if fin and len(fin) >= 2:
                pscore = calculate_prosperity_score(fin)
                entry["prosperity"] = pscore.composite_score
                entry["delta_g"] = pscore.delta_g
                entry["stage"] = classify_stock_stage(pscore)
            if div:
                entry["dividend"] = div.dividend_score
            if entry:
                scores[code] = entry
        except Exception:
            pass
    return scores


def run_backfill_auto(
    account: PaperAccount,
    strategy: Strategy,
    start_date: str,
    end_date: str | None = None,
    universe_codes: list[str] | None = None,
    scoring_frequency: int = 1,
) -> list[dict]:
    """Full-auto backfill: automatically compute factor scores each scoring day.

    This is the one-command backfill — no pre-computed scores needed. It:
    1. Builds a stock universe (from ``universe_codes`` or the full stock list).
    2. Every ``scoring_frequency`` trading days, computes Davis scores + factor
       scores for the universe (point-in-time correct via ``as_of=`` params).
    3. Passes scores to ``run_day`` for strategy evaluation + trade execution.

    Args:
        start_date / end_date: YYYYMMDD.
        universe_codes: explicit stock list to score. If None, uses the top-50
            by cached market cap (avoid full-universe for speed).
        scoring_frequency: re-score every N trading days (default 1 = daily).
    """
    from davis_analyzer.tushare_client import TushareClient

    end_date = end_date or datetime.now().strftime("%Y%m%d")
    trading_days = _get_trading_days(start_date, end_date)
    if not trading_days:
        logger.warning(f"No trading days found between {start_date} and {end_date}")
        return []

    client = TushareClient()

    # Build universe
    if universe_codes is None:
        # Default: use the stock list from market_data.db, take a reasonable set
        repo = get_repository()
        stock_df = repo.get_stock_list()
        if stock_df is not None and not stock_df.empty:
            universe_codes = stock_df["ts_code"].tolist()[:50]  # top 50 for speed
        else:
            universe_codes = []

    # Build stock_infos dict for score_universe_at
    stock_infos: dict = {}
    with get_market_conn() as conn:
        conn.row_factory = sqlite3.Row  # enable name-based access
        for code in universe_codes:
            row = conn.execute(
                "SELECT ts_code, name, industry FROM stock_basic WHERE ts_code=?", (code,)
            ).fetchone()
            if row:
                from davis_analyzer.types import StockInfo

                stock_infos[code] = StockInfo(
                    ts_code=row["ts_code"],
                    name=row["name"],
                    industry=row["industry"] or "",
                    list_status="L",
                    is_cyclical=False,
                )

    logger.info(
        f"[{account.name}] Auto-backfill {len(trading_days)} days, "
        f"universe={len(universe_codes)} stocks, "
        f"scoring every {scoring_frequency} days"
    )

    executor = DailyExecutor(account, strategy)
    results: list[dict] = []
    cached_davis: dict[str, dict] = {}
    cached_factors: dict[str, dict] = {}

    for i, day in enumerate(trading_days):
        as_of = datetime.strptime(day, "%Y%m%d").date()

        # Re-score periodically
        if i % scoring_frequency == 0:
            logger.info(f"  [{day}] Scoring universe ({len(universe_codes)} stocks)...")
            try:
                cached_davis = _compute_davis_scores_at(client, as_of, universe_codes, stock_infos)
                cached_factors = _compute_factor_scores_at(client, as_of, universe_codes)
                logger.info(
                    f"  [{day}] Scored: {len(cached_davis)} davis, {len(cached_factors)} factor"
                )
            except Exception:
                logger.exception(f"  [{day}] Scoring failed")
                cached_davis = {}
                cached_factors = {}

        scores = {
            "_davis_scores": cached_davis,
            "_factor_scores": cached_factors,
        }
        result = executor.run_day(day, factor_scores=scores)
        results.append(result)

        if (i + 1) % 10 == 0:
            nav = result.get("nav", 0)
            logger.info(f"  progress: {i+1}/{len(trading_days)} days, NAV={nav:,.0f}")

    return results


# ── Shadow tracking helpers ────────────────────────────────────────────

_SHADOW_CONFIRM_DAYS = 20  # trading days to judge a rotation swap


def _record_shadow_trade(
    account_id: int,
    trade_date: str,
    sold_code: str, sold_name: str, sold_price: float,
    bought_code: str, bought_name: str, bought_price: float,
    score_diff: float,
) -> None:
    """Record a rotation swap for shadow tracking."""
    with get_connection() as c:
        c.execute(
            "INSERT INTO paper_shadow_trades "
            "(account_id, rotate_date, sold_ts_code, sold_name, sold_price, "
            "bought_ts_code, bought_name, bought_price, score_diff, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'tracking')",
            (account_id, trade_date, sold_code, sold_name, sold_price,
             bought_code, bought_name, bought_price, score_diff),
        )
        c.commit()


def _update_shadow_tracking(trade_date: str, prices: dict[str, float]) -> None:
    """Update shadow trade P&L and confirm those past the threshold.

    For each 'tracking' shadow trade:
    1. Compute sold_return and bought_return from rotate_date prices.
    2. Compute excess_return = bought_return - sold_return.
    3. If ≥20 trading days since rotate_date, mark as confirmed with verdict.
    """
    with get_connection() as c:
        rows = c.execute(
            "SELECT id, rotate_date, sold_ts_code, sold_price, "
            "bought_ts_code, bought_price FROM paper_shadow_trades "
            "WHERE status = 'tracking'"
        ).fetchall()
        if not rows:
            return

        all_dates = _get_trading_days("20260101", trade_date)

        for r in rows:
            rotate_date = r["rotate_date"]
            sold_price = r["sold_price"]
            bought_price = r["bought_price"]

            sold_px = prices.get(r["sold_ts_code"])
            bought_px = prices.get(r["bought_ts_code"])

            sold_ret = ((sold_px / sold_price - 1) * 100) if (sold_px and sold_price > 0) else None
            bought_ret = ((bought_px / bought_price - 1) * 100) if (bought_px and bought_price > 0) else None
            excess = (bought_ret - sold_ret) if (sold_ret is not None and bought_ret is not None) else None

            try:
                idx_rotate = all_dates.index(rotate_date)
                idx_now = all_dates.index(trade_date)
                days_passed = idx_now - idx_rotate
            except (ValueError, IndexError):
                days_passed = 0

            if days_passed >= _SHADOW_CONFIRM_DAYS and excess is not None:
                verdict = "正确" if excess > 0 else "错误"
                c.execute(
                    "UPDATE paper_shadow_trades SET "
                    "status='confirmed', confirm_date=?, "
                    "sold_return=?, bought_return=?, excess_return=?, verdict=? "
                    "WHERE id=?",
                    (trade_date, sold_ret, bought_ret, excess, verdict, r["id"]),
                )
            elif excess is not None:
                c.execute(
                    "UPDATE paper_shadow_trades SET "
                    "sold_return=?, bought_return=?, excess_return=? WHERE id=?",
                    (sold_ret, bought_ret, excess, r["id"]),
                )
        c.commit()
