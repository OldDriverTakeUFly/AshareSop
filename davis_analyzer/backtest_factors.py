"""Cross-sectional factor scoring for the backtest engine.

This module produces a point-in-time factor score for every stock in the
universe as of a given historical date (``as_of``).  The score drives
the rebalance logic in :mod:`davis_analyzer.backtest`.

Four factors are blended (all point-in-time correct):

    * **Momentum** — :func:`davis_analyzer.momentum.analyze_momentum`
      accepts a ``today`` parameter; passing ``today=as_of`` yields a score
      using only data up to ``as_of``.

    * **Valuation** — :func:`davis_analyzer.valuation.fetch_valuation_history`
      accepts an ``as_of`` parameter that anchors the PE/PB look-back window
      to the rebalance date instead of ``date.today()``.

    * **Prosperity (景气度)** — :func:`davis_analyzer.prosperity.calculate_prosperity_score`
      consumes quarterly ``FinancialData``.  Point-in-time correctness is
      guaranteed upstream: :func:`fetch_financial_data` now filters rows by
      ``ann_date <= as_of`` so only *already-disclosed* quarters are seen.

    * **Distress (困境)** — :func:`davis_analyzer.distress.calculate_distress_score`
      consumes the same disclosed-quarter history plus the PE/PB percentile
      from the valuation factor.

Default weights mirror ``DAVIS_DOUBLE_WEIGHTS`` (valuation 0.30 + prosperity
0.30 + distress 0.25 + trend 0.15), with momentum substituting for trend.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from loguru import logger

from davis_analyzer.constants import (
    CYCLICAL_FACTOR_WEIGHTS,
    SUPER_CYCLE_INDUSTRIES,
    SUPER_CYCLE_MIN_POSITIVE_QUARTERS,
    SUPER_CYCLE_PERSISTENCE_BONUS,
)
from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo
from davis_analyzer.valuation import (
    calculate_valuation_score,
    detect_cyclical,
    fetch_valuation_history,
)


@dataclass
class FactorConfig:
    """Weights and knobs for the cross-sectional factor blend.

    Weights need not sum to 1 — they are normalised inside :func:`_blend` so
    mis-configuration degrades gracefully rather than producing out-of-range
    scores.  Default weights approximate ``DAVIS_DOUBLE_WEIGHTS`` with
    momentum standing in for the trend leg.
    """

    momentum_weight: float = 0.25
    valuation_weight: float = 0.25
    prosperity_weight: float = 0.30
    distress_weight: float = 0.20
    # Skip stocks whose momentum engine returns data_sufficient=False.
    require_momentum_data: bool = True
    # Minimum quarters of disclosed financial data to attempt prosperity/distress.
    min_quarters_for_financial: int = 2


@dataclass
class FactorScore:
    """Per-stock factor result for a single rebalance date."""

    ts_code: str
    composite: float
    momentum_score: float | None = None
    valuation_score: float | None = None
    prosperity_score: float | None = None
    distress_score: float | None = None


def _blend(
    momentum: float | None,
    valuation: float | None,
    prosperity: float | None,
    distress: float | None,
    cfg: FactorConfig,
    is_cyclical: bool = False,
) -> float:
    """Weighted-normalised blend of the four sub-scores.

    Only available sub-scores participate; the normaliser divides by the sum
    of weights whose sub-score is present, so a missing factor does not
    silently drag the composite to zero.

    When *is_cyclical* is True (classical cyclical stock), the weights from
    ``CYCLICAL_FACTOR_WEIGHTS`` override *cfg* — valuation gets 0.40 (PB is
    the reliable anchor), prosperity drops to 0.15 (ΔG is mean-reverting).
    """
    if is_cyclical:
        w_mom = CYCLICAL_FACTOR_WEIGHTS["momentum"]
        w_val = CYCLICAL_FACTOR_WEIGHTS["valuation"]
        w_pros = CYCLICAL_FACTOR_WEIGHTS["prosperity"]
        w_dist = CYCLICAL_FACTOR_WEIGHTS["distress"]
    else:
        w_mom = cfg.momentum_weight
        w_val = cfg.valuation_weight
        w_pros = cfg.prosperity_weight
        w_dist = cfg.distress_weight

    total_w = 0.0
    acc = 0.0
    if momentum is not None:
        acc += momentum * w_mom
        total_w += w_mom
    if valuation is not None:
        acc += valuation * w_val
        total_w += w_val
    if prosperity is not None:
        acc += prosperity * w_pros
        total_w += w_pros
    if distress is not None:
        acc += distress * w_dist
        total_w += w_dist
    if total_w == 0:
        return 0.0
    return acc / total_w


def classify_stock(industry: str) -> str:
    """Classify a stock into one of three domain categories.

    Returns ``"classic_cyclical"``, ``"super_cycle"``, or ``"normal"``.

    * **classic_cyclical** — CYCLICAL_INDUSTRIES (steel/coal/chemicals/…):
      ΔG is mean-reverting → clamp + tilt to valuation weight.
    * **super_cycle** — SUPER_CYCLE_INDUSTRIES (AI hardware / semi equipment):
      ΔG is structurally persistent → no clamp, persistence bonus.
    * **normal** — everything else: default weights, no clamp.
    """
    if detect_cyclical(industry):
        return "classic_cyclical"
    if industry in SUPER_CYCLE_INDUSTRIES:
        return "super_cycle"
    return "normal"


def _count_consecutive_positive_delta_g(fin_data: list) -> int:
    """Count how many recent quarters have ΔG > 0 (consecutive, most-recent-first).

    Used for the super-cycle persistence bonus: a structural growth stock
    with ≥ N consecutive quarters of positive ΔG earns a bounded bonus.
    """
    # Build the YoY growth series (most-recent-first), same logic as prosperity.
    sorted_data = sorted(fin_data, key=lambda d: d.report_period)
    rev_growths = [
        d.yoy_revenue_growth * 100
        for d in sorted_data
        if d.yoy_revenue_growth is not None
    ]
    rev_growths = list(reversed(rev_growths))  # most-recent-first
    if len(rev_growths) < 2:
        return 0

    # Compute per-quarter ΔG (current - previous).
    delta_gs = []
    for i in range(len(rev_growths) - 1):
        delta_gs.append(rev_growths[i] - rev_growths[i + 1])

    # Count consecutive positive ΔG from the most recent.
    count = 0
    for dg in delta_gs:
        if dg > 0:
            count += 1
        else:
            break
    return count


# ─────────────────── Super-cycle early detection ───────────────────
#
# The core question: can we identify a structural super-cycle *before*
# the main price rally?
#
# V2 (tightened): fixes 6 issues from the V1 audit:
#   1. Pattern A requires momentum confirmation (G>50% AND mom>60)
#   2. Pattern C (whitelist) is a tiebreaker, never a standalone trigger
#   3. Negative filters: G>500% (low-base noise) is excluded
#   4. G>200% is capped at "emerging" (suspect low-base until confirmed)
#   5. hit-rate target: ~10-15% of universe (was 42%)
#
# Signal levels:
#   - **confirmed**: G>50%+momentum OR persistent ΔG acceleration
#   - **emerging**:  mid-G + high-ΔG, OR high-G without momentum
#   - **none**:      everything else


@dataclass
class SuperCycleSignal:
    """Result of super-cycle early-signal detection for one stock.

    Levels (V3):
        - ``confirmed``: high G + early momentum, or persistent ΔG acceleration.
        - ``emerging``: mid-G + high ΔG, or whitelist + G>15%.
        - ``potential``: high G but price suppressed (momentum <30).
          These are *compressed springs*: real growth the market hasn't
          priced in yet.  When the suppression lifts, the rally can be
          explosive.  Distinct from "none" — these deserve a watchlist.
        - ``none``: no signal.
    """

    level: str  # "confirmed" | "emerging" | "potential" | "none"
    consecutive_positive_dg: int  # quarters of ΔG > 0
    latest_g: float | None  # latest YoY revenue growth (%)
    latest_dg: float | None  # latest ΔG (pp)
    latest_profit_g: float | None  # latest YoY profit growth (%)
    latest_contract_liab: float | None  # latest 合同负债 (元) — V5
    contract_liab_yoy: float | None  # contract-liab YoY growth (%) — V5
    latest_capex: float | None  # latest capex (元) — V5
    capex_yoy: float | None  # capex YoY growth (%) — V5
    dg_trend: list[float]  # ΔG per quarter, most-recent-first
    trigger: str  # which pattern triggered


def detect_super_cycle_early_signal(
    fin_data: list,
    industry: str = "",
    momentum_score: float | None = None,
    min_consecutive_confirmed: int = 3,
    min_g_confirmed: float = 15.0,
    high_g_threshold: float = 50.0,
    high_g_momentum_confirmed: float = 30.0,
    high_g_momentum_potential: float = 30.0,
    mid_g_low: float = 20.0,
    mid_g_high: float = 50.0,
    mid_g_dg_required: float = 15.0,
    noise_g_cap: float = 500.0,
    suspect_g_cap: float = 200.0,
) -> SuperCycleSignal:
    """Detect structural super-cycle acceleration before the price rally.

    V4 changes (after anomaly analysis):

    * **Profit-direction filter**: Revenue growth alone is insufficient —
      87% of "high-G low-momentum" stocks are *revenue-up-profit-down*
      (price wars, low-base effects, unsustainable expansion).  V4
      requires the latest profit growth to be positive AND in the same
      direction as revenue growth.  This single filter is expected to
      eliminate the bulk of false positives that plagued V3.

    * The "potential" level (suppressed high-G) is retained but now also
      requires positive profit growth — a stock with G>50% but profit
      declining is no longer even "potential"; it's "none".

    Signal levels (V4):
      - ``confirmed``: G>50% + momentum>=30 + profit>0, OR persistent ΔG
      - ``emerging``: mid-G + high-ΔG + profit>0, OR whitelist + G>=15% + profit>0
      - ``potential``: G>50% + momentum<30 + profit>0 (suppressed but profitable)
      - ``none``: everything else (including all revenue-up-profit-down stocks)
    """
    is_whitelist = industry in SUPER_CYCLE_INDUSTRIES
    has_momentum = momentum_score is not None and momentum_score > 0

    if not fin_data or len(fin_data) < 2:
        return SuperCycleSignal(
            level="none", consecutive_positive_dg=0,
            latest_g=None, latest_dg=None, latest_profit_g=None,
            latest_contract_liab=None, contract_liab_yoy=None,
            latest_capex=None, capex_yoy=None,
            dg_trend=[], trigger="none",
        )

    sorted_data = sorted(fin_data, key=lambda d: d.report_period)
    rev_growths = [
        d.yoy_revenue_growth * 100
        for d in sorted_data
        if d.yoy_revenue_growth is not None
    ]
    rev_growths = list(reversed(rev_growths))

    # ── V4: Extract latest profit growth ──
    profit_growths = [
        d.yoy_profit_growth * 100
        for d in sorted_data
        if d.yoy_profit_growth is not None
    ]
    latest_profit_g = profit_growths[-1] if profit_growths else None  # ASC order, last = newest

    if len(rev_growths) < 2:
        return SuperCycleSignal(
            level="none", consecutive_positive_dg=0,
            latest_g=rev_growths[0] if rev_growths else None,
            latest_dg=None, latest_profit_g=latest_profit_g,
            latest_contract_liab=None, contract_liab_yoy=None,
            latest_capex=None, capex_yoy=None,
            dg_trend=[], trigger="none",
        )

    dg_trend = [rev_growths[i] - rev_growths[i + 1] for i in range(len(rev_growths) - 1)]

    consecutive = 0
    for dg in dg_trend:
        if dg > 0:
            consecutive += 1
        else:
            break

    latest_g = rev_growths[0]
    latest_dg = dg_trend[0] if dg_trend else None

    # ── Negative filter: extreme low-base noise ──
    if latest_g > noise_g_cap:
        return SuperCycleSignal(
            level="none", consecutive_positive_dg=consecutive,
            latest_g=round(latest_g, 1), latest_dg=round(latest_dg, 1) if latest_dg else None,
            latest_profit_g=round(latest_profit_g, 1) if latest_profit_g is not None else None,
            latest_contract_liab=None, contract_liab_yoy=None,
            latest_capex=None, capex_yoy=None,
            dg_trend=[round(d, 1) for d in dg_trend], trigger="none",
        )

    # ── V4: Profit-direction filter ──
    # Revenue growth without profit growth = price war / unsustainable / low-base.
    # These are NOT super-cycle candidates — they are exactly the "anomaly" stocks
    # where 92.4% declined in H1 2026.  Hard-exclude them.
    profit_healthy = (
        latest_profit_g is not None
        and latest_profit_g > 0  # profit must be growing
    )

    # ── V5: Balance-sheet demand/supply signals ──
    # contract_liab (合同负债) = customer prepayments — a leading indicator of
    #   future revenue.  If it's growing faster than revenue, demand is building
    #   faster than what's already been recognised → structural demand shift.
    # capex (购建固定资产等支付现金) = capital expenditure — a leading indicator
    #   of supply expansion.  In a super-cycle, companies invest ahead of demand.
    #   Growing capex confirms the company itself believes the cycle is real.
    contract_liab_series = [d.contract_liab for d in sorted_data if d.contract_liab is not None and d.contract_liab > 0]
    capex_series = [d.capex for d in sorted_data if d.capex is not None and d.capex > 0]

    latest_contract_liab = contract_liab_series[-1] if contract_liab_series else None
    latest_capex = capex_series[-1] if capex_series else None

    # YoY growth (shift by 4 quarters = same period last year)
    contract_liab_yoy = None
    if len(contract_liab_series) >= 5:  # need 5 to have current + 4 quarters back
        prev = contract_liab_series[-5]
        if prev > 0:
            contract_liab_yoy = (contract_liab_series[-1] / prev - 1) * 100

    capex_yoy = None
    if len(capex_series) >= 5:
        prev = capex_series[-5]
        if prev > 0:
            capex_yoy = (capex_series[-1] / prev - 1) * 100

    # V5 boost: if both contract_liab and capex are growing >20% YoY,
    # the company is seeing demand build-up AND investing in capacity —
    # a strong structural confirmation.  Upgrade one level (none→emerging,
    # emerging→confirmed) but only if profit is healthy.
    bs_confirmed = (
        profit_healthy
        and contract_liab_yoy is not None and contract_liab_yoy > 20
        and capex_yoy is not None and capex_yoy > 20
    )

    suspect_high_g = latest_g > suspect_g_cap

    # ── Pattern A: high G + early momentum + profit confirmed ──
    pattern_a = (latest_g > high_g_threshold and not suspect_high_g
                 and has_momentum and momentum_score >= high_g_momentum_confirmed
                 and profit_healthy)

    # ── Pattern A-suppressed: high G + profit healthy, but momentum low ──
    pattern_a_suppressed = (latest_g > high_g_threshold and not suspect_high_g
                            and profit_healthy
                            and (not has_momentum or momentum_score < high_g_momentum_potential))

    # Pattern B: mid G + high ΔG + profit healthy
    pattern_b = (mid_g_low <= latest_g <= mid_g_high
                 and latest_dg is not None and latest_dg > mid_g_dg_required
                 and profit_healthy)

    # Pattern D: persistent ΔG + profit healthy
    pattern_d = (consecutive >= min_consecutive_confirmed
                 and latest_g >= min_g_confirmed
                 and profit_healthy)

    # Pattern C (suspect): G in 200-500% range — still require profit positive
    pattern_c_suspect = suspect_high_g and profit_healthy

    # ── Classify ──
    if pattern_a or pattern_d:
        level = "confirmed"
        trigger = "high_g_early_momentum" if pattern_a else "persistent_dg"
    elif pattern_a_suppressed:
        level = "potential"
        trigger = "high_g_suppressed"
    elif pattern_b or pattern_c_suspect:
        level = "emerging"
        trigger = "mid_g_accel" if pattern_b else "suspect_high_g"
    else:
        level = "none"
        trigger = "none"

    # ── Pattern C whitelist boost: upgrade none→emerging only ──
    # V4: also require profit healthy for whitelist boost
    if (is_whitelist and level == "none" and latest_g is not None
            and latest_g >= 15.0 and profit_healthy):
        level = "emerging"
        trigger = "whitelist_boost"

    # ── V5: Balance-sheet confirmation upgrade ──
    # DISABLED: V5 precision validation showed BS-confirmed (contract_liab +
    # capex both >20% YoY) had only 11.5% precision (below V4's 22.9% and
    # below the ~9% random baseline).  The upgrade added 279 noisy stocks
    # that dragged overall precision from 22.9% to 16.7%.  The data fields
    # (contract_liab_yoy, capex_yoy) are still collected and returned in the
    # signal for reporting/analysis, but they no longer trigger level upgrades.
    # if bs_confirmed and level == "none":
    #     level = "emerging"
    #     trigger = "bs_demand_supply_confirmed"
    # elif bs_confirmed and level == "emerging":
    #     level = "confirmed"
    #     trigger = "bs_demand_supply_confirmed"

    return SuperCycleSignal(
        level=level,
        consecutive_positive_dg=consecutive,
        latest_g=round(latest_g, 1) if latest_g is not None else None,
        latest_dg=round(latest_dg, 1) if latest_dg is not None else None,
        latest_profit_g=round(latest_profit_g, 1) if latest_profit_g is not None else None,
        latest_contract_liab=round(latest_contract_liab, 0) if latest_contract_liab is not None else None,
        contract_liab_yoy=round(contract_liab_yoy, 1) if contract_liab_yoy is not None else None,
        latest_capex=round(latest_capex, 0) if latest_capex is not None else None,
        capex_yoy=round(capex_yoy, 1) if capex_yoy is not None else None,
        dg_trend=[round(d, 1) for d in dg_trend],
        trigger=trigger,
    )


def score_universe_at(
    client: TushareClient,
    as_of: date,
    stock_infos: dict[str, StockInfo],
    config: FactorConfig | None = None,
) -> dict[str, float]:
    """Score every stock in *stock_infos* as of *as_of*.

    Returns ``{ts_code: composite_score}`` for stocks that produced at least
    one valid sub-score.  Stocks with no usable data are omitted (the engine
    treats them as un-rankable).

    Each API failure is logged and skipped — one bad stock never aborts the
    whole cross-section.
    """
    cfg = config or FactorConfig()
    scores: dict[str, float] = {}

    for ts_code, info in stock_infos.items():
        try:
            # ── Classify stock into domain (classic_cyclical / super_cycle / normal) ──
            domain = classify_stock(info.industry)
            is_cyclical = domain == "classic_cyclical"
            is_super_cycle = domain == "super_cycle"

            # ── Momentum (point-in-time via today=as_of) ──
            mom_signal = analyze_momentum(client, ts_code, today=as_of)
            momentum_score: float | None = None
            if mom_signal is not None and mom_signal.data_sufficient:
                momentum_score = mom_signal.momentum_score
            elif mom_signal is not None and not cfg.require_momentum_data:
                momentum_score = mom_signal.momentum_score

            # ── Valuation (point-in-time via as_of) ──
            history = fetch_valuation_history(client, ts_code, as_of=as_of)
            valuation_score: float | None = None
            pe_pct = 0.5
            pb_pct = 0.5
            if history:
                valuation_score, pe_pct, pb_pct = calculate_valuation_score(history, is_cyclical)

            # ── Prosperity + Distress (point-in-time via as_of on fetcher) ──
            # fetch_financial_data filters by ann_date <= as_of, so the list
            # only contains quarters that were publicly disclosed as of as_of.
            # For classical cyclicals, is_cyclical=True triggers ΔG clamping
            # inside calculate_prosperity_score.
            prosperity_score: float | None = None
            distress_score: float | None = None
            fin_data = fetch_financial_data(client, ts_code, as_of=as_of)
            if len(fin_data) >= cfg.min_quarters_for_financial:
                prosp = calculate_prosperity_score(fin_data, is_cyclical=is_cyclical)
                prosperity_score = prosp.composite_score

                # Distress needs the valuation percentiles + latest balance sheet.
                if valuation_score is not None:
                    eps_history = [fd.eps for fd in fin_data]
                    roe_history = [fd.roe for fd in fin_data]
                    revenue_growth_history = [
                        fd.yoy_revenue_growth for fd in fin_data if fd.yoy_revenue_growth is not None
                    ]
                    profit_growth_history = [
                        fd.yoy_profit_growth for fd in fin_data if fd.yoy_profit_growth is not None
                    ]
                    latest = fin_data[0]
                    total_debt = latest.total_debt
                    total_assets = latest.total_assets
                    debt_ratio = total_debt / total_assets if total_assets > 0 else 0.0

                    distress = calculate_distress_score(
                        eps_history=eps_history,
                        pe_pct=pe_pct,
                        pb_pct=pb_pct,
                        debt_ratio=debt_ratio,
                        operating_cf=latest.operating_cf,
                        total_debt=total_debt,
                        total_assets=total_assets,
                        roe_history=roe_history,
                        revenue_history=revenue_growth_history,
                        profit_history=profit_growth_history,
                        delta_g=prosp.delta_g,
                        ts_code=ts_code,
                    )
                    distress_score = distress.total_score

            composite = _blend(
                momentum_score, valuation_score, prosperity_score, distress_score, cfg,
                is_cyclical=is_cyclical,
            )

            # ── Super-cycle persistence bonus ──
            # Structural-growth stocks with ≥ N consecutive quarters of
            # positive ΔG earn a bounded bonus.  This rewards genuine
            # multi-quarter acceleration (AI demand ramp, semi capex cycle)
            # that classical cyclicals cannot sustain.
            if is_super_cycle and fin_data and len(fin_data) >= cfg.min_quarters_for_financial:
                consecutive_pos = _count_consecutive_positive_delta_g(fin_data)
                if consecutive_pos >= SUPER_CYCLE_MIN_POSITIVE_QUARTERS:
                    composite += SUPER_CYCLE_PERSISTENCE_BONUS

            if all(
                s is None
                for s in (momentum_score, valuation_score, prosperity_score, distress_score)
            ):
                continue  # no usable signal — skip

            scores[ts_code] = round(composite, 2)
        except Exception:
            logger.debug("Factor scoring failed for {} at {}", ts_code, as_of)

    logger.info(
        "Cross-section scored: {}/{} stocks at as_of={}",
        len(scores),
        len(stock_infos),
        as_of,
    )
    return scores
