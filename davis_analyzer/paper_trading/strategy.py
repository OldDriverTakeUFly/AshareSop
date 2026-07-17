"""Trading strategies for the paper-trading system.

Each strategy receives a snapshot of the current portfolio + factor signals +
prices, and returns a list of :class:`Signal` objects (buy/sell/hold). The
executor then acts on these signals.

Two strategies are provided:

1. **DavisDoubleStrategy** — periodic equal-weight rotation into the top-N
   stocks by Davis Double ``final_score``. Rebalances every *frequency* trading
   days. This is the simplest "does the 4-dimension scoring work?" test.

2. **FactorThresholdStrategy** — daily check using the supplementary factor
   engines: buy when momentum is strong + holders are accumulating; sell when
   momentum collapses or holders distribute. This tests the factor signals
   identified in our research reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from davis_analyzer.paper_trading.account import Position


@dataclass
class Signal:
    """A trading signal produced by a strategy."""

    ts_code: str
    name: str
    action: str  # "BUY" / "SELL" / "HOLD"
    target_weight: float = 0.0  # fraction of total equity (for BUY)
    signal_reason: str = ""


@dataclass
class MarketSnapshot:
    """Context passed to strategies on each evaluation."""

    trade_date: str  # YYYYMMDD
    prices: dict[str, float]  # ts_code → close price
    # Factor scores (all optional — strategies choose which to use)
    davis_scores: dict[str, dict] = field(default_factory=dict)
    # davis_scores[ts_code] = {"final_score": float, "rank": int, "name": str}
    factor_scores: dict[str, dict] = field(default_factory=dict)
    # factor_scores[ts_code] = {"momentum": float, "holder": float, "dividend": float, ...}
    stock_names: dict[str, str] = field(default_factory=dict)
    # ts_code → name
    # ── Smart strategy context (enhancement) ──
    market_regime: str = "mixed"  # "bull" / "bear" / "mixed"
    industries: dict[str, str] = field(default_factory=dict)  # ts_code → industry
    industry_trend: dict[str, str] = field(default_factory=dict)  # industry → "up"/"down"/"flat"


class Strategy(Protocol):
    """Protocol for trading strategies."""

    name: str

    def evaluate(
        self,
        positions: list[Position],
        snapshot: MarketSnapshot,
        total_equity: float,
    ) -> list[Signal]:
        """Produce trading signals for this cycle."""
        ...


# ─── Strategy 1: Davis Double Equal-Weight Rotation ─────────────────────


class DavisDoubleStrategy:
    """Rotate into top-N stocks by Davis Double final_score every N days.

    Config:
        top_n: number of stocks to hold (equal-weight)
        frequency: rebalance every N trading days
        min_score: minimum final_score to buy (filter)
    """

    name = "davis_double"

    def __init__(
        self,
        top_n: int = 10,
        frequency: int = 5,
        min_score: float = 50.0,
    ) -> None:
        self.top_n = top_n
        self.frequency = frequency
        self.min_score = min_score
        self._day_count = 0

    def evaluate(
        self,
        positions: list[Position],
        snapshot: MarketSnapshot,
        total_equity: float,
    ) -> list[Signal]:
        self._day_count += 1
        if self._day_count % self.frequency != 0:
            # Not a rebalance day — hold everything
            return [
                Signal(ts_code=p.ts_code, name=p.name, action="HOLD")
                for p in positions
            ]

        # Rank stocks by final_score
        ranked = sorted(
            snapshot.davis_scores.items(),
            key=lambda x: x[1].get("final_score", 0),
            reverse=True,
        )
        # Filter by min_score and price availability
        targets = []
        for code, info in ranked[: self.top_n * 2]:  # get some buffer
            if info.get("final_score", 0) < self.min_score:
                continue
            if code not in snapshot.prices or snapshot.prices[code] <= 0:
                continue
            targets.append((code, info))

        target_codes = {c for c, _ in targets[: self.top_n]}
        held_codes = {p.ts_code for p in positions}
        weight = 1.0 / max(len(target_codes), 1) if target_codes else 0.0

        signals: list[Signal] = []

        # Sell positions not in target set
        for pos in positions:
            if pos.ts_code not in target_codes:
                signals.append(
                    Signal(
                        ts_code=pos.ts_code,
                        name=pos.name,
                        action="SELL",
                        signal_reason=f"跌出top{self.top_n}",
                    )
                )

        # Buy new targets
        for code, info in targets[: self.top_n]:
            name = info.get("name", snapshot.stock_names.get(code, code))
            if code not in held_codes:
                signals.append(
                    Signal(
                        ts_code=code,
                        name=name,
                        action="BUY",
                        target_weight=weight,
                        signal_reason=f"final_score={info.get('final_score', 0):.1f} top{self.top_n}",
                    )
                )

        return signals


# ─── Strategy 2: Factor Threshold (momentum + holder) ────────────────────


class FactorThresholdStrategy:
    """Daily factor-threshold strategy with market gate + sector rotation.

    Four-dimension stock selection (upgraded from 2D momentum+holder):
      1. **Momentum** — price trend strength (primary)
      2. **Holder** — chip concentration (institutional accumulation)
      3. **Dividend** — payout continuity (fundamental stability)
      4. **Forecast** — earnings pre-announcement leading score (forward-looking)

    A stock qualifies if it passes the momentum gate (primary) AND at least
    one of the three secondary dimensions (holder/dividend/forecast). This
    "1 primary + 1 secondary" rule broadens the candidate pool significantly
    vs the old "momentum AND holder" dual gate.

    Sell when: momentum collapses, OR all secondary dimensions fail,
    OR sector trend turns down.
    """

    name = "factor_threshold"

    def __init__(
        self,
        max_positions: int = 10,
        buy_momentum: float = 70.0,
        sell_momentum: float = 40.0,
        buy_holder_min: float = 40.0,
        buy_dividend_min: float = 55.0,
        buy_forecast_min: float = 70.0,
    ) -> None:
        self.max_positions = max_positions
        self.buy_momentum = buy_momentum
        self.sell_momentum = sell_momentum
        self.buy_holder_min = buy_holder_min
        self.buy_dividend_min = buy_dividend_min
        self.buy_forecast_min = buy_forecast_min

    def _effective_max_positions(self, market_regime: str) -> int:
        """Reduce position cap in bear/mixed markets."""
        if market_regime == "bear":
            return 0  # no new buys in bear market
        if market_regime == "mixed":
            return max(1, self.max_positions // 2)
        return self.max_positions

    def evaluate(
        self,
        positions: list[Position],
        snapshot: MarketSnapshot,
        total_equity: float,
    ) -> list[Signal]:
        signals: list[Signal] = []

        # ── 1. Scan ALL candidates with 4-dimension scoring ──
        # A stock qualifies if momentum passes (primary gate) AND at least
        # one secondary dimension (holder/dividend/forecast) passes.
        held_codes = {p.ts_code for p in positions}
        all_qualified: list[tuple] = []  # (code, composite_score, details_str, industry)

        for code, factors in snapshot.factor_scores.items():
            if code not in snapshot.prices or snapshot.prices[code] <= 0:
                continue
            mom = factors.get("momentum")
            if mom is None or mom <= self.buy_momentum:
                continue

            # Sector filter
            industry = snapshot.industries.get(code, "")
            sector_trend = snapshot.industry_trend.get(industry, "flat")
            if sector_trend == "down":
                continue

            # Check secondary dimensions
            holder = factors.get("holder")
            dividend = factors.get("dividend")
            forecast = factors.get("forecast_leading") or factors.get("leading")

            secondary_pass = []
            secondary_details = []
            if holder is not None and holder > self.buy_holder_min:
                secondary_pass.append("holder")
                secondary_details.append(f"筹码{holder:.0f}")
            if dividend is not None and dividend > self.buy_dividend_min:
                secondary_pass.append("dividend")
                secondary_details.append(f"红利{dividend:.0f}")
            if forecast is not None and forecast > self.buy_forecast_min:
                secondary_pass.append("forecast")
                secondary_details.append(f"前瞻{forecast:.0f}")

            if not secondary_pass:
                continue  # must pass at least one secondary dimension

            # Composite score: momentum weighted 50%, best secondary 50%
            best_secondary = max(
                holder or 0, dividend or 0, forecast or 0
            )
            composite = mom * 0.5 + best_secondary * 0.5
            detail_str = f"动量{mom:.0f} " + " ".join(secondary_details)
            all_qualified.append((code, composite, detail_str, industry))

        # Rank by composite score descending
        all_qualified.sort(key=lambda x: x[1], reverse=True)

        # ── 2. Check existing positions for sell signals ──
        for pos in positions:
            factors = snapshot.factor_scores.get(pos.ts_code, {})
            mom = factors.get("momentum")
            holder = factors.get("holder")
            holder_trend = factors.get("holder_trend", "")
            dividend = factors.get("dividend")
            forecast = factors.get("forecast_leading") or factors.get("leading")
            industry = snapshot.industries.get(pos.ts_code, "")
            sector_trend = snapshot.industry_trend.get(industry, "flat")

            reasons: list[str] = []
            should_sell = False

            # Primary sell: momentum collapse
            if mom is not None and mom < self.sell_momentum:
                should_sell = True
                reasons.append(f"动量{mom:.0f}<{self.sell_momentum}")

            # Secondary sell: ALL secondary dimensions failing
            holder_ok = holder is not None and holder > self.buy_holder_min
            div_ok = dividend is not None and dividend > self.buy_dividend_min
            fc_ok = forecast is not None and forecast > self.buy_forecast_min
            if not (holder_ok or div_ok or fc_ok):
                # Only sell if not already flagged by momentum
                if not should_sell:
                    should_sell = True
                    fails = []
                    if holder is not None: fails.append(f"筹码{holder:.0f}")
                    if dividend is not None: fails.append(f"红利{dividend:.0f}")
                    reasons.append(f"次维度全_fail({','.join(fails)})")

            # Holder distribution (hard sell)
            if holder is not None and holder <= 0 and "集中" not in holder_trend:
                should_sell = True
                reasons.append(f"筹码score={holder:.0f}分散")

            # Sector rotation
            if sector_trend == "down":
                should_sell = True
                reasons.append(f"行业{industry}景气走弱，切换赛道")

            if should_sell:
                signals.append(
                    Signal(
                        ts_code=pos.ts_code,
                        name=pos.name,
                        action="SELL",
                        signal_reason="；".join(reasons),
                    )
                )

        # ── 3. Market gate ──
        effective_max = self._effective_max_positions(snapshot.market_regime)
        if effective_max == 0:
            return signals

        # ── 4. Optimal portfolio: keep best N qualified stocks ──
        # The target portfolio is the top `effective_max` qualified stocks.
        # If a held stock is NOT in the top effective_max, it should be
        # replaced — BUT only if the replacement is meaningfully better
        # (margin > swap_threshold) to avoid excessive churn.
        swap_threshold = 5.0  # momentum points; don't swap if difference < 5

        target_codes = {c for c, _, _, _ in all_qualified[:effective_max]}
        # Also include qualified stocks already held (even if below rank cutoff)
        qualified_codes = {c for c, _, _, _ in all_qualified}

        position_weight = 1.0 / effective_max

        # Sell held stocks that are no longer qualified at all
        # (skip if already flagged for sell above by factor/sector checks)
        already_selling = {s.ts_code for s in signals if s.action == "SELL"}
        for pos in positions:
            if pos.ts_code not in qualified_codes and pos.ts_code not in already_selling:
                signals.append(
                    Signal(
                        ts_code=pos.ts_code,
                        name=pos.name,
                        action="SELL",
                        signal_reason="不再符合因子门槛，优化持仓",
                    )
                )
                held_codes.discard(pos.ts_code)  # will be replaced

        # For held stocks that are qualified but dropped from top-N:
        # sell only if the marginal replacement is significantly better
        for pos in positions:
            if pos.ts_code in qualified_codes and pos.ts_code not in target_codes:
                for code, score, details, industry in all_qualified:
                    if code not in held_codes and code in target_codes:
                        held_score = next(
                            (s for c, s, _, _ in all_qualified if c == pos.ts_code), 0
                        )
                        if score - held_score > swap_threshold:
                            signals.append(
                                Signal(
                                    ts_code=pos.ts_code,
                                    name=pos.name,
                                    action="SELL",
                                    signal_reason=f"优化换仓: 替换为{snapshot.stock_names.get(code, code)}(综合分{score:.0f}>{held_score:.0f})",
                                )
                            )
                            held_codes.discard(pos.ts_code)
                        break

        # Buy new target stocks that aren't held
        for code, score, details, industry in all_qualified[:effective_max]:
            if code not in held_codes:
                name = snapshot.stock_names.get(code, code)
                sector_note = f" 行业{industry}↑" if industry else ""
                signals.append(
                    Signal(
                        ts_code=code,
                        name=name,
                        action="BUY",
                        target_weight=position_weight,
                        signal_reason=f"{details}{sector_note}",
                    )
                )
                held_codes.add(code)

        return signals


# ─── Registry ────────────────────────────────────────────────────────────


STRATEGY_REGISTRY: dict[str, type] = {
    "davis_double": DavisDoubleStrategy,
    "factor_threshold": FactorThresholdStrategy,
}


def create_strategy(name: str, config: dict) -> Strategy:
    """Create a strategy instance by name."""
    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown strategy '{name}'. Available: {list(STRATEGY_REGISTRY)}"
        )
    return cls(**config)
