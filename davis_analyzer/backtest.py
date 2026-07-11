"""Periodic-rebalance backtest engine for factor-based strategies.

Core loop (定期调仓 model)::

    for each rebalance_date in trading_dates[::frequency]:
        1. score every stock at *as_of = rebalance_date* (point-in-time)
        2. pick top-N by score → target holdings
        3. execute on the *next* trading day:
             sell positions that dropped out of top-N
             buy new top-N entrants (equal-weight from freed cash)
        4. record trades + mark-to-market daily

The engine reuses existing davis_analyzer data and factor layers and never
issues live API calls for price data during the simulation — all OHLC is
served from the SQLite cache populated by ``TushareClient``.

Trading calendar: derived from the cached daily prices of a *calendar
anchor* stock (default ``000001.SH``, the SSE Composite Index).  Any stock
with a dense daily-price cache over the backtest window works as an anchor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd
from loguru import logger

from davis_analyzer.backtest_factors import FactorConfig, score_universe_at
from davis_analyzer.tushare_client import TushareClient


# ──────────────────────────── configuration ────────────────────────────


@dataclass
class BacktestConfig:
    """All knobs for a single backtest run.

    Attributes:
        start_date / end_date: inclusive calendar-date bounds for the run.
        universe: explicit ts_code list.  ``None`` means "all cached stocks".
        frequency: rebalance every N *trading* days.
        top_n: number of holdings held at each rebalance (equal-weight).
        execution_price: ``"open"`` or ``"close"`` — which OHLC field fills
            orders at on the execution day.
        initial_capital: starting cash in CNY.
        commission_bps: broker commission in basis points (per trade).
        stamp_tax_bps: stamp duty in basis points (sell side only).
        calendar_anchor: ts_code whose cached daily prices define the trading
            calendar.  Defaults to the SSE Composite Index.
    """

    start_date: date
    end_date: date
    universe: list[str] | None = None
    frequency: int = 5
    top_n: int = 10
    execution_price: str = "open"
    initial_capital: float = 1_000_000.0
    commission_bps: float = 2.5
    stamp_tax_bps: float = 10.0
    calendar_anchor: str = "000001.SH"
    factor_config: FactorConfig = field(default_factory=FactorConfig)


# ──────────────────────────── portfolio types ────────────────────────────


@dataclass
class Position:
    """A held position: share count and average cost basis."""

    shares: int
    cost_basis: float  # average price per share including costs


@dataclass
class Trade:
    """One executed order.

    ``amount`` is the gross notional (price × shares); ``cost`` is the sum
    of commission + stamp duty for this trade.
    """

    signal_date: date
    exec_date: date
    ts_code: str
    action: str  # "BUY" or "SELL"
    price: float
    shares: int
    amount: float
    cost: float


@dataclass
class EquitySnapshot:
    """Daily mark-to-market snapshot of the portfolio."""

    date: date
    equity: float
    cash: float
    positions_value: float


@dataclass
class BacktestResult:
    """Full output of a backtest run."""

    config: BacktestConfig
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[EquitySnapshot] = field(default_factory=list)
    rebalance_dates: list[date] = field(default_factory=list)
    # Final portfolio state.
    final_cash: float = 0.0
    final_positions: dict[str, Position] = field(default_factory=dict)


# ──────────────────────────── portfolio engine ────────────────────────────


class Portfolio:
    """Cash + positions with rebalance and mark-to-market operations."""

    def __init__(self, initial_capital: float) -> None:
        self.cash: float = initial_capital
        self.positions: dict[str, Position] = {}

    def market_value(self, prices: dict[str, float]) -> float:
        """Total equity = cash + sum(shares × price) for held positions."""
        val = self.cash
        for code, pos in self.positions.items():
            px = prices.get(code)
            if px is not None:
                val += pos.shares * px
        return val

    def rebalance(
        self,
        target_codes: list[str],
        exec_prices: dict[str, float],
        signal_date: date,
        exec_date: date,
        commission_bps: float,
        stamp_tax_bps: float,
    ) -> list[Trade]:
        """Rotate holdings toward *target_codes* using *exec_prices*.

        Execution order (same trading day):
          1. Sell everything not in the target set first (frees cash).
          2. Buy into the target set equal-weight using total available
             capital divided by ``top_n``.

        This guarantees we never over-spend: sells land before buys.

        Stocks with no executable price on *exec_date* (suspended / missing
        data) are skipped — existing positions in them are carried forward
        and target entries are dropped from this rebalance.  This mirrors
        real-world limits: you cannot trade a halted stock.
        """
        trades: list[Trade] = []
        held_codes = set(self.positions.keys())
        target_set = set(target_codes)

        # ── Phase 1: sell positions that dropped out of the target set ──
        to_sell = held_codes - target_set
        for code in sorted(to_sell):
            px = exec_prices.get(code)
            if px is None or px <= 0:
                continue  # can't sell a halted stock — carry forward
            pos = self.positions[code]
            gross = pos.shares * px
            cost = _trade_cost(gross, commission_bps, stamp_tax_bps, is_sell=True)
            self.cash += gross - cost
            trades.append(
                Trade(
                    signal_date=signal_date,
                    exec_date=exec_date,
                    ts_code=code,
                    action="SELL",
                    price=px,
                    shares=pos.shares,
                    amount=gross,
                    cost=cost,
                )
            )
            del self.positions[code]

        # ── Phase 2: buy into the target set (equal-weight) ──
        # Re-derive the effective target: codes we should hold after this
        # rebalance.  Codes we could not sell (halted) stay in the book, so
        # they consume capital and we buy fewer new names.
        buyable_targets = [c for c in target_codes if exec_prices.get(c) is not None]
        # Determine which target codes we still need to buy.
        to_buy = [c for c in buyable_targets if c not in self.positions]

        if not to_buy:
            return trades

        # Equal-weight target: total equity / top_n gives the per-slot
        # allocation.  We use current total equity (after sells) so freed
        # cash is redeployed.
        total_equity = self.market_value(exec_prices)
        per_slot = total_equity / len(target_codes)

        for code in to_buy:
            px = exec_prices.get(code)
            if px is None or px <= 0:
                continue
            # Buy with whole lots of 100 shares (A-share board lot).
            shares = int(per_slot / px // 100) * 100
            if shares <= 0:
                continue
            gross = shares * px
            cost = _trade_cost(gross, commission_bps, stamp_tax_bps, is_sell=False)
            if gross + cost > self.cash:
                # Trim to what we can afford (still board-lot aligned).
                affordable = int((self.cash / (px * (1 + commission_bps / 1e4))) // 100) * 100
                if affordable <= 0:
                    continue
                shares = affordable
                gross = shares * px
                cost = _trade_cost(gross, commission_bps, stamp_tax_bps, is_sell=False)
            self.cash -= gross + cost
            self.positions[code] = Position(shares=shares, cost_basis=gross / shares)
            trades.append(
                Trade(
                    signal_date=signal_date,
                    exec_date=exec_date,
                    ts_code=code,
                    action="BUY",
                    price=px,
                    shares=shares,
                    amount=gross,
                    cost=cost,
                )
            )

        return trades


def _trade_cost(
    gross: float, commission_bps: float, stamp_tax_bps: float, is_sell: bool
) -> float:
    """Commission applies both sides; stamp duty only on sells (A-share rule)."""
    commission = gross * commission_bps / 1e4
    stamp = gross * stamp_tax_bps / 1e4 if is_sell else 0.0
    return commission + stamp


# ──────────────────────────── price access ────────────────────────────


def _get_trading_calendar(client: TushareClient, config: BacktestConfig) -> list[date]:
    """Derive the sorted trading-day list from the calendar anchor's cache.

    Uses the anchor stock's cached daily prices (which are dense on trading
    days) rather than a separate calendar API.  Raises if the anchor has no
    cached data in the window.
    """
    start = config.start_date.strftime("%Y%m%d")
    end = config.end_date.strftime("%Y%m%d")
    df = client.get_daily_prices(config.calendar_anchor, start, end)
    if df is None or df.empty:
        # Fall back to any stock that has data — the anchor may be uncached.
        logger.warning(
            "Calendar anchor {} has no cached prices in [{} {}]; "
            "falling back to union of all cached trade dates",
            config.calendar_anchor,
            start,
            end,
        )
        return _calendar_from_union(client, config)
    dates = sorted(pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.date.unique())
    return list(dates)


def _calendar_from_union(client: TushareClient, config: BacktestConfig) -> list[date]:
    """Last-resort calendar: union of all trade dates present in the price cache."""
    import sqlite3

    from davis_analyzer.tushare_client import _CACHE_DB

    start = config.start_date.strftime("%Y%m%d")
    end = config.end_date.strftime("%Y%m%d")
    with sqlite3.connect(str(_CACHE_DB)) as conn:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM daily_price_cache "
            "WHERE trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
            (start, end),
        ).fetchall()
    return [pd.to_datetime(r[0], format="%Y%m%d").date() for r in rows]


def _exec_prices_for_date(
    client: TushareClient, codes: list[str], trade_date: date, price_field: str
) -> dict[str, float]:
    """Fetch the execution price for every *code* on *trade_date*.

    Returns ``{ts_code: price}``.  Stocks with no data on that day
    (suspended) are omitted — callers treat absence as "untradeable".

    Uses unadjusted prices because the trade is a one-day execution, not a
    multi-period return calc.
    """
    date_str = trade_date.strftime("%Y%m%d")
    prices: dict[str, float] = {}
    for code in codes:
        df = client.get_daily_prices(code, date_str, date_str)
        if df is None or df.empty:
            continue
        row = df.iloc[0]
        px = row.get(price_field)
        if pd.notna(px) and px is not None and px > 0:
            prices[code] = float(px)
    return prices


def _mtm_prices_for_date(
    client: TushareClient, codes: list[str], trade_date: date
) -> dict[str, float]:
    """Close prices for mark-to-market (always uses ``close``)."""
    return _exec_prices_for_date(client, codes, trade_date, "close")


# ──────────────────────────── main loop ────────────────────────────


def run_backtest(
    config: BacktestConfig, client: TushareClient
) -> BacktestResult:
    """Execute a periodic-rebalance backtest.

    Pipeline:
      1. Build the trading calendar from the anchor's cached prices.
      2. Resolve the stock universe (explicit list or all cached codes).
      3. Walk trading days: on rebalance days score → rebalance → execute
         next day; every day mark-to-market the book.

    No live API calls for *price* data are issued during the walk — all
    prices come from cache.  Factor scoring may trigger incremental cache
    fills on first run, but subsequent runs are pure cache reads.
    """
    logger.info(
        "Backtest start: [{} → {}], freq={}d, top_n={}, exec={}",
        config.start_date,
        config.end_date,
        config.frequency,
        config.top_n,
        config.execution_price,
    )

    # ── 1. Trading calendar ──
    calendar = _get_trading_calendar(client, config)
    if len(calendar) < 2:
        logger.error("Trading calendar too short ({} days) — aborting", len(calendar))
        return BacktestResult(config=config)
    logger.info("Trading calendar: {} days ({} → {})", len(calendar), calendar[0], calendar[-1])

    # ── 2. Stock universe ──
    if config.universe is not None:
        universe_codes = list(config.universe)
    else:
        universe_codes = _all_cached_stock_codes()
    logger.info("Universe: {} stocks", len(universe_codes))

    # We need StockInfo for factor scoring (industry for cyclical flag).
    stock_infos = _build_stock_infos(client, universe_codes)

    # ── 3. Rebalance schedule ──
    rebalance_dates = calendar[:: config.frequency]
    logger.info("Rebalance dates: {} (every {} trading days)", len(rebalance_dates), config.frequency)

    result = BacktestResult(config=config, rebalance_dates=rebalance_dates)
    portfolio = Portfolio(config.initial_capital)

    # Map rebalance signal date → next trading day (execution day).
    cal_index = {d: i for i, d in enumerate(calendar)}

    # ── 4. Daily walk ──
    rebalance_signals: dict[date, list[str]] = {}
    pending_signals: list[tuple[date, list[str]]] = []

    for today in calendar:
        # Execute any pending rebalance signal scheduled for today.
        exec_for_today = [sig for sig in pending_signals if sig[0] == today]
        pending_signals = [sig for sig in pending_signals if sig[0] != today]

        for _signal_date, target_codes in exec_for_today:
            all_codes = set(target_codes) | set(portfolio.positions.keys())
            exec_prices = _exec_prices_for_date(
                client, list(all_codes), today, config.execution_price
            )
            trades = portfolio.rebalance(
                target_codes=target_codes,
                exec_prices=exec_prices,
                signal_date=_signal_date,
                exec_date=today,
                commission_bps=config.commission_bps,
                stamp_tax_bps=config.stamp_tax_bps,
            )
            result.trades.extend(trades)

        # On a rebalance signal day, compute the target set for *next* day.
        if today in cal_index and today in rebalance_dates:
            scores = score_universe_at(
                client, today, stock_infos, config.factor_config
            )
            ranked = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)
            target = ranked[: config.top_n]
            # Schedule execution for the next trading day.
            idx = cal_index[today]
            if idx + 1 < len(calendar):
                exec_day = calendar[idx + 1]
                pending_signals.append((exec_day, target))
                rebalance_signals[today] = target
                logger.info(
                    "Rebalance signal {}: top-{} = {}, exec on {}",
                    today,
                    config.top_n,
                    target[:5],
                    exec_day,
                )

        # Mark-to-market every day.
        held_codes = list(portfolio.positions.keys())
        mtm_prices = _mtm_prices_for_date(client, held_codes, today)
        equity = portfolio.market_value(mtm_prices)
        positions_value = equity - portfolio.cash
        result.equity_curve.append(
            EquitySnapshot(
                date=today,
                equity=equity,
                cash=portfolio.cash,
                positions_value=positions_value,
            )
        )

    result.final_cash = portfolio.cash
    result.final_positions = portfolio.positions
    logger.info(
        "Backtest complete: {} trades, {} equity snapshots, final equity ≈ {:.0f}",
        len(result.trades),
        len(result.equity_curve),
        result.equity_curve[-1].equity if result.equity_curve else 0.0,
    )
    return result


# ──────────────────────────── helpers ────────────────────────────


def _all_cached_stock_codes() -> list[str]:
    """Return every ts_code that has at least one cached daily price row."""
    import sqlite3

    from davis_analyzer.tushare_client import _CACHE_DB

    with sqlite3.connect(str(_CACHE_DB)) as conn:
        rows = conn.execute(
            "SELECT DISTINCT ts_code FROM daily_price_cache"
        ).fetchall()
    return [r[0] for r in rows]


def _build_stock_infos(
    client: TushareClient, codes: list[str]
) -> dict[str, "StockInfo"]:
    """Build a StockInfo map for the universe (industry for cyclical flag).

    Falls back to ``is_cyclical=False`` when industry data is unavailable.
    """
    from davis_analyzer.types import StockInfo

    infos: dict[str, StockInfo] = {}
    try:
        df = client.get_stock_list()
        industry_map: dict[str, str] = {}
        name_map: dict[str, str] = {}
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                industry_map[row["ts_code"]] = row.get("industry", "")
                name_map[row["ts_code"]] = row.get("name", "")
    except Exception:
        industry_map = {}
        name_map = {}

    from davis_analyzer.constants import CYCLICAL_INDUSTRIES

    cyclical_set = set(CYCLICAL_INDUSTRIES)
    for code in codes:
        industry = industry_map.get(code, "")
        infos[code] = StockInfo(
            ts_code=code,
            name=name_map.get(code, ""),
            industry=industry,
            list_status="L",
            is_cyclical=industry in cyclical_set,
        )
    return infos
