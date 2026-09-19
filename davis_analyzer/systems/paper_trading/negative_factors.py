"""Negative factor veto — filters out known loss-making patterns before buy.

Derived from a post-mortem of 5 zombie accounts (see
docs/方法论/负因子选股方法论_20260806.md). The philosophy: avoiding
mistakes is more reliable than chasing alpha, so this module is a
*one-strike veto* gate — any single rule firing blocks the buy.

Five rules, each backed by failed samples:

  ① 风口追高 (cyclical top-chasing):  industry PE percentile > 80 + momentum > 70
  ② 套牢装死 (zombie holding):       held > 365 days + unrealized PnL < -30%
  ③ 单一赛道集中 (concentration):     one industry > 50% of portfolio
  ④ 深套绝对止损 (stop-loss paralysis): current price < buy price × 0.5
  ⑤ 周期股PE陷阱 (cyclical PE trap): is_cyclical + PE pct < 30 + PB pct > 80

Rules ② and ④ also produce FORCE_SELL signals for already-held positions
that have decayed past the no-recovery threshold. This unblocks the
executor's existing stop-loss, which can't trigger on deeply underwater
positions (a structural flaw documented in the report).

Integration point: FactorThresholdStrategy.evaluate() calls
``NegativeFactorVeto.check_buy()`` for each buy candidate and
``check_holding()`` during the position scan. Both are opt-in via
``enable_negative_factors=True`` (default True from 2026-08-06).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from loguru import logger

# ── Thresholds (tuned from the 2026-08-06 post-mortem; re-calibrate via abx) ──

# Rule ①: 风口追高. High PE + high momentum = buying at a sector top.
TOPCHASE_PE_PCT = 80.0       # industry / stock PE percentile threshold
TOPCHASE_MOMENTUM = 70.0     # momentum score at which "hot" becomes "too hot"

# Rule ②: 套牢装死. Held too long + deep loss = zombie, force exit.
ZOMBIE_HOLDING_DAYS = 365    # days
ZOMBIE_LOSS_PCT = -30.0      # unrealized PnL % threshold

# Rule ③: 单一赛道集中. Single-industry cap.
CONCENTRATION_MAX = 0.50     # 50% of portfolio market value

# Rule ④: 深套绝对止损. Triggered when loss exceeds this regardless of volatility.
ABSOLUTE_STOP_LOSS_PCT = -50.0   # never let a position ride below -50%

# Rule ⑤: 周期股 PE 陷阱. Cyclical stocks look "cheap" on PE at cycle tops
# because earnings are temporarily extreme — but PB reveals the truth.
CYCLICAL_PE_PCT_MAX = 30.0
CYCLICAL_PB_PCT_MIN = 80.0


@dataclass
class VetoResult:
    """Outcome of a negative-factor check.

    For buy candidates: ``vetoed=True`` means "skip this stock".
    For holdings: ``action="FORCE_SELL"`` means "add a sell signal now".
    """
    vetoed: bool = False
    rule: str = ""              # short rule name, e.g. "zombie_holding"
    reason: str = ""            # human-readable detail
    action: Literal["SKIP_BUY", "FORCE_SELL", ""] = ""


# ── Public API ─────────────────────────────────────────────────────────


def check_buy(
    code: str,
    factors: dict,
    snapshot,
    portfolio_concentration: dict[str, float] | None = None,
    thresholds: dict | None = None,
) -> VetoResult:
    """Run buy-side negative-factor checks on a candidate.

    Args:
        code: ts_code of the buy candidate.
        factors: snapshot.factor_scores[code] dict (must contain "momentum").
        snapshot: MarketSnapshot (for prices, pe_percentile, industries).
        portfolio_concentration: {industry: weight} of current holdings, to
            check rule ③. If None, concentration check is skipped.
        thresholds: optional override dict for any of the THRESHOLD constants.

    Returns:
        VetoResult with vetoed=True if any rule fires.
    """
    t = _merge_thresholds(thresholds)

    mom = factors.get("momentum")
    # pe_percentile may live in snapshot OR in factor_scores (backtest path
    # fills factor_scores[code]["pe_pct"] but not always snapshot.pe_percentile).
    pe_pct = snapshot.pe_percentile.get(code)
    if pe_pct is None:
        pe_pct = factors.get("pe_pct") or factors.get("pe_percentile")
    if pe_pct is not None:
        pe_pct = pe_pct * 100 if pe_pct <= 1.0 else pe_pct  # normalize 0-1 → 0-100
    industry = snapshot.industries.get(code, "")

    # ── Rule ①: 风口追高 ──
    # EXEMPTION: stocks with strong volume-price confirmation (platform_breakout
    # or low_vol) bypass this veto — consistent with the PE exemption in
    # strategy.py, where technically-driven buys justify a high valuation.
    if pe_pct is not None and pe_pct > t["topchase_pe_pct"] \
       and mom is not None and mom > t["topchase_momentum"]:
        vol_sig = (snapshot.volume_signal.get(code) or {}) if hasattr(snapshot, "volume_signal") else {}
        sig_type = vol_sig.get("signal_type", "") if isinstance(vol_sig, dict) else ""
        if sig_type not in ("platform_breakout", "low_vol"):
            return VetoResult(
                True, "topchase",
                f"风口追高: PE分位{pe_pct:.0f}%>{t['topchase_pe_pct']:.0f}% "
                f"+ 动量{mom:.0f}>{t['topchase_momentum']:.0f}",
                "SKIP_BUY",
            )

    # ── Rule ③: 单一赛道集中 ──
    if portfolio_concentration and industry:
        weight = portfolio_concentration.get(industry, 0.0)
        if weight > t["concentration_max"]:
            return VetoResult(
                True, "concentration",
                f"集中度过高: {industry}占比{weight*100:.0f}%>{t['concentration_max']*100:.0f}%",
                "SKIP_BUY",
            )

    # ── Rule ⑤: 周期股 PE 陷阱 ──
    is_cyc = factors.get("is_cyclical", False)
    pb_pct = factors.get("pb_percentile") or factors.get("pb_pct")
    if pb_pct is not None:
        pb_pct = pb_pct * 100 if pb_pct <= 1.0 else pb_pct  # normalize
    if is_cyc and pe_pct is not None and pb_pct is not None \
       and pe_pct < t["cyclical_pe_pct_max"] and pb_pct > t["cyclical_pb_pct_min"]:
        return VetoResult(
            True, "cyclical_pe_trap",
            f"周期股PE陷阱: PE分位{pe_pct:.0f}%(低) 但 PB分位{pb_pct:.0f}%(高) = 景气顶点",
            "SKIP_BUY",
        )

    return VetoResult()


def check_holding(
    position,
    current_price: float,
    today: str,
    thresholds: dict | None = None,
) -> VetoResult:
    """Run holding-side checks (rules ② and ④) on an existing position.

    These rules produce FORCE_SELL signals, not buy vetoes — they unblock
    positions that the executor's stop-loss can't reach (deeply underwater).

    Args:
        position: Position dataclass (ts_code, avg_cost, entry_date).
        current_price: latest close for the position's ts_code.
        today: YYYYMMDD trade date (for holding-days calc).
        thresholds: optional override dict.

    Returns:
        VetoResult with action="FORCE_SELL" if a rule fires.
    """
    t = _merge_thresholds(thresholds)
    cost = position.avg_cost
    if cost <= 0 or current_price <= 0:
        return VetoResult()

    pnl_pct = (current_price / cost - 1.0) * 100.0

    # ── Rule ④: 深套绝对止损 (check first — most decisive) ──
    if pnl_pct <= t["absolute_stop_loss_pct"]:
        return VetoResult(
            True, "absolute_stop",
            f"绝对止损: 亏损{pnl_pct:.0f}%≤{t['absolute_stop_loss_pct']:.0f}%",
            "FORCE_SELL",
        )

    # ── Rule ②: 套牢装死 ──
    holding_days = _days_between(position.entry_date, today)
    if holding_days >= t["zombie_holding_days"] and pnl_pct < t["zombie_loss_pct"]:
        return VetoResult(
            True, "zombie",
            f"僵尸持仓: 持有{holding_days}天>{t['zombie_holding_days']}天 "
            f"+ 亏损{pnl_pct:.0f}%<{t['zombie_loss_pct']}%",
            "FORCE_SELL",
        )

    return VetoResult()


def compute_concentration(positions, current_prices: dict[str, float]) -> dict[str, float]:
    """Compute industry weights of current holdings for rule ③.

    Returns {industry: weight_in_portfolio}. Weights sum to ~1.0 for invested
    capital (cash excluded). Used as input to check_buy rule ③.
    """
    industry_mv: dict[str, float] = {}
    total_mv = 0.0
    for p in positions:
        price = current_prices.get(p.ts_code, p.avg_cost)
        mv = p.shares * price
        ind = getattr(p, "industry", "") or ""
        industry_mv[ind] = industry_mv.get(ind, 0.0) + mv
        total_mv += mv
    if total_mv <= 0:
        return {}
    return {ind: mv / total_mv for ind, mv in industry_mv.items()}


# ── Helpers ────────────────────────────────────────────────────────────


def _merge_thresholds(override: dict | None) -> dict:
    """Merge user overrides onto the module-level defaults."""
    defaults = {
        "topchase_pe_pct": TOPCHASE_PE_PCT,
        "topchase_momentum": TOPCHASE_MOMENTUM,
        "zombie_holding_days": ZOMBIE_HOLDING_DAYS,
        "zombie_loss_pct": ZOMBIE_LOSS_PCT,
        "concentration_max": CONCENTRATION_MAX,
        "absolute_stop_loss_pct": ABSOLUTE_STOP_LOSS_PCT,
        "cyclical_pe_pct_max": CYCLICAL_PE_PCT_MAX,
        "cyclical_pb_pct_min": CYCLICAL_PB_PCT_MIN,
    }
    if override:
        defaults.update({k: v for k, v in override.items() if v is not None})
    return defaults


def _days_between(entry_date: str, today: str) -> int:
    """Calendar days between two YYYYMMDD strings (0 on parse failure)."""
    from datetime import datetime
    try:
        d0 = datetime.strptime(str(entry_date), "%Y%m%d")
        d1 = datetime.strptime(str(today), "%Y%m%d")
        return max(0, (d1 - d0).days)
    except (ValueError, TypeError):
        return 0
