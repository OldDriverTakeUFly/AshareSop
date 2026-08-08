"""A/B backtest: negative-factor veto ON vs OFF.

Creates two accounts with identical FactorThresholdStrategy configs, except:
  - A (baseline): enable_negative_factors=False
  - B (experiment): enable_negative_factors=True

Runs both through run_backfill_auto over the same window, then prints a
comparison table (return / Sharpe / max-drawdown / win-rate / zombie-count).

This is the empirical validation of docs/方法论/负因子选股方法论_20260806.md.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/negative_factor_abx.py \
        [--start 20210101] [--end 20260731] [--universe-size 50]
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger

logger.remove()
logger.add(sys.stderr, level="WARNING")


def _max_drawdown(equities: list[float]) -> float:
    if len(equities) < 2:
        return 0.0
    peak, max_dd = equities[0], 0.0
    for eq in equities:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)
    return round(max_dd, 2)


def _sharpe(daily_returns: list[float]) -> float:
    if len(daily_returns) < 2:
        return 0.0
    mean_r = sum(daily_returns) / len(daily_returns)
    var = sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std = math.sqrt(var) if var > 0 else 0
    if std == 0:
        return 0.0
    return round(mean_r / std * math.sqrt(252) / 100, 2)  # daily_return is in %


def _summarize(account, label: str) -> dict:
    """Compute performance metrics for one account."""
    nav = account.get_nav_history()
    trades = account.get_trades()
    positions = account.get_positions()
    if not nav:
        return {"label": label, "error": "no NAV history"}

    equities = [n.total_equity for n in nav]
    daily_returns = [n.daily_return for n in nav if n.daily_return is not None]
    initial = account.initial_capital
    total_ret = round((equities[-1] / initial - 1) * 100, 2)
    years = len(nav) / 252
    annualised = round(((equities[-1] / initial) ** (1 / years) - 1) * 100, 2) if years > 0 else 0

    sells = [t for t in trades if t.action == "SELL"]
    wins = [t for t in sells if "止损" not in (t.signal_reason or "")]
    win_rate = round(len(wins) / len(sells) * 100, 1) if sells else 0

    # Zombie count: positions held >365d with loss (approx via entry_date)
    today = datetime.now().strftime("%Y%m%d")
    zombies = 0
    for p in positions:
        try:
            d0 = datetime.strptime(str(p.entry_date), "%Y%m%d")
            if (datetime.now() - d0).days > 365:
                zombies += 1
        except (ValueError, TypeError):
            pass

    return {
        "label": label,
        "total_return": total_ret,
        "annualised": annualised,
        "max_drawdown": _max_drawdown(equities),
        "sharpe": _sharpe(daily_returns),
        "win_rate": win_rate,
        "trades": len(trades),
        "sells": len(sells),
        "positions": len(positions),
        "zombies": zombies,
        "final_equity": round(equities[-1], 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Negative-factor A/B backtest")
    parser.add_argument("--start", default="20210101")
    parser.add_argument("--end", default="20260731")
    parser.add_argument("--universe-size", type=int, default=50)
    parser.add_argument("--universe-codes", type=str, default="",
                        help="Comma-separated ts_codes for custom universe (overrides --universe-size)")
    parser.add_argument("--scoring-freq", type=int, default=5)
    args = parser.parse_args()

    from davis_analyzer.paper_trading.account import PaperAccount
    from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
    from davis_analyzer.paper_trading.executor import run_backfill_auto

    # Shared strategy config — only enable_negative_factors differs.
    base_cfg = dict(
        max_positions=10,
        buy_momentum=65,
        sell_momentum=40,
        buy_holder_min=35,
        buy_dividend_min=55,
        buy_prosperity_min=45,
        min_secondary_dims=1,
        enable_adaptive_sell=True,
        risk_stop_multiplier=0.70,
    )

    print(f"[abx] A/B backtest: {args.start} → {args.end}, universe={args.universe_size}")
    print(f"[abx] Creating accounts...")

    # Account A: baseline (no negative factors)
    strat_a = FactorThresholdStrategy(enable_negative_factors=False, **base_cfg)
    acct_a = PaperAccount.create(
        name="nf_A_baseline", strategy_name="factor_threshold", initial_capital=1_000_000
    )

    # Account B: experiment (negative factors ON)
    strat_b = FactorThresholdStrategy(enable_negative_factors=True, **base_cfg)
    acct_b = PaperAccount.create(
        name="nf_B_negative", strategy_name="factor_threshold", initial_capital=1_000_000
    )

    # Build universe: custom codes or top-N by market cap
    if args.universe_codes:
        universe = [c.strip() for c in args.universe_codes.split(",") if c.strip()]
        print(f"[abx] Custom universe: {len(universe)} stocks")
    else:
        from davis_analyzer.tushare_client import TushareClient
        client = TushareClient()
        from davis_analyzer.stock_universe import build_stock_universe
        universe = [s.ts_code for s in build_stock_universe(client)[:args.universe_size]]
    print(f"[abx] Universe: {len(universe)} stocks")

    # Run backfill for both (this is the slow part — ~30-60min each)
    for label, acct, strat in [("A_baseline", acct_a, strat_a), ("B_negative", acct_b, strat_b)]:
        print(f"\n[abx] === Running {label} ===")
        try:
            run_backfill_auto(
                account=acct,
                strategy=strat,
                start_date=args.start,
                end_date=args.end,
                universe_codes=universe,
                scoring_frequency=args.scoring_freq,
            )
        except Exception as exc:
            print(f"[abx] {label} FAILED: {type(exc).__name__}: {exc}")
            return 1

    # Summarize
    sa = _summarize(acct_a, "A_baseline (无负因子)")
    sb = _summarize(acct_b, "B_negative (有负因子)")

    print("\n" + "=" * 85)
    print("A/B 对比结果")
    print("=" * 85)
    print(f"{'指标':<16} {'A(无负因子)':>16} {'B(有负因子)':>16} {'差异':>16}")
    print("-" * 85)
    for key in ["total_return", "annualised", "max_drawdown", "sharpe",
                "win_rate", "trades", "zombies", "final_equity"]:
        va = sa.get(key, 0)
        vb = sb.get(key, 0)
        try:
            diff = vb - va
            diff_str = f"{diff:+.2f}" if isinstance(vb, float) else f"{diff:+d}"
        except TypeError:
            diff_str = "—"
        suffix = "%" if key in ("total_return", "annualised", "max_drawdown", "win_rate") else ""
        print(f"{key:<16} {va:>14,.2f}{suffix} {vb:>14,.2f}{suffix} {diff_str:>16}")

    # Verdict
    dd_improve = sa.get("max_drawdown", 0) - sb.get("max_drawdown", 0)
    ret_diff = sb.get("total_return", 0) - sa.get("total_return", 0)
    print()
    if dd_improve > 5 and ret_diff > -5:
        print(f"✅ 负因子有效: 最大回撤改善 {dd_improve:.1f}pp, 收益差异 {ret_diff:+.1f}pp")
    elif ret_diff < -10:
        print(f"⚠️ 负因子损害收益 {ret_diff:.1f}pp — 需调阈值")
    else:
        print(f"📋 负因子效果中性: 回撤{dd_improve:+.1f}pp, 收益{ret_diff:+.1f}pp")

    return 0


if __name__ == "__main__":
    sys.exit(main())
