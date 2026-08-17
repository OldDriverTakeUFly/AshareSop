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

from loguru import logger

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
    market_regime: str = "neutral"  # "bull" / "bear" / "neutral" (HMM) or "mixed" (legacy)
    vol_mult: float = 1.0           # position size multiplier from market vol regime
    overseas_risk: float = 0.0      # 国际共振风险分 0-100 (0=未启用/无数据；>=50 触发降级)
    ivix: float = 0.0               # 中国 VIX (iVIX), 0 = 无数据；>25 = 高恐慌
    rv_decay_ratio: float | None = None  # RV5/RV20 比率 (上证), None=无数据
    index_20d_drop: float | None = None  # 上证前20日涨跌幅 (%), None=无数据
    stock_20d_drops: dict = field(default_factory=dict)  # ts_code → 前20日涨跌幅 (%)
    vol_ratio_250: float | None = None  # 全市场近20日均量/250日均量, None=无数据
    industries: dict[str, str] = field(default_factory=dict)  # ts_code → industry
    industry_trend: dict[str, str] = field(default_factory=dict)  # industry → "up"/"down"/"flat"
    # ── Short-term momentum + valuation (added for quality filtering) ──
    short_momentum: dict[str, float] = field(default_factory=dict)  # ts_code → 5-day return %
    mom60: dict[str, float] = field(default_factory=dict)  # ts_code → 60-day return % (妖股检测)
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
    # ── Amihud liquidity factor (0-100, higher = more liquid) ──
    amihud: dict[str, float] = field(default_factory=dict)
    # ── Dragon-tiger institutional net-buy (0-100, higher = more accumulation) ──
    dragon_tiger: dict[str, float] = field(default_factory=dict)
    # ── Repurchase positive signal (0-100, higher = more bullish) ──
    repurchase: dict[str, float] = field(default_factory=dict)
    # ── Intraday amplitude (当日振幅, 0+, higher = more volatile intraday) ──
    intraday_amplitude: dict[str, float] = field(default_factory=dict)
    # ── Intraday gap (开盘缺口%, positive = gap up bullish) ──
    intraday_gap: dict[str, float] = field(default_factory=dict)


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
        #
        # When enable_adaptive_sell=True, the threshold adjusts by HMM regime:
        #   bull → 25 (let winners run longer)
        #   neutral → 30 (standard)
        #   bear → 35 (exit faster)
        sell_momentum: float = 30.0,
        enable_adaptive_sell: bool = False,
        # ── Dynamic position weighting ──
        # When True, higher composite-score stocks get proportionally larger
        # position sizes (softmax-like). When False, equal-weight allocation.
        enable_dynamic_weight: bool = False,
        # ── Amihud liquidity weight ──
        # Weight of Amihud illiquidity score in composite rating.
        # 0 = disabled (default). When > 0, more liquid stocks rank higher.
        # Academic: IC 5.72%, orthogonal to momentum/valuation.
        amihud_weight: float = 0.0,
        # ── Dragon-tiger (龙虎榜) institutional net-buy weight ──
        # 0 = disabled (default). When > 0, stocks with recent positive
        # institutional net-buy on dragon-tiger list rank higher.
            dragon_tiger_weight: float = 0.0,
        # ── Repurchase (回购) positive signal weight ──
        # 0 = disabled (default). When > 0, stocks with recent large
        # repurchase announcements rank higher (management confidence).
        repurchase_weight: float = 0.0,
        # ── Intraday amplitude filter ──
        # When > 0, stocks with daily amplitude > this threshold are excluded
        # from buy candidates. IC = -0.065 (high amplitude → future reversal).
        #
        # A/B result (2026-07-30, 127-day backtest):
        #   amp=0.00 → Sharpe +1.521 (baseline)
        #   amp=0.06 → Sharpe +0.677 (too strict, kills momentum stocks)
        #   amp=0.08 → Sharpe +1.928 (BEST — filters extreme volatility)
        #   amp=0.10 → Sharpe +0.596 (too loose, adds noise)
        max_intraday_amplitude: float = 0.08,
        # ── Quality factor weight (QMJ-style: ROE + OCF/NI + leverage) ──
        # When > 0, high-quality stocks rank higher.
        #
        # A/B result (2026-07-31, 138-day backtest):
        #   qw=0.00 → Sharpe +1.037 (baseline)
        #   qw=0.05 → Sharpe +1.198
        #   qw=0.10 → Sharpe +2.197 (BEST)
        quality_weight: float = 0.10,
        # ── Intraday gap weight ──
        # When > 0, gap-up stocks (positive overnight signal) rank higher.
        # IC = +0.025 (positive: gap up → future returns higher).
        #   gw=0.00 → Sharpe 0.912 (baseline)
        #   gw=0.05 → Sharpe 3.073 (BEST, +22.87pp return, MDD -3.31pp)
        #   gw=0.10 → Sharpe 2.698
        gap_weight: float = 0.05,
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
        # ── Momentum-Holder synergy boost ──
        # When > 0, stocks with BOTH high momentum AND high holder concentration
        # get a composite-score boost. Empirical basis (83433 stock-quarter
        # samples, 2021-2025):
        #   高动量+筹码集中 → +4.77% / 20d (胜率53%)
        #   高动量+筹码分散 → +1.24% / 20d (胜率42%)
        # The spread is +3.5pp — institutions accumulating in trending stocks
        # is the strongest confirmation signal. Without this, momentum and
        # holder are scored independently, missing the interaction effect.
        #   0.0 = disabled (default)
        #   0.05-0.10 = recommended range
        holder_momentum_synergy: float = 0.0,
        # ── iVIX panic pause ──
        # When > 0, suspends NEW buys when iVIX > this threshold AND the market
        # is in an uptrend (bull/neutral). Empirical basis: buying in uptrends
        # during high VIX (>25) yields -0.42% / 5d vs -0.03% in normal VIX —
        # high-VIX uptrends are unstable (假突破). NOTE: high VIX in downtrends
        # is NOT paused — that's when panic reversals happen (+2.29% / 5d).
        #   0  = disabled
        #   25 = pause buys when VIX > 25 and market not in bear
        # Production default: 25.0 (C3 verified 2026-08-06: +5.4pp vs baseline)
        ivix_pause_threshold: float = 25.0,
        # ── Oversold bounce sub-strategy (独立子策略) ──
        # When True, activates a parallel "panic bottom fishing" module that
        # buys deeply oversold stocks when the MARKET is also in panic.
        #
        # Empirical basis (5-year, 2021-2026):
        #   Market layer: 上证前20d跌幅<-5% + RV5/RV20>0.8 → 20d +4-5%
        #   Stock layer:  在触发日选跌幅最深 → Q5-Q1 = +4.41%
        #   Sell signal:  RV5/RV20 < 0.8 (波动衰减 = 反弹走完)
        #
        # This runs INDEPENDENTLY of the trend-following logic. It bypasses
        # the normal buy_momentum/holder/PE filters because oversold stocks
        # by definition have low momentum. Instead it uses its own entry
        # (deepest drop + alive), holding (max 20 days), and exit (vol decay).
        # Allocated a fixed number of "bounce slots" separate from max_positions.
        # Production default: True (C3 verified 2026-08-06: +7.3pp vs baseline,
        # 2022 bear market +3.1pp improvement, 133 bounce trades over 5 years)
        enable_oversold_bounce: bool = True,
        # Sweep 2026-08-10: slots 3/2/1 → Sharpe 0.720/0.753/0.886 (1 is best —
        # concentrated best-opportunity bounce beats diversification).
        oversold_bounce_slots: int = 1,        # 独立于 max_positions 的额外仓位
        # ── Cyclical stock holding rules (周期股持仓辅助) ──
        # Cyclical stocks have a bimodal return structure: fast death or
        # super-cycle. The 31-60 day zone with small P&L is the worst area
        # (avg -1.25%, win rate 33% in 5-yr backtest). Rules:
        #   1. Exit cyclical positions held >30d with P&L < 3% (cycle missed)
        #   2. Widen stop for cyclical positions with P&L > 15% (super-cycle
        #      protection — these average +41.5% when held 60d+)
        #   0 = disabled
        enable_cyclical_rules: bool = False,
        cyclical_exit_days: int = 30,          # 持仓超过此天数
        cyclical_exit_min_pnl: float = 0.03,   # 且浮盈低于此值 → 清仓
        cyclical_super_cycle_pnl: float = 0.15, # 浮盈超过此值 → 宽止损保护
        oversold_market_drop: float = -5.0,     # 上证前20d跌幅阈值
        oversold_rv_ratio_min: float = 0.8,     # RV5/RV20 最小值（未衰减）
        oversold_rv_ratio_sell: float = 0.8,    # RV5/RV20 卖出阈值（衰减即卖）
        oversold_max_hold_days: int = 20,       # 最大持有天数
        oversold_stop_loss: float = 0.08,       # 硬止损 8%
        # ── Bounce candidate depth threshold (反弹选股跌幅门槛) ──
        # Full-market study (66 trigger days × 500 stocks, 24k samples):
        #   drop -3~-10%  → +3.77% / 20d, win 63%
        #   drop -10~-20% → +4.58% / 20d, win 69%
        #   drop < -20%   → +10.54% / 20d, win 78%  ← golden zone
        # Deeper drops bounce harder (monotonic). Old hardcoded -3% admits
        # shallow-dip noise; a stricter floor concentrates on real panic.
        oversold_candidate_min_drop: float = -3.0,
        # ── Bounce fallen pool (反弹全市场暴跌池) ──
        # When > 0, on bounce trigger days the executor expands the candidate
        # pool with the market-wide N deepest-fallen stocks (ex- ST). The
        # main pool (top turnover) rarely contains true panic drops. Set to
        # 0 to keep bounce selection within the main universe.
        oversold_fallen_pool_size: int = 0,
        # ── Demon stock filter (妖股过滤/反因子应用) ──
        # Anti-factor study (31k samples, 2021-2026): mom60 IC=-0.074 — the
        # top mom60 quintile averages -0.56% over the next 20d (mean reversion
        # of extreme winners). When > 0, reject buying stocks whose 60d return
        # exceeds this cap (e.g. 1.5 = reject stocks up >150% in 3 months).
        #   0.0 = disabled
        #   1.5 = recommended (3个月涨幅超150%不追)
        max_mom60: float = 0.0,
        # ── Trailing stop (跟踪止损) ──
        # When > 0, replaces fixed take-profit for positions in profit.
        # Once P&L >= trailing_activate (e.g. 10%), the stop line moves up
        # to: highest_price × (1 - trailing_drawback). Position is sold when
        # price drops drawback% from its peak. This "lets winners run" and
        # captures larger trends, addressing the "卖飞 +23%" problem.
        #   0.0 = disabled (use fixed take_profit from _RISK_RULES)
        #   0.08 = recommended (8% drawback from peak)
        trailing_drawback: float = 0.0,         # 回撤阈值 (0.08 = 从最高点回撤8%卖出)
        trailing_activate: float = 0.10,        # 激活阈值 (盈利10%后开始跟踪)
        # ── Minimum hold period ──
        # Prevents ultra-short trades (<3 days) that contribute <6% of profit
        # but 24% of trades. Positions are not sold (except hard stop) within
        # this many trading days after purchase.
        #   0 = disabled (sell anytime)
        #   5 = recommended (hold at least 5 trading days)
        min_hold_days: int = 0,
        # ── Quick stop for new positions ──
        # If a position drops > quick_stop_pct within quick_stop_days of
        # purchase, exit immediately (don't wait for the full hard_stop).
        # Addresses: 109 stop-losses averaged 113 days hold (too slow).
        #   0.0 = disabled
        #   0.05 = recommended (5% drop within 5 days → exit)
        quick_stop_pct: float = 0.0,
        quick_stop_days: int = 5,
        # ── Volume ratio defense (量能比防御) ──
        # When > 0, reduces max positions when market volume ratio
        # (20d avg / 250d avg) exceeds this threshold. High volume ratio
        # = 放量高潮 = momentum likely to fail (IC=-0.214).
        # Verified 2026-08-08: W1 vs W0 = +89.3% vs +85.4%, Sharpe +0.868 vs +0.804.
        #   0.0 = disabled
        #   1.2 = production default (verified 2026-08-08)
        vol_ratio_defense: float = 1.2,
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
        # ── Negative-factor veto (2026-08-06) ──
        # One-strike filter blocking known loss-making patterns before buy,
        # + force-sell for zombie holdings. Default ON.
        enable_negative_factors: bool = True,
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
        self.enable_adaptive_sell = enable_adaptive_sell
        self.enable_dynamic_weight = enable_dynamic_weight
        self.amihud_weight = amihud_weight
        self.dragon_tiger_weight = dragon_tiger_weight
        self.repurchase_weight = repurchase_weight
        self.max_intraday_amplitude = max_intraday_amplitude
        self.quality_weight = quality_weight
        self.gap_weight = gap_weight
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
        self.holder_momentum_synergy = holder_momentum_synergy
        self.ivix_pause_threshold = ivix_pause_threshold
        self.enable_oversold_bounce = enable_oversold_bounce
        self.oversold_bounce_slots = oversold_bounce_slots
        self.enable_cyclical_rules = enable_cyclical_rules
        self.cyclical_exit_days = cyclical_exit_days
        self.cyclical_exit_min_pnl = cyclical_exit_min_pnl
        self.cyclical_super_cycle_pnl = cyclical_super_cycle_pnl
        self.oversold_market_drop = oversold_market_drop
        self.oversold_rv_ratio_min = oversold_rv_ratio_min
        self.oversold_rv_ratio_sell = oversold_rv_ratio_sell
        self.oversold_max_hold_days = oversold_max_hold_days
        self.oversold_stop_loss = oversold_stop_loss
        self.oversold_candidate_min_drop = oversold_candidate_min_drop
        self.oversold_fallen_pool_size = oversold_fallen_pool_size
        self.max_mom60 = max_mom60
        self.trailing_drawback = trailing_drawback
        self.trailing_activate = trailing_activate
        self.min_hold_days = min_hold_days
        self.quick_stop_pct = quick_stop_pct
        self.quick_stop_days = quick_stop_days
        self.vol_ratio_defense = vol_ratio_defense
        # Track bounce positions: {ts_code: buy_date} for hold-day tracking
        self._bounce_positions: dict[str, str] = {}
        self.pe_exemption_for_volume = pe_exemption_for_volume
        self.low_vol_stop_exemption = low_vol_stop_exemption
        self.volume_weight = volume_weight
        self.enable_volume_risk = enable_volume_risk
        self.enable_event_filter = enable_event_filter
        # Negative-factor veto (2026-08-06 post-mortem): one-strike filter
        # that blocks known loss-making patterns before buy + force-sells
        # zombie holdings. See docs/方法论/负因子选股方法论_20260806.md.
        self.enable_negative_factors = enable_negative_factors
        self.event_penalty_weight = event_penalty_weight
        self.tech_weight = tech_weight
        self.risk_stop_multiplier = risk_stop_multiplier
        self.buy_forecast_min = buy_forecast_min
        self.buy_prosperity_min = buy_prosperity_min
        # Track recently sold codes to enforce cooldown (ts_code → trade_date)
        self._cooldown: dict[str, str] = {}
        self._cooldown_days = 5  # don't rebuy within 5 trading days of selling

    def _oversold_bounce_evaluate(
        self, positions, snapshot, signals, total_equity,
        held_codes: set | None = None,
    ) -> list:
        """Oversold bounce sub-strategy: buy deepest-oversold stocks in panic.

        Activated when market 20-day drop < threshold AND RV5/RV20 > threshold
        (volatility hasn't decayed = panic still alive = bounce opportunity).

        Empirical basis:
          Market: 上证跌>5% + RV未衰减 → 20d +4-5%
          Stock:  跌幅最深的Q5 vs 最抗跌Q1 = +4.41% spread
          Exit:   RV5/RV20 < 0.8 (衰减即卖)

        This bypasses normal buy filters (momentum/holder/PE) because oversold
        stocks by definition have low momentum. Uses dedicated slots separate
        from max_positions.
        """
        if held_codes is None:
            held_codes = {p.ts_code for p in positions}

        # Check market-level trigger from snapshot
        market_drop = getattr(snapshot, "index_20d_drop", None)
        rv_ratio = getattr(snapshot, "rv_decay_ratio", None)

        if market_drop is None or rv_ratio is None:
            return signals  # no market data for bounce trigger

        # Trigger: market oversold + vol not decayed
        if market_drop > self.oversold_market_drop:
            return signals  # not oversold enough
        if rv_ratio < self.oversold_rv_ratio_min:
            return signals  # vol already decayed, bounce opportunity passed

        # Count active bounce positions
        active_bounce = sum(
            1 for p in positions
            if p.ts_code in self._bounce_positions
        )
        available_slots = self.oversold_bounce_slots - active_bounce
        if available_slots <= 0:
            return signals  # bounce slots full

        # Rank candidates by 20-day drop (deepest first)
        # Use short_momentum as proxy if available, otherwise skip
        candidates = []
        for code, sm in snapshot.short_momentum.items():
            if code in held_codes:
                continue
            if code not in snapshot.prices or snapshot.prices[code] <= 0:
                continue
            # short_momentum is 5-day return; we want 20-day drop
            # Use the precomputed value from executor if available
            drop_20d = getattr(snapshot, "stock_20d_drops", {}).get(code)
            if drop_20d is None:
                continue
            if drop_20d > self.oversold_candidate_min_drop:  # depth floor
                continue
            candidates.append((code, drop_20d))

        # Sort by deepest drop first
        candidates.sort(key=lambda x: x[1])  # most negative first

        bounce_weight = 1.0 / (self.oversold_bounce_slots + self.max_positions)  # ~12%

        for code, drop in candidates[:available_slots]:
            name = snapshot.stock_names.get(code, code)
            signals.append(Signal(
                ts_code=code,
                name=name,
                action="BUY",
                target_weight=bounce_weight,
                signal_reason=(
                    f"超跌反弹：上证{market_drop:.1f}% RV比率{rv_ratio:.2f} "
                    f"个股20d跌幅{drop:.1f}%"
                ),
            ))
            self._bounce_positions[code] = snapshot.trade_date
            held_codes.add(code)

        return signals

    def _effective_max_positions(self, market_regime: str,
                                 vol_mult: float = 1.0) -> int:
        """Reduce position cap in bear/neutral markets + vol adjustment.

        Supports both old regime names (mixed) and new (neutral):
        - bear/panic → 0 (no new buys)
        - mixed/neutral → half positions × vol_mult
        - bull → full positions × vol_mult

        vol_mult: position size multiplier from market volatility regime
        (1.1 low_vol / 1.0 normal / 0.8 high_vol / 0.5 extreme_vol).
        """
        if market_regime in ("bear", "panic"):
            return 0  # no new buys in bear market
        if market_regime in ("mixed", "neutral"):
            base = max(1, self.max_positions // 2)
        else:
            base = self.max_positions
        return max(1, int(base * vol_mult))

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

            # ── Intraday amplitude filter ──
            # IC = -0.065: high daily range → future reversal.
            # Skip stocks with extreme intraday volatility on the buy day.
            if self.max_intraday_amplitude > 0:
                amp = snapshot.intraday_amplitude.get(code)
                if amp is not None and amp > self.max_intraday_amplitude:
                    continue  # too volatile, skip buy

            # ── Negative-factor veto (2026-08-06 post-mortem) ──
            # One-strike filter: blocks known loss-making patterns before buy.
            # Rules ①③⑤ (topchase / concentration / cyclical-PE-trap).
            # Rules ②④ (zombie / absolute-stop) fire in the holding scan below.
            if self.enable_negative_factors:
                from davis_analyzer.paper_trading.negative_factors import check_buy as _nf_check_buy
                veto = _nf_check_buy(
                    code, factors, snapshot,
                    portfolio_concentration=getattr(self, "_current_concentration", None),
                )
                if veto.vetoed:
                    logger.debug(f"负因子否决 {code}: {veto.reason}")
                    continue

            # ── iVIX panic pause ──
            # When VIX is high AND market is not in bear, buying in uptrends is
            # likely to fail (empirical: -0.42% / 5d vs -0.03% normally). The
            # high volatility means breakouts are unreliable. But we do NOT
            # pause in bear markets — high-VIX downtrends are where panic
            # reversals happen (+2.29% / 5d), exactly when value buyers step in.
            if self.ivix_pause_threshold > 0:
                ivix = getattr(snapshot, "ivix", 0.0)
                if ivix > self.ivix_pause_threshold:
                    regime = snapshot.market_regime
                    if regime not in ("bear", "panic"):
                        continue  # high VIX in uptrend — pause buy

            # ── Demon stock filter (妖股过滤) ──
            # Extreme 60d winners mean-revert (IC=-0.074, top quintile -0.56%/20d).
            # Reject buying stocks that already surged past max_mom60 — chasing
            # 3-month doublers is buying the top of the distribution.
            if self.max_mom60 > 0:
                m60 = snapshot.mom60.get(code)
                if m60 is not None and m60 > self.max_mom60 * 100:
                    continue  # too extended — mean reversion risk

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
            aw = self.amihud_weight
            dw = self.dragon_tiger_weight
            rw = self.repurchase_weight
            qw = self.quality_weight
            gw = self.gap_weight
            hw = self.holder_momentum_synergy
            extras_weight = vw + tw + aw + dw + rw + qw + gw + hw
            legacy_weight = 1.0 - extras_weight

            vol_score = snapshot.volume_signal.get(code, {}).get("score", 50.0)
            tech_s = snapshot.tech_score.get(code, 50.0)
            amihud_s = snapshot.amihud.get(code, 50.0)
            dt_s = snapshot.dragon_tiger.get(code, 50.0)
            rep_s = snapshot.repurchase.get(code, 50.0)
            quality_raw = factors.get("quality", 50.0)
            quality_s = quality_raw.quality_score if hasattr(quality_raw, "quality_score") else float(quality_raw)
            # Gap score: map gap% to 0-100 (0% → 50 neutral, +5% → 100, -5% → 0)
            gap_pct = snapshot.intraday_gap.get(code, 0.0)
            gap_s = max(0.0, min(100.0, 50.0 + gap_pct * 10.0))

            if extras_weight > 0:
                # Holder-Momentum synergy: only fires when BOTH are high.
                # synergy = (holder_norm × mom_norm) scaled to 0-100, but only
                # contributes when both exceed 0.7 (otherwise capped low).
                # This captures the empirical finding that 高动量+筹码集中 = +4.77%/20d.
                if hw > 0 and holder is not None:
                    h_norm = max(0.0, min(1.0, holder / 100.0))
                    m_norm = max(0.0, min(1.0, mom / 100.0))
                    # Nonlinear: only rewards when BOTH are high
                    synergy_s = h_norm * m_norm * 100.0
                else:
                    synergy_s = 0.0

                composite = (
                    mom * 0.40 * legacy_weight
                    + best_secondary * 0.40 * legacy_weight
                    + prosperity_score * 0.20 * legacy_weight
                    + vol_score * vw
                    + tech_s * tw
                    + amihud_s * aw
                    + dt_s * dw
                    + rep_s * rw
                    + quality_s * qw
                    + gap_s * gw
                    + synergy_s * hw
                )
                detail_str = (
                    f"动量{mom:.0f} " + " ".join(secondary_details)
                    + (f" 量价{vol_score:.0f}" if vw > 0 else "")
                    + (f" 技术{tech_s:.0f}" if tw > 0 else "")
                    + (f" 流动{amihud_s:.0f}" if aw > 0 else "")
                    + (f" 龙虎{dt_s:.0f}" if dw > 0 else "")
                    + (f" 回购{rep_s:.0f}" if rw > 0 else "")
                    + (f" 质量{quality_s:.0f}" if qw > 0 else "")
                    + (f" 共振{synergy_s:.0f}" if hw > 0 else "")
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

        # Pre-compute portfolio industry concentration for negative-factor
        # rule ③ (used during buy-candidate check above).
        if self.enable_negative_factors:
            from davis_analyzer.paper_trading.negative_factors import compute_concentration
            self._current_concentration = compute_concentration(positions, snapshot.prices)

        # ── 2. Check existing positions for sell signals ──
        for pos in positions:
            # Bounce positions are managed solely by the oversold bounce
            # sell logic (vol decay / time stop / hard stop), NOT by the
            # trend strategy's momentum/sector/holder sell rules. Without
            # this exemption, bounce buys get sold on day 1 because they
            # have low momentum and weak sector trend by definition.
            is_bounce_pos = pos.ts_code in self._bounce_positions

            factors = snapshot.factor_scores.get(pos.ts_code, {})
            mom = factors.get("momentum")

            # ── Negative-factor holding check (rules ②④) ──
            # Force-sell zombie holdings + absolute-stop deeply underwater
            # positions that the executor's stop-loss can't reach.
            if self.enable_negative_factors:
                from davis_analyzer.paper_trading.negative_factors import check_holding as _nf_check_holding
                cur_px = snapshot.prices.get(pos.ts_code, pos.avg_cost)
                veto = _nf_check_holding(pos, cur_px, snapshot.trade_date)
                if veto.vetoed and veto.action == "FORCE_SELL":
                    signals.append(Signal(
                        ts_code=pos.ts_code, name=pos.name,
                        action="SELL", shares=pos.shares, price=cur_px,
                        score=0,
                        signal_reason=f"负因子[{veto.rule}] {veto.reason}",
                    ))
                    logger.info(
                        f"[{snapshot.trade_date}] 负因子强制卖出 {pos.ts_code}: {veto.reason}"
                    )
                    continue  # skip normal sell checks — already force-selling
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

            # ── Bounce position exemption ──
            # Skip all trend-based sell rules (momentum/sector/holder) for
            # oversold bounce positions. They are managed solely by the
            # bounce sell logic (vol decay / time stop / hard stop) which
            # runs in the section above (2b). Without this exemption, bounce
            # buys get sold on day 1 because they have low momentum by design.
            if is_bounce_pos:
                continue  # skip to next position — bounce sells handled in 2b

            # Primary sell: momentum collapse
            # Adaptive threshold: adjust sell sensitivity by market regime
            if self.enable_adaptive_sell:
                regime = snapshot.market_regime
                if regime == "bull":
                    effective_sell = max(20, self.sell_momentum - 5)  # 25: let winners run
                elif regime in ("bear", "panic"):
                    effective_sell = self.sell_momentum + 5  # 35: exit faster in bear
                else:  # neutral / mixed
                    effective_sell = self.sell_momentum  # 30: standard
            else:
                effective_sell = self.sell_momentum

            if mom is not None and mom < effective_sell:
                should_sell = True
                reasons.append(f"动量{mom:.0f}<{effective_sell}")

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
                # Remove from bounce tracking if it was a bounce position
                self._bounce_positions.pop(pos.ts_code, None)

        # ── 2b. Oversold bounce: sell on volatility decay ──
        # Bounce positions exit when RV5/RV20 drops below threshold (波动衰减=
        # 反弹走完), or after max hold days, or hard stop-loss.
        if self.enable_oversold_bounce and self._bounce_positions:
            for pos in positions:
                if pos.ts_code not in self._bounce_positions:
                    continue
                buy_date = self._bounce_positions[pos.ts_code]
                px = snapshot.prices.get(pos.ts_code, 0)
                pnl_pct = (px / pos.avg_cost - 1) if pos.avg_cost > 0 and px > 0 else 0

                # Hold days
                hold_days = _days_between(buy_date, snapshot.trade_date)

                # RV ratio (from snapshot — set by executor)
                rv_ratio = getattr(snapshot, "rv_decay_ratio", None)

                bounce_reasons = []
                if rv_ratio is not None and rv_ratio < self.oversold_rv_ratio_sell:
                    bounce_reasons.append(f"波动衰减(RV5/RV20={rv_ratio:.2f}<{self.oversold_rv_ratio_sell})")
                if hold_days >= self.oversold_max_hold_days:
                    bounce_reasons.append(f"持有{hold_days}天到期")
                if pnl_pct <= -self.oversold_stop_loss:
                    bounce_reasons.append(f"硬止损{pnl_pct*100:.1f}%")

                if bounce_reasons:
                    signals.append(Signal(
                        ts_code=pos.ts_code,
                        name=pos.name,
                        action="SELL",
                        signal_reason="超跌反弹卖出：" + "；".join(bounce_reasons),
                    ))
                    self._bounce_positions.pop(pos.ts_code, None)

        # ── 2c. Cyclical stock holding rules (周期股持仓辅助) ──
        # Rule 1: cyclical + held >30d + P&L < 3% → exit (cycle missed).
        # Empirical: this zone averages -1.25% with 33% win rate — holding
        # a cyclical that hasn't moved in a month is dead capital.
        # Rule 2 is implemented in executor's risk layer (super-cycle stop
        # widening) since it needs to interact with hard_stop thresholds.
        if self.enable_cyclical_rules:
            try:
                from davis_analyzer.cyclical import is_cyclical_by_code
                from datetime import datetime as _dtc
                for pos in positions:
                    if pos.ts_code in self._bounce_positions:
                        continue  # bounce positions managed separately
                    if not is_cyclical_by_code(pos.ts_code):
                        continue
                    px = snapshot.prices.get(pos.ts_code)
                    if px is None or px <= 0 or not pos.avg_cost or pos.avg_cost <= 0:
                        continue
                    pnl = px / pos.avg_cost - 1
                    if not pos.entry_date:
                        continue
                    hold_days = (_dtc.strptime(snapshot.trade_date, "%Y%m%d")
                                 - _dtc.strptime(pos.entry_date[:8], "%Y%m%d")).days
                    if hold_days > self.cyclical_exit_days and pnl < self.cyclical_exit_min_pnl:
                        signals.append(Signal(
                            ts_code=pos.ts_code,
                            name=pos.name,
                            action="SELL",
                            signal_reason=f"周期股清理：持仓{hold_days}天浮盈{pnl*100:.1f}%<{self.cyclical_exit_min_pnl*100:.0f}%（周期已过）",
                        ))
            except Exception:
                pass  # cyclical rules are best-effort

        # ── 3. Market gate ──
        effective_max = self._effective_max_positions(
            snapshot.market_regime, getattr(snapshot, "vol_mult", 1.0)
        )
        # Volume ratio defense: halve positions when market volume >> 250d avg
        if self.vol_ratio_defense > 0:
            vr = getattr(snapshot, "vol_ratio_250", None)
            if vr is not None and vr > self.vol_ratio_defense and effective_max > 1:
                effective_max = max(1, effective_max // 2)
        if effective_max == 0:
            # Trend strategy is closed (bear market). But oversold bounce
            # sub-strategy can still operate — panic bottoms happen in bear.
            if self.enable_oversold_bounce:
                return self._oversold_bounce_evaluate(
                    positions, snapshot, signals, total_equity
                )
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
            # Bounce positions: skip "no longer qualified" sell (they bypass
            # the normal qualification pipeline by design)
            if pos.ts_code in self._bounce_positions:
                continue
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
            # Position sizing: equal-weight (default) or score-weighted
            if self.enable_dynamic_weight:
                # Score-weighted allocation: higher composite score → larger weight
                # Uses softmax-like normalization across qualified candidates
                scores_list = [s for _, s, _, _, _ in all_qualified[:slots]]
                if scores_list and max(scores_list) > min(scores_list):
                    # Normalize scores to 0.5-1.5 range, then normalize to sum=1
                    s_min, s_max = min(scores_list), max(scores_list)
                    raw_weights = [0.5 + 1.0 * (s - s_min) / (s_max - s_min)
                                   for s in scores_list]
                    total_w = sum(raw_weights)
                    weights_list = [w / total_w for w in raw_weights]
                else:
                    # All same score → equal weight
                    weights_list = [1.0 / len(scores_list)] * len(scores_list) if scores_list else []
            else:
                # Equal weight
                raw_weight = 1.0 / effective_max
                capped_weight = min(raw_weight, self.max_single_position_pct / 100.0)

            for idx, (code, score, details, industry, dims) in enumerate(all_qualified):
                if bought >= slots:
                    break
                if code in held_codes:
                    continue
                # Cooldown check
                if code in self._cooldown:
                    continue

                # Calculate weight
                if self.enable_dynamic_weight and idx < len(weights_list):
                    weight = min(weights_list[idx], self.max_single_position_pct / 100.0)
                else:
                    # Fallback: equal weight
                    raw_w = 1.0 / max(effective_max, 1)
                    weight = min(raw_w, self.max_single_position_pct / 100.0)

                name = snapshot.stock_names.get(code, code)
                sector_note = f" 行业{industry}↑" if industry else ""
                signals.append(
                    Signal(
                        ts_code=code,
                        name=name,
                        action="BUY",
                        target_weight=weight,
                        signal_reason=f"{details}{sector_note}",
                    )
                )
                held_codes.add(code)
                bought += 1

        # ── 5. Oversold bounce sub-strategy (parallel to trend) ──
        # Activates when market is in panic (上证跌幅>5% + RV未衰减).
        # Buys the deepest-oversold stocks regardless of momentum/filters.
        if self.enable_oversold_bounce:
            self._oversold_bounce_evaluate(
                positions, snapshot, signals, total_equity,
                held_codes=held_codes,
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
