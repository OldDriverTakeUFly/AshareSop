"""Soft-penalty A/B test: compare different event_penalty_weight values.

Building on V2 (volume-only, our best), tests whether replacing the hard
event filter with a soft composite-score penalty improves returns.

4 variants (V2 reused as reference):
  V2_volume_only (reference)   — penalty_weight=0, hard filter off
  V5_soft_pwr_05               — penalty_weight=0.5
  V6_soft_pwr_10               — penalty_weight=1.0
  V7_soft_tech                 — penalty_weight=0.5 + tech_weight=0.075
"""
import os, sys, time, sqlite3, json
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="ERROR")

from stockhot.data_layer.market_db import get_connection as get_market_conn
from stockhot.storage.database import init_database, DB_PATH
from davis_analyzer.paper_trading.account import PaperAccount
from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
from davis_analyzer.paper_trading.executor import run_backfill_auto

init_database()

START = "20260105"
END = "20260715"
INITIAL_CAPITAL = 1_000_000
UNIVERSE_SIZE = 200
SCORING_FREQUENCY = 3

BASE_PARAMS = dict(
    max_positions=10, buy_momentum=65, sell_momentum=40,
    buy_holder_min=35, buy_dividend_min=55, buy_forecast_min=70,
    buy_prosperity_min=45, min_secondary_dims=1,
    volume_weight=0.05, enable_volume_risk=True,
    enable_event_filter=False,  # hard filter always off (we're testing soft)
)

VARIANTS = [
    ("V2_volume_only", {**BASE_PARAMS, "event_penalty_weight": 0.0, "tech_weight": 0.0}),
    ("V5_soft_pwr_05", {**BASE_PARAMS, "event_penalty_weight": 0.5, "tech_weight": 0.0}),
    ("V6_soft_pwr_10", {**BASE_PARAMS, "event_penalty_weight": 1.0, "tech_weight": 0.0}),
    ("V7_soft_tech",   {**BASE_PARAMS, "event_penalty_weight": 0.5, "tech_weight": 0.075}),
]


def build_universe(top_n: int) -> list[str]:
    with get_market_conn() as c:
        ref_row = c.execute(
            "SELECT MAX(trade_date) FROM daily_price WHERE trade_date < ?", (START,)
        ).fetchone()
        ref_end = ref_row[0] if ref_row and ref_row[0] else "20251231"
        rows = c.execute("""
            SELECT a.ts_code FROM daily_price a
            JOIN daily_price b ON a.ts_code=b.ts_code
              AND b.trade_date = (SELECT MAX(trade_date) FROM daily_price
                                  WHERE ts_code=a.ts_code AND trade_date <= '20251001')
            WHERE a.trade_date = ? AND a.close > 0 AND b.close > 0
            ORDER BY (a.close / b.close - 1) DESC LIMIT ?
        """, (ref_end, top_n)).fetchall()
    return [r[0] for r in rows]


def reset_account(name: str, config: dict) -> PaperAccount:
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute("SELECT id FROM paper_accounts WHERE name=?", (name,)).fetchone()
        if row:
            aid = row[0]
            for tbl in ("paper_positions", "paper_trades",
                        "paper_nav_history", "paper_shadow_trades"):
                c.execute(f"DELETE FROM {tbl} WHERE account_id=?", (aid,))
            c.execute("DELETE FROM paper_accounts WHERE id=?", (aid,))
            c.commit()
    return PaperAccount.create(
        name=name, strategy_name="factor_threshold",
        initial_capital=INITIAL_CAPITAL, config=config,
    )


def max_drawdown(nav_history: list[float]) -> float:
    peak = nav_history[0] if nav_history else 0
    mdd = 0.0
    for v in nav_history:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > mdd: mdd = dd
    return mdd


def run_variant(label: str, params: dict, universe: list[str]) -> dict:
    print(f"\n{'='*70}", flush=True)
    print(f"  {label}: epw={params['event_penalty_weight']} tw={params['tech_weight']}", flush=True)
    print(f"{'='*70}", flush=True)
    account = reset_account(f"sp_{label}", params)
    strategy = FactorThresholdStrategy(**params)
    t0 = time.time()
    results = run_backfill_auto(
        account, strategy, START, END,
        universe_codes=universe, scoring_frequency=SCORING_FREQUENCY,
    )
    elapsed = time.time() - t0
    nav_rows = account.get_nav_history()
    nav_history = [r.total_equity for r in nav_rows] if nav_rows else []
    final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
    total_ret = (final_nav / INITIAL_CAPITAL - 1) * 100
    mdd = max_drawdown(nav_history) if nav_history else 0
    trades = account.get_trades()
    account.close()
    return {
        "label": label,
        "event_penalty_weight": params["event_penalty_weight"],
        "tech_weight": params["tech_weight"],
        "final_nav": final_nav, "return_pct": total_ret,
        "max_drawdown_pct": mdd, "n_trades": len(trades),
        "n_buys": sum(1 for t in trades if t.action == "BUY"),
        "distinct_stocks": len({t.ts_code for t in trades}),
        "elapsed_sec": elapsed,
    }


def main():
    print(f"\nBuilding universe (top {UNIVERSE_SIZE} by 90d return)...")
    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe: {len(universe)} stocks\n")

    # Reuse V2 if already done in 4-way backtest
    results = []
    # Check if V2_volume exists from full_backtest
    try:
        with sqlite3.connect(DB_PATH) as c:
            row = c.execute(
                "SELECT COUNT(*) FROM paper_nav_history n "
                "JOIN paper_accounts a ON n.account_id=a.id "
                "WHERE a.name='abx_V2_volume'"
            ).fetchone()
            if row[0] >= 120:
                print("  [reuse] V2_volume from full_backtest (abx_V2_volume)")
                account = PaperAccount.load("abx_V2_volume")
                nav_rows = account.get_nav_history()
                nav_history = [r.total_equity for r in nav_rows] if nav_rows else []
                trades = account.get_trades()
                final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
                results.append({
                    "label": "V2_volume_only (reused)",
                    "event_penalty_weight": 0.0, "tech_weight": 0.0,
                    "final_nav": final_nav,
                    "return_pct": (final_nav / INITIAL_CAPITAL - 1) * 100,
                    "max_drawdown_pct": max_drawdown(nav_history) if nav_history else 0,
                    "n_trades": len(trades),
                    "n_buys": sum(1 for t in trades if t.action == "BUY"),
                    "distinct_stocks": len({t.ts_code for t in trades}),
                    "elapsed_sec": 0,
                })
                account.close()
    except Exception:
        pass

    # If V2 wasn't reused, run it
    if not results:
        results.append(run_variant("V2_volume_only",
                                   {**BASE_PARAMS, "event_penalty_weight": 0.0, "tech_weight": 0.0},
                                   universe))

    for label, params in VARIANTS[1:]:  # skip V2 (already added)
        try:
            r = run_variant(label, params, universe)
            results.append(r)
            print(f"\n  → {label}: ret={r['return_pct']:+.2f}% MDD={r['max_drawdown_pct']:.2f}% "
                  f"trades={r['n_trades']} ({r['elapsed_sec']/60:.1f}min)", flush=True)
        except Exception as e:
            import traceback
            print(f"\n  ✗ {label} FAILED: {e}", flush=True)
            traceback.print_exc()

    # ── Final table ──
    print("\n\n" + "=" * 90, flush=True)
    print(f"  SOFT-PENALTY A/B — {START} → {END}", flush=True)
    print("=" * 90, flush=True)
    print(f"{'Variant':<22} {'PenaltyWt':>9} {'TechW':>6} {'Return%':>9} {'MaxDD%':>8} "
          f"{'Trades':>7} {'Δ vs V2':>9}", flush=True)
    print("-" * 90, flush=True)
    v2_ret = results[0]["return_pct"]
    for r in results:
        d = r["return_pct"] - v2_ret
        print(f"{r['label']:<22} {r['event_penalty_weight']:>9.2f} "
              f"{r['tech_weight']:>6.3f} {r['return_pct']:>+8.2f}% "
              f"{r['max_drawdown_pct']:>7.2f}% {r['n_trades']:>7} {d:>+8.2f}pp",
              flush=True)
    print("=" * 90, flush=True)

    with open("logs/soft_penalty_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)


if __name__ == "__main__":
    main()
