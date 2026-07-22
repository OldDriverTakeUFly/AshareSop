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
    # ── Volume-price signal (added for composite rating) ──
    volume_signal: dict[str, dict] = field(default_factory=dict)
    # ts_code → {"score": float, "signal_type": str, "vol_ratio": float, ...}
    # signal_type ∈ {"platform_breakout", "low_vol", "high_vol", "neutral"}
    # ── Event signal (减持/解禁硬门槛) ──
    event_signal: dict[str, dict] = field(default_factory=dict)
    # ts_code → {"blocked": bool, "reason": str}
    # ── Technical factor (composite tech_score, 0-100) ──
    tech_score: dict[str, float] = field(default_factory=dict)
    # ts_code → tech_score (0-100, higher = stronger technical state)


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
        max_positions: int = 5,
        buy_momentum: float = 70.0,
        # sell_momentum: Fine-param sweep (2026-07-23) showed 30 beats 40/45.
        # Lower threshold = exit sooner when momentum fades (better in bear markets).
        #   sell=30 → Sharpe +0.252 (BEST)
        #   sell=40 → Sharpe +0.085
        #   sell=45 → Sharpe -0.249
        sell_momentum: float = 30.0,
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
        # ── PE exemption for volume-price signals ──
        # When True, stocks with platform_breakout or low_vol volume signal
        # bypass the max_pe_percentile cap. Rationale: technically-driven buys
        # where expensive is justified by momentum confirmation.
        #
        # Stage-2 A/B result (2026-07-21, 127-day backtest):
        #   S0 (no exemption)   → Sharpe -0.133
        #   S1 (PE exemption)   → Sharpe +0.085 (BEST, first positive Sharpe)
        #   S2 (low_vol stop)   → Sharpe -0.155 (worse)
        #   S3 (S1 + S2)        → Sharpe -0.155 (S2 cancels S1's gain)
        pe_exemption_for_volume: bool = True,
        # ── Low-volume (吸筹) stop-loss exemption ──
        # When > 0, positions flagged as low_vol (主力吸筹 signal) get wider
        # stop-loss: hard_stop *= (1 + low_vol_stop_exemption).
        #   0.0 = no exemption (default)
        #   0.5 = stop widened by 50% (e.g., 8% → 12%)
        # Rationale: low-position high-volume often involves shake-outs (洗盘)
        # before the real move; tight stop would exit during normal dips.
        #
        # NOTE: Stage-2 A/B showed this REDUCES Sharpe (S2/S3 < S1), because
        # wider stops on low_vol positions led to larger losses. Default OFF.
        low_vol_stop_exemption: float = 0.0,
        # ── Volume-price composite weight ──
        # When > 0, the composite rating blends in a volume-price score
        # (platform breakout / low-position volume = positive; high-position
        # volume = negative). Set to 0 to disable (legacy behaviour).
        #
        # Sweep result (2026-07-20, 127-day backtest, top-200 universe):
        #   vw=0.00 → +2.75%   vw=0.05 → +3.01% (best)
        #   vw=0.10 → -0.91%   vw=0.15 → -1.52%   vw=0.20 → -1.45%
        # The buy-side volume signal is noisy — most value comes from the
        # high-vol risk sell (enable_volume_risk). Keep buy weight low.
        volume_weight: float = 0.05,           # weight of volume-price score in composite
        # ── Volume-price risk sell (高位放量) ──
        # When True, the risk layer treats ``signal_type == "high_vol"`` as a
        # distribution event and emits a SELL for profitable positions. Set to
        # False to disable this risk-sell path entirely (for A/B testing).
        enable_volume_risk: bool = True,
        # ── Event hard filter (减持/解禁) ──
        # When True, stocks with recent >1% reductions (last 60d) or upcoming
        # >=5% unlocks (next 30d) are excluded from buy candidates.
        # Empirical basis: docs/方法论/A股事件因子实证研究方法论.md
        #
        # NOTE: 4-way backtest on 2026-07-20 showed enabling this REDUCES return
        # by -3.92pp (V3 vs V2), because the filter is too aggressive in our
        # strong-momentum universe (减持后继续上涨的强势股被误杀).
        # Default OFF — prefer event_penalty_weight (soft-gate) below.
        enable_event_filter: bool = False,
        # ── Event soft penalty (减持/解禁 扣分) ──
        # When > 0, stocks with event signals receive a composite-score penalty
        # proportional to event severity (0-30 points), weighted by this factor.
        # Unlike enable_event_filter (hard-gate), this preserves ranking — strong
        # stocks still qualify, just rank lower. Recommended 0.5-1.0.
        #   penalty_weight=1.0 → 30-point penalty reduces composite by 30
        #   penalty_weight=0.5 → 30-point penalty reduces composite by 15
        event_penalty_weight: float = 0.0,
        # ── Technical factor weight ──
        # Weight of tech_score (0-100) in the composite rating.
        # Empirical basis: docs/方法论/A股技术因子实证研究方法论.md (Q5-Q1=+1.14%, 20d)
        # When > 0, the composite blends in tech_score. Set to 0 to disable.
        #
        # NOTE: 4-way backtest showed +1.29pp improvement when combined with
        # event filter (V4 vs V3), but net negative vs volume-only (V4 < V2).
        # Default OFF — re-enable if event filter is also kept.
        tech_weight: float = 0.0,
        # ── Risk threshold multiplier (止损/止盈收紧/放宽) ──
        # Multiplies the base stop-loss/take-profit from _RISK_RULES.
        #   1.0 = baseline
        #   0.8 = 止损收紧 20%（降低回撤但可能多砍仓）
        #   1.2 = 止损放宽 20%（少砍仓但回撤可能加大）
        #
        # Sharpe sweep result (2026-07-21, 127-day backtest):
        #   pos=5 + stop=0.70 → Sharpe -0.133 (BEST)
        #   pos=5 + stop=1.00 → Sharpe -0.367
        #   pos=10 (any stop) → Sharpe -0.482 ~ -0.649 (worst)
        # Tighter stop + concentrated positions = better risk-adjusted return.
        risk_stop_multiplier: float = 0.70,
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
        self.pe_exemption_for_volume = pe_exemption_for_volume
        self.low_vol_stop_exemption = low_vol_stop_exemption
        self.volume_weight = volume_weight
        self.enable_volume_risk = enable_volume_risk
        self.enable_event_filter = enable_event_filter
        self.event_penalty_weight = event_penalty_weight
        self.tech_weight = tech_weight
        self.risk_stop_multiplier = risk_stop_multiplier
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
            #
            # EXEMPTION: stocks with strong volume-price confirmation
            # (platform_breakout or low_vol signal) bypass the PE cap — these
            # are technically-driven buys where expensive is justified by
            # momentum confirmation.
            pe_pct = snapshot.pe_percentile.get(code)
            if pe_pct is not None and pe_pct > self.max_pe_percentile:
                if self.pe_exemption_for_volume:
                    vol_sig = snapshot.volume_signal.get(code, {})
                    sig_type = vol_sig.get("signal_type", "neutral")
                    if sig_type in ("platform_breakout", "low_vol"):
                        pass  # exempt — strong technical confirmation
                    else:
                        continue  # too expensive AND no volume confirmation
                else:
                    continue  # too expensive even with good factors

            # ── Event hard filter (减持/解禁) ──
            # Empirical: 减持后 60 天 CAR -1.76%, 解禁前 20 天 CAR -2.79%
            # Skip stocks with recent ≥1% reductions or upcoming ≥5% unlocks.
            if self.enable_event_filter:
                ev = snapshot.event_signal.get(code)
                if ev is not None and ev.get("blocked"):
                    continue  # event-blocked, skip buy

            # Composite score: momentum + best secondary + prosperity + volume-price + tech.
            # Default weights:
            #   动量 35% + 次维度 35% + 景气 17.5% + 量价 5% + 技术 7.5%
            # The legacy 40/40/20 weights are rescaled into (1 - vw - tw) of total.
            best_secondary = max(
                holder or 0, dividend or 0, forecast or 0, prosperity or 0
            )
            prosperity_score = prosperity or 50  # default neutral if missing

            vw = self.volume_weight
            tw = self.tech_weight
            extras_weight = vw + tw
            legacy_weight = 1.0 - extras_weight

            vol_score = snapshot.volume_signal.get(code, {}).get("score", 50.0)
            tech_s = snapshot.tech_score.get(code, 50.0)

            if extras_weight > 0:
                composite = (
                    mom * 0.40 * legacy_weight
                    + best_secondary * 0.40 * legacy_weight
                    + prosperity_score * 0.20 * legacy_weight
                    + vol_score * vw
                    + tech_s * tw
                )
                detail_str = (
                    f"动量{mom:.0f} " + " ".join(secondary_details)
                    + (f" 量价{vol_score:.0f}" if vw > 0 else "")
                    + (f" 技术{tech_s:.0f}" if tw > 0 else "")
                )
            else:
                composite = mom * 0.4 + best_secondary * 0.4 + prosperity_score * 0.2
                detail_str = f"动量{mom:.0f} " + " ".join(secondary_details)

            # ── Event soft penalty (减持/解禁 扣分) ──
            # Unlike hard filter, this doesn't skip — just lowers composite.
            if self.event_penalty_weight > 0:
                ev = snapshot.event_signal.get(code)
                if ev is not None:
                    penalty = ev.get("penalty", 0.0)
                    if penalty > 0:
                        # penalty is 0-30, weighted by event_penalty_weight
                        deduction = penalty * self.event_penalty_weight
                        composite -= deduction
                        detail_str += f" 事件-{penalty:.0f}"

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
