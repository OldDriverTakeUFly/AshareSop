"""International market resonance overlay for A-share regime detection.

A *parallel* signal layer that reads overseas market data (US 10Y yield,
USD/JPY, US-VIX, US equity indices) and computes a 0–100 overseas risk
score. This score is used to DOWNGRADE the HMM-based market regime — never
to upgrade it — so international turmoil can force ``bull → neutral`` or
``→ bear`` but never the reverse.

Design rationale (full thread in conversation 2026-07-30):
  - HMM training stays single-feature (SH returns). Multi-feature HMM was
    tested by the original author (SH+CYB) and found *worse* (Sharpe
    1.521→1.440), so we don't pollute the training set.
  - Instead this module sits *after* ``get_market_regime`` as a confirmation
    overlay, mirroring the existing iVIX overlay pattern in executor.py.
  - Thresholds use *absolute* values (VIX>30, USD/JPY>160, 10Y +10bp/day)
    rather than historical percentiles, because overseas data history is
    short (~5 months) and percentile ranks would be unreliable.

The four sub-signals encode the crisis chains established earlier:
  1. US 10Y surge  — "oil → rate-hike → equity-multiple compression" chain
  2. US-VIX panic — leading indicator of the 7/17 tech selloff
  3. USD/JPY extreme — carry-trade unwind risk (2024-08-05 crash trigger)
  4. US equity overnight gap — global risk contagion
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from loguru import logger

from stockhot.storage.database import get_connection as get_stockhot_conn

RegimeState = Literal["bull", "bear", "neutral"]


# ── Signal weights (sum to 1.0) ────────────────────────────────────────
# Tuned by macro reasoning, not optimization — these reflect the relative
# importance of each chain in the current regime (滞胀 + yen carry risk).
WEIGHT_BOND_YIELD = 0.30   # rate-driven valuation compression
WEIGHT_US_VIX = 0.25       # equity panic leading indicator
WEIGHT_USDJPY = 0.25       # liquidity/carry unwind trigger
WEIGHT_US_EQUITY = 0.20    # overnight contagion

# ── Absolute thresholds ────────────────────────────────────────────────
# 10Y daily move (bp). +10bp/day = meaningful rate surge; +20bp = violent.
BOND_SURGE_BP_DAILY = 10.0
BOND_SURGE_BP_DAILY_MAX = 20.0

# US-VIX levels. 20 = calm, 25 = unease, 30 = fear, 40+ = panic.
VIX_CALM = 20.0
VIX_FEAR = 30.0
VIX_PANIC = 40.0

# USD/JPY carry-trade thresholds. 160 = BOJ intervention line (2024-08-05
# crash began near here); 165 = extreme, unwind imminent.
USDJPY_WARN = 160.0
USDJPY_EXTREME = 165.0

# US equity overnight drop (%). -2% = notable, -4% = crash signal.
US_EQUITY_DROP_WARN = -2.0
US_EQUITY_DROP_CRASH = -4.0

# ── Downgrade thresholds (applied to the composite 0–100 score) ───────
# >= FORCE_BEAR  → override to bear regardless of HMM
# >= DOWNGRADE   → bull demoted to neutral
FORCE_BEAR_SCORE = 70.0
DOWNGRADE_SCORE = 50.0


@dataclass
class SubSignal:
    """One of the four international risk sub-signals."""
    name: str
    score: float          # 0–100 contribution to composite
    raw_value: float | None
    detail: str           # human-readable explanation


@dataclass
class InternationalRisk:
    """Composite overseas risk assessment for a single trade date."""
    trade_date: str
    composite_score: float            # 0–100, higher = more dangerous
    sub_signals: list[SubSignal] = field(default_factory=list)
    data_sufficient: bool = True      # False if too many fields missing
    confidence_note: str = ""

    @property
    def level(self) -> str:
        """Qualitative risk band."""
        if self.composite_score >= FORCE_BEAR_SCORE:
            return "极端"
        if self.composite_score >= DOWNGRADE_SCORE:
            return "偏高"
        if self.composite_score >= 30:
            return "中等"
        return "偏低"


# ── Data loading ───────────────────────────────────────────────────────


def _load_overseas_row(trade_date_dash: str) -> dict | None:
    """Load a single day's overseas data (YYYY-MM-DD format) from stockhot.db.

    Returns None if the row doesn't exist. invest_overseas_market lives in
    the stockhot DB (cross-database read, same pattern as strategy_signal.py).
    """
    try:
        with get_stockhot_conn() as conn:
            row = conn.execute(
                "SELECT date, us_10y, us_2y, us_10y_change_bp, usd_jpy, "
                "us_vix, sp500_pct, nasdaq_pct, dow_pct, vix "
                "FROM invest_overseas_market WHERE date=?",
                (trade_date_dash,),
            ).fetchone()
        if row is None:
            return None
        return {k: row[k] for k in row.keys()}
    except Exception as exc:
        logger.debug("overseas row load failed for {}: {}", trade_date_dash, exc)
        return None


def _load_prev_overseas(trade_date_dash: str, days_back: int = 7) -> dict | None:
    """Load the most recent overseas row *before* trade_date (for rate change)."""
    try:
        cutoff = (datetime.strptime(trade_date_dash, "%Y-%m-%d")
                  - timedelta(days=days_back)).strftime("%Y-%m-%d")
        with get_stockhot_conn() as conn:
            row = conn.execute(
                "SELECT date, us_10y, us_vix FROM invest_overseas_market "
                "WHERE date < ? AND date >= ? AND us_10y IS NOT NULL "
                "ORDER BY date DESC LIMIT 1",
                (trade_date_dash, cutoff),
            ).fetchone()
        return {k: row[k] for k in row.keys()} if row else None
    except Exception:
        return None


# ── Sub-signal scorers ─────────────────────────────────────────────────


def _score_bond_yield(row: dict, prev: dict | None) -> SubSignal:
    """US 10Y surge → rate-driven valuation compression risk."""
    raw = row.get("us_10y")
    chg_bp = row.get("us_10y_change_bp")
    # If stored change is missing, derive from previous row.
    if (chg_bp is None or chg_bp == 0) and prev and prev.get("us_10y") and raw:
        chg_bp = round((raw - prev["us_10y"]) * 100, 2)

    if chg_bp is None:
        return SubSignal("bond_yield", 0.0, None, "美债数据缺失")

    # Rising yields are the risk. Score ramps from 0 (flat) to 100 (violent surge).
    # A +10bp day scores ~50; +20bp scores 100. Falls (negative bp) score 0.
    if chg_bp <= 0:
        score = 0.0
        detail = f"10Y {raw}% ({chg_bp:+.1f}bp, 利率回落/持稳，无压力)"
    else:
        score = min(100.0, (chg_bp / BOND_SURGE_BP_DAILY_MAX) * 100)
        detail = f"10Y {raw}% ({chg_bp:+.1f}bp, 利率飙升{'⚠️' if chg_bp >= BOND_SURGE_BP_DAILY else ''})"
    return SubSignal("bond_yield", score, chg_bp, detail)


def _score_us_vix(row: dict, prev: dict | None) -> SubSignal:
    """US-VIX panic level. Falls back to QVIX (China 50ETF) if US-VIX missing."""
    raw = row.get("us_vix")
    source = "US-VIX"
    if raw is None:
        raw = row.get("vix")  # QVIX fallback
        source = "QVIX(替代)"
    if raw is None:
        return SubSignal("us_vix", 0.0, None, "VIX 数据缺失")

    # Score: 20→0, 30→50, 40+→100 (linear ramp in the danger zone).
    if raw <= VIX_CALM:
        score = 0.0
    elif raw >= VIX_PANIC:
        score = 100.0
    else:
        score = (raw - VIX_CALM) / (VIX_PANIC - VIX_CALM) * 100
    flag = "⚠️恐慌" if raw >= VIX_FEAR else ""
    detail = f"{source}={raw:.1f} {flag}"
    return SubSignal("us_vix", score, raw, detail)


def _score_usdjpy(row: dict) -> SubSignal:
    """USD/JPY extreme weakness — carry-trade unwind trigger."""
    raw = row.get("usd_jpy")
    if raw is None:
        return SubSignal("usd_jpy", 0.0, None, "日元数据缺失")

    # Score: <=158 → 0, 160 → ~33, 165 → 100. Above 160 the carry-trade
    # unwind risk grows non-linearly (2024-08-05 began at ~160).
    if raw <= USDJPY_WARN:
        # Still score modestly above 158 to reflect mounting pressure.
        score = max(0.0, (raw - 158) / (USDJPY_WARN - 158) * 33) if raw > 158 else 0.0
    elif raw >= USDJPY_EXTREME:
        score = 100.0
    else:
        score = 33.0 + (raw - USDJPY_WARN) / (USDJPY_EXTREME - USDJPY_WARN) * 67.0
    flag = "⚠️套息脆弱" if raw >= USDJPY_WARN else ""
    detail = f"USD/JPY={raw:.2f} {flag}"
    return SubSignal("usd_jpy", score, raw, detail)


def _score_us_equity(row: dict) -> SubSignal:
    """US equity overnight drop — global risk contagion."""
    # Use the worst of S&P/Nasdaq/Dow as the contagion proxy.
    drops = []
    for col in ("sp500_pct", "nasdaq_pct", "dow_pct"):
        v = row.get(col)
        if v is not None:
            drops.append(v)
    if not drops:
        return SubSignal("us_equity", 0.0, None, "美股数据缺失")

    worst = min(drops)  # most negative
    # Score: 0 → 0%, -2% → 50, -4%+ → 100. Rallies (positive) score 0.
    if worst >= 0:
        score = 0.0
    else:
        score = min(100.0, abs(worst) / abs(US_EQUITY_DROP_CRASH) * 100)
    flag = "⚠️暴跌" if worst <= US_EQUITY_DROP_WARN else ""
    detail = f"美股最差{worst:+.2f}% {flag}"
    return SubSignal("us_equity", score, worst, detail)


# ── Public API ─────────────────────────────────────────────────────────


def get_international_risk(trade_date: str) -> InternationalRisk:
    """Compute overseas risk score for a trade date.

    Args:
        trade_date: YYYYMMDD (A-share format) or YYYY-MM-DD.

    Returns:
        InternationalRisk with composite 0–100 score and sub-signal details.
        Returns a zero-score result if data is unavailable (fail-safe:
        missing international data never forces a downgrade).
    """
    dash = trade_date.replace("-", "")
    dash = f"{dash[:4]}-{dash[4:6]}-{dash[6:8]}"

    row = _load_overseas_row(dash)
    if row is None:
        # No data at all — fail safe (no downgrade on missing data).
        return InternationalRisk(
            trade_date=trade_date, composite_score=0.0,
            data_sufficient=False,
            confidence_note="无国际数据，跳过 overlay",
        )

    prev = _load_prev_overseas(dash)

    subs = [
        _score_bond_yield(row, prev),
        _score_us_vix(row, prev),
        _score_usdjpy(row),
        _score_us_equity(row),
    ]

    # Weighted composite. Missing sub-signals contribute 0 (fail-safe).
    weights = {
        "bond_yield": WEIGHT_BOND_YIELD,
        "us_vix": WEIGHT_US_VIX,
        "usd_jpy": WEIGHT_USDJPY,
        "us_equity": WEIGHT_US_EQUITY,
    }
    composite = sum(s.score * weights[s.name] for s in subs)

    # Data sufficiency: if >=3 of 4 sub-signals have no data, mark insufficient.
    missing = sum(1 for s in subs if s.raw_value is None)
    sufficient = missing < 3

    note = f"{['bond','vix','jpy','eq'][0]}→" if False else ""
    conf = "高" if missing == 0 else ("中" if missing <= 1 else "低")

    risk = InternationalRisk(
        trade_date=trade_date,
        composite_score=round(composite, 1),
        sub_signals=subs,
        data_sufficient=sufficient,
        confidence_note=f"置信度{conf}({4-missing}/4信号可用)",
    )
    logger.info("overseas risk {} = {:.1f} [{}]", trade_date, composite, risk.level)
    return risk


def apply_overseas_overlay(regime: RegimeState, risk: InternationalRisk) -> RegimeState:
    """Downgrade a regime based on overseas risk. Never upgrades.

    - score >= 70 → bear (override)
    - score >= 50 → bull demoted to neutral
    - score <  50 → unchanged
    """
    if not risk.data_sufficient:
        return regime  # fail-safe: no data, no downgrade

    s = risk.composite_score
    if s >= FORCE_BEAR_SCORE:
        return "bear" if regime != "bear" else regime
    if s >= DOWNGRADE_SCORE and regime == "bull":
        return "neutral"
    return regime


# ── Batch backfill (for validation/research) ───────────────────────────


def backfill_risk_scores(start_date: str, end_date: str) -> list[InternationalRisk]:
    """Compute risk scores for a date range — used to validate the overlay
    against historical events (e.g., did it warn before the 7/17 crash?).

    Args:
        start_date, end_date: YYYYMMDD or YYYY-MM-DD.
    """
    start = start_date.replace("-", "")
    end = end_date.replace("-", "")
    results = []
    cur = datetime.strptime(start, "%Y%m%d")
    last = datetime.strptime(end, "%Y%m%d")
    while cur <= last:
        results.append(get_international_risk(cur.strftime("%Y%m%d")))
        cur += timedelta(days=1)
    return results
