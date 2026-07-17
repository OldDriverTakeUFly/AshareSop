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


def _infer_industry_trends(
    factor_data: dict[str, dict], industries: dict[str, str]
) -> dict[str, str]:
    """Infer per-industry price trend from average momentum of scored stocks.

    Uses the momentum scores already computed for stocks in each industry.
    avg_momentum > 55 → "up", < 45 → "down", else "flat".
    """
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
        self, market_regime: str, sector_trend: str
    ) -> tuple[float, float]:
        """Look up dynamic stop-loss / take-profit for this position."""
        return self._RISK_RULES.get(
            (market_regime, sector_trend), self._DEFAULT_RISK
        )

    def _check_risk_signals(
        self,
        positions: list[Position],
        prices: dict[str, float],
        trade_date: str,
        market_regime: str = "mixed",
        industries: dict[str, str] | None = None,
        industry_trend: dict[str, str] | None = None,
    ) -> list[Signal]:
        """Check stop-loss / take-profit for every holding with DYNAMIC thresholds.

        Thresholds adapt to market regime + sector trend:
        - Bear market + declining sector → tight 7% stop, no take-profit
        - Bull market + rising sector → wide 12% stop, 30% take-profit
        - etc. (see _RISK_RULES table)
        """
        industries = industries or {}
        industry_trend = industry_trend or {}
        signals: list[Signal] = []
        for pos in positions:
            px = prices.get(pos.ts_code)
            if px is None or px <= 0:
                continue
            pnl_pct = (px / pos.avg_cost - 1) if pos.avg_cost > 0 else 0

            # Dynamic thresholds based on market + sector
            industry = industries.get(pos.ts_code, "")
            sector_trend = industry_trend.get(industry, "flat")
            hard_stop, take_profit = self._get_risk_thresholds(market_regime, sector_trend)

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
        industry_trend = _infer_industry_trends(factor_data, industries)

        # ── 3a. Risk management: dynamic stop-loss / take-profit ──
        risk_signals = self._check_risk_signals(
            positions, prices, trade_date,
            market_regime=market_regime,
            industries=industries,
            industry_trend=industry_trend,
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
