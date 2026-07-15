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
    """Fetch close prices for multiple stocks on a given date (YYYYMMDD)."""
    if not ts_codes:
        return {}
    repo = get_repository()
    prices = {}
    for code in ts_codes:
        try:
            df = repo.get_daily_prices(code, trade_date, trade_date)
            if df is not None and not df.empty:
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
            scores[code] = entry
        except Exception:
            pass

    return scores


class DailyExecutor:
    """Execute one trading day for a paper-trading account."""

    def __init__(self, account: PaperAccount, strategy: Strategy) -> None:
        self.account = account
        self.strategy = strategy
        self.commission_bps = 2.5
        self.stamp_tax_bps = 10.0

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

        # ── 3. Build snapshot ──
        stock_names = {c: _get_stock_name(c) for c in codes_to_price}
        total_equity = self.account.market_value(prices)

        snapshot = MarketSnapshot(
            trade_date=trade_date,
            prices=prices,
            davis_scores=davis_scores,
            factor_scores=factor_data,
            stock_names=stock_names,
        )

        # ── 4. Evaluate strategy ──
        signals = self.strategy.evaluate(positions, snapshot, total_equity)
        logger.info(
            f"[{self.account.name}] {trade_date}: {len(signals)} signals "
            f"({sum(1 for s in signals if s.action == 'BUY')} buy, "
            f"{sum(1 for s in signals if s.action == 'SELL')} sell)"
        )

        # ── 5. Execute trades (sells first) ──
        trades = []
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

        # Buys
        for sig in signals:
            if sig.action == "BUY":
                px = prices.get(sig.ts_code)
                if px is None or px <= 0:
                    continue
                target_amount = total_equity * sig.target_weight
                target_shares = int(target_amount / px)
                trade = self.account.buy(
                    ts_code=sig.ts_code,
                    name=sig.name,
                    shares=target_shares,
                    price=px,
                    trade_date=trade_date,
                    signal_reason=sig.signal_reason,
                )
                if trade:
                    trades.append(trade)

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
    """Compute supplementary factor scores (momentum/holder/dividend) at *as_of*.

    Returns ``{ts_code: {"momentum": float, "holder": float, "holder_trend": str, ...}}``.
    """
    from davis_analyzer.momentum import analyze_momentum
    from davis_analyzer.holder_concentration import analyze_holder_concentration
    from davis_analyzer.dividend import analyze_dividend

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
    scoring_frequency: int = 5,
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
        scoring_frequency: re-score every N trading days (default 5 = weekly).
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
