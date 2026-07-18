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


def _days_between(date_str1: str, date_str2: str) -> int:
    """Approximate calendar days between two YYYYMMDD strings."""
    try:
        from datetime import datetime as _dt
        d1 = _dt.strptime(date_str1, "%Y%m%d")
        d2 = _dt.strptime(date_str2, "%Y%m%d")
        return abs((d2 - d1).days)
    except (ValueError, TypeError):
        return 999  # treat invalid as far apart


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
    # ── Short-term momentum + valuation (added for quality filtering) ──
    short_momentum: dict[str, float] = field(default_factory=dict)  # ts_code → 5-day return %
    pe_percentile: dict[str, float] = field(default_factory=dict)  # ts_code → PE historical percentile (0-100)
    volatility: dict[str, float] = field(default_factory=dict)  # ts_code → 20-day annualized vol %


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
        buy_prosperity_min: float = 45.0,
        min_secondary_dims: int = 1,
        max_single_position_pct: float = 12.0,
        rotatable_ratio: float = 0.4,
        rotation_threshold: float = 15.0,
        # ── Quality filters ──
        require_short_momentum: bool = True,  # 5-day return must be > 0
        max_pe_percentile: float = 80.0,      # PE must be below 80th percentile
        vol_adjusted_stops: bool = True,      # Adjust stop-loss by individual volatility
    ) -> None:
        self.max_positions = max_positions
        self.buy_momentum = buy_momentum
        self.sell_momentum = sell_momentum
        self.buy_holder_min = buy_holder_min
        self.buy_dividend_min = buy_dividend_min
        self.buy_forecast_min = buy_forecast_min
        self.buy_prosperity_min = buy_prosperity_min
        self.min_secondary_dims = min_secondary_dims
        self.max_single_position_pct = max_single_position_pct
        self.rotatable_ratio = rotatable_ratio
        self.rotation_threshold = rotation_threshold
        # Quality filters
        self.require_short_momentum = require_short_momentum
        self.max_pe_percentile = max_pe_percentile
        self.vol_adjusted_stops = vol_adjusted_stops
        self.buy_forecast_min = buy_forecast_min
        self.buy_prosperity_min = buy_prosperity_min
        # Track recently sold codes to enforce cooldown (ts_code → trade_date)
        self._cooldown: dict[str, str] = {}
        self._cooldown_days = 5  # don't rebuy within 5 trading days of selling

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

        # ── 1. Scan ALL candidates with 5-dimension scoring ──
        # A stock qualifies if momentum passes (primary gate) AND at least
        # one secondary dimension (holder/dividend/forecast/prosperity) passes.
        held_codes = {p.ts_code for p in positions}
        all_qualified: list[tuple] = []  # (code, composite_score, details_str, industry, passed_dims)

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
            prosperity = factors.get("prosperity")

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
            if prosperity is not None and prosperity > self.buy_prosperity_min:
                secondary_pass.append("prosperity")
                secondary_details.append(f"景气{prosperity:.0f}")

            if not secondary_pass:
                continue  # must pass at least one secondary dimension

            # Quality gate: require at least min_secondary_dims secondary dimensions
            if len(secondary_pass) < self.min_secondary_dims:
                continue

            # ── Short-term momentum confirmation ──
            # Don't buy stocks whose long-term momentum is high but recent
            # 5-day return is negative (the "dead cat bounce" filter).
            if self.require_short_momentum:
                sm = snapshot.short_momentum.get(code)
                if sm is not None and sm <= 0:
                    continue  # recent 5-day return ≤ 0, skip

            # ── Valuation filter ──
            # Don't buy stocks at extreme valuation (PE > 80th percentile).
            # High prosperity can tolerate higher PE, but not unlimited.
            pe_pct = snapshot.pe_percentile.get(code)
            if pe_pct is not None and pe_pct > self.max_pe_percentile:
                continue  # too expensive even with good factors

            # Composite score: momentum 40%, best secondary 40%, prosperity bonus 20%
            best_secondary = max(
                holder or 0, dividend or 0, forecast or 0, prosperity or 0
            )
            # Prosperity gets a bonus weight because it reflects fundamental growth quality
            prosperity_score = prosperity or 50  # default neutral if missing
            composite = mom * 0.4 + best_secondary * 0.4 + prosperity_score * 0.2
            detail_str = f"动量{mom:.0f} " + " ".join(secondary_details)
            all_qualified.append((code, composite, detail_str, industry, set(secondary_pass)))

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
            prosperity = factors.get("prosperity")
            stage = factors.get("stage", "")
            industry = snapshot.industries.get(pos.ts_code, "")
            sector_trend = snapshot.industry_trend.get(industry, "flat")

            reasons: list[str] = []
            should_sell = False

            # Primary sell: momentum collapse
            if mom is not None and mom < self.sell_momentum:
                should_sell = True
                reasons.append(f"动量{mom:.0f}<{self.sell_momentum}")

            # Prosperity sell: stage turned to 下降拐点 or 减速期 with low score
            if stage in ("下降拐点",) and prosperity is not None and prosperity < 35:
                should_sell = True
                reasons.append(f"景气{stage} score={prosperity:.0f}")

            # Secondary sell: ALL secondary dimensions failing
            holder_ok = holder is not None and holder > self.buy_holder_min
            div_ok = dividend is not None and dividend > self.buy_dividend_min
            fc_ok = forecast is not None and forecast > self.buy_forecast_min
            pros_ok = prosperity is not None and prosperity > self.buy_prosperity_min
            if not (holder_ok or div_ok or fc_ok or pros_ok):
                if not should_sell:
                    should_sell = True
                    fails = []
                    if holder is not None: fails.append(f"筹码{holder:.0f}")
                    if prosperity is not None: fails.append(f"景气{prosperity:.0f}")
                    reasons.append(f"次维度全fail({','.join(fails)})")

            # Holder distribution hard sell — only if holder was the buy reason
            # (if stock was bought via dividend/forecast/prosperity, holder=0
            # alone shouldn't trigger sell)
            if holder is not None and holder <= 0 and "集中" not in holder_trend:
                # Check if any other dimension still supports holding
                if not (div_ok or fc_ok or pros_ok):
                    should_sell = True
                    reasons.append(f"筹码score={holder:.0f}分散且无其他支撑")

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
                self._cooldown[pos.ts_code] = snapshot.trade_date

        # ── 3. Market gate ──
        effective_max = self._effective_max_positions(snapshot.market_regime)
        if effective_max == 0:
            return signals

        # ── 4. Tiered holding: 60% core (locked) + 40% rotatable ──
        # Core positions are held unless buy reason fully reverses.
        # Rotatable positions can be replaced by significantly stronger candidates.
        target_codes = {c for c, _, _, _, _ in all_qualified[:effective_max]}
        qualified_codes = {c for c, _, _, _, _ in all_qualified}

        position_weight = 1.0 / effective_max

        # Update cooldown
        current_date = snapshot.trade_date
        expired = [k for k, v in list(self._cooldown.items())
                   if _days_between(v, current_date) >= self._cooldown_days]
        for k in expired:
            del self._cooldown[k]

        # Sell held stocks that are no longer qualified at all
        already_selling = {s.ts_code for s in signals if s.action == "SELL"}
        for pos in positions:
            if pos.ts_code not in qualified_codes and pos.ts_code not in already_selling:
                factors_p = snapshot.factor_scores.get(pos.ts_code, {})
                p_holder = factors_p.get("holder")
                p_div = factors_p.get("dividend")
                p_fc = factors_p.get("forecast_leading") or factors_p.get("leading")
                p_pros = factors_p.get("prosperity")
                all_reversed = True
                if p_holder is not None and p_holder > self.buy_holder_min:
                    all_reversed = False
                if p_div is not None and p_div > self.buy_dividend_min:
                    all_reversed = False
                if p_fc is not None and p_fc > self.buy_forecast_min:
                    all_reversed = False
                if p_pros is not None and p_pros > self.buy_prosperity_min:
                    all_reversed = False
                if all_reversed:
                    signals.append(
                        Signal(
                            ts_code=pos.ts_code,
                            name=pos.name,
                            action="SELL",
                            signal_reason="买入理由全部反转，清仓",
                        )
                    )
                    held_codes.discard(pos.ts_code)
                    self._cooldown[pos.ts_code] = current_date

        # ── Rotatable tier: replace weak held stocks with stronger new candidates ──
        # Identify which held stocks are "rotatable" (bottom 40% by composite score)
        held_with_scores = []
        for pos in positions:
            if pos.ts_code in already_selling or pos.ts_code not in held_codes:
                continue
            # Find this stock's composite score from all_qualified
            score = next((s for c, s, _, _, _ in all_qualified if c == pos.ts_code), None)
            if score is not None:
                held_with_scores.append((pos.ts_code, pos.name, score))

        # Sort held stocks by score ascending (weakest first)
        held_with_scores.sort(key=lambda x: x[2])

        # Determine how many are rotatable
        n_rotatable = int(len(held_with_scores) * self.rotatable_ratio)
        rotatable_codes = {code for code, _, _ in held_with_scores[:n_rotatable]}

        # For each rotatable stock, check if there's a significantly better unheld candidate
        for held_code, held_name, held_score in held_with_scores[:n_rotatable]:
            if held_code not in held_codes:
                continue  # already being sold
            # Find the best unheld qualified candidate
            for cand_code, cand_score, cand_details, cand_ind, cand_dims in all_qualified:
                if cand_code in held_codes or cand_code in self._cooldown:
                    continue
                if cand_code not in target_codes:
                    continue
                # Only rotate if new candidate is significantly stronger
                if cand_score - held_score >= self.rotation_threshold:
                    signals.append(
                        Signal(
                            ts_code=held_code,
                            name=held_name,
                            action="SELL",
                            signal_reason=f"轮动换仓: {held_name}({held_score:.0f}) → {snapshot.stock_names.get(cand_code, cand_code)}({cand_score:.0f}) 差值{cand_score-held_score:.0f}>{self.rotation_threshold}",
                        )
                    )
                    held_codes.discard(held_code)
                    self._cooldown[held_code] = current_date
                    break  # only replace with the single best candidate

        # Buy new target stocks for empty slots
        # Cap single position to max_single_position_pct of total equity
        current_hold_count = len(positions) - len(already_selling) - sum(
            1 for s in signals if s.action == "SELL"
        )
        slots = effective_max - current_hold_count
        if slots > 0:
            bought = 0
            # Calculate position weight with single-position cap
            raw_weight = 1.0 / effective_max
            capped_weight = min(raw_weight, self.max_single_position_pct / 100.0)
            for code, score, details, industry, dims in all_qualified:
                if bought >= slots:
                    break
                if code in held_codes:
                    continue
                # Cooldown check
                if code in self._cooldown:
                    continue
                name = snapshot.stock_names.get(code, code)
                sector_note = f" 行业{industry}↑" if industry else ""
                signals.append(
                    Signal(
                        ts_code=code,
                        name=name,
                        action="BUY",
                        target_weight=capped_weight,
                        signal_reason=f"{details}{sector_note}",
                    )
                )
                held_codes.add(code)
                bought += 1

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
