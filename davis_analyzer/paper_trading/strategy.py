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
    """Daily factor-threshold strategy using supplementary engines.

    Buy when: momentum_score > buy_momentum AND holder trend is "集中"
    Sell when: momentum_score < sell_momentum OR holder_score == 0 (distribution)

    Config:
        max_positions: maximum concurrent positions
        buy_momentum: momentum threshold to trigger buy
        sell_momentum: momentum threshold to trigger sell
        position_weight: equal weight per position (1/max_positions)
    """

    name = "factor_threshold"

    def __init__(
        self,
        max_positions: int = 10,
        buy_momentum: float = 70.0,
        sell_momentum: float = 40.0,
        buy_holder_min: float = 40.0,
    ) -> None:
        self.max_positions = max_positions
        self.buy_momentum = buy_momentum
        self.sell_momentum = sell_momentum
        self.buy_holder_min = buy_holder_min
        self.position_weight = 1.0 / max_positions

    def evaluate(
        self,
        positions: list[Position],
        snapshot: MarketSnapshot,
        total_equity: float,
    ) -> list[Signal]:
        signals: list[Signal] = []

        # ── Check existing positions for sell signals ──
        for pos in positions:
            factors = snapshot.factor_scores.get(pos.ts_code, {})
            mom = factors.get("momentum")
            holder = factors.get("holder")
            holder_trend = factors.get("holder_trend", "")

            reasons = []
            should_sell = False

            if mom is not None and mom < self.sell_momentum:
                should_sell = True
                reasons.append(f"动量{mom:.0f}<{self.sell_momentum}")

            if holder is not None and holder <= 0 and "集中" not in holder_trend:
                should_sell = True
                reasons.append(f"筹码score={holder:.0f}分散")

            if should_sell:
                signals.append(
                    Signal(
                        ts_code=pos.ts_code,
                        name=pos.name,
                        action="SELL",
                        signal_reason="；".join(reasons),
                    )
                )

        # ── Scan for buy candidates ──
        held_codes = {p.ts_code for p in positions}
        current_count = len(positions)
        slots_available = self.max_positions - current_count

        if slots_available > 0:
            candidates = []
            for code, factors in snapshot.factor_scores.items():
                if code in held_codes:
                    continue
                if code not in snapshot.prices or snapshot.prices[code] <= 0:
                    continue

                mom = factors.get("momentum")
                holder = factors.get("holder")

                if mom is not None and mom > self.buy_momentum:
                    if holder is not None and holder > self.buy_holder_min:
                        candidates.append((code, mom, holder))

            # Sort by momentum descending, take top slots
            candidates.sort(key=lambda x: x[1], reverse=True)
            for code, mom, holder in candidates[:slots_available]:
                name = snapshot.stock_names.get(code, code)
                signals.append(
                    Signal(
                        ts_code=code,
                        name=name,
                        action="BUY",
                        target_weight=self.position_weight,
                        signal_reason=f"动量{mom:.0f}>{self.buy_momentum} 筹码{holder:.0f}>{self.buy_holder_min}",
                    )
                )

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
