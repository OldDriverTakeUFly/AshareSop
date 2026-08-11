"""Full 127-day backtest comparing cumulative optimizations.

4 variants, all on same universe (top-200 by 90d return) and window:
  V1_baseline_202505  — original pre-volume-price config (legacy reference)
  V2_volume_only      — +量价 (vw=0.05, enable_volume_risk=True)
  V3_volume_event     — +事件硬门槛 (enable_event_filter=True)
  V4_all_optimized    — +技术因子 (tech_weight=0.075) [DEFAULT config now]

V4 IS the new default config. V1 is the pre-optimization baseline.
Delta V4 - V1 = total cumulative improvement from this research.
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
    max_positions=10,
    buy_momentum=65,
    sell_momentum=40,
    buy_holder_min=35,
    buy_dividend_min=55,
    buy_forecast_min=70,
    buy_prosperity_min=45,
    min_secondary_dims=1,
)

# 4 variants (cumulative)
VARIANTS = [
    ("V1_baseline", {
        **BASE_PARAMS,
        "volume_weight": 0.0, "enable_volume_risk": False,
        "enable_event_filter": False, "tech_weight": 0.0,
    }),
    ("V2_volume", {
        **BASE_PARAMS,
        "volume_weight": 0.05, "enable_volume_risk": True,
        "enable_event_filter": False, "tech_weight": 0.0,
    }),
    ("V3_volume_event", {
        **BASE_PARAMS,
        "volume_weight": 0.05, "enable_volume_risk": True,
        "enable_event_filter": True, "tech_weight": 0.0,
    }),
    ("V4_all_optimized", {
        **BASE_PARAMS,
        "volume_weight": 0.05, "enable_volume_risk": True,
        "enable_event_filter": True, "tech_weight": 0.075,
    }),
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
    extras = {k: v for k, v in params.items() if k not in BASE_PARAMS}
    print(f"  {label}: extras={extras}", flush=True)
    print(f"{'='*70}", flush=True)
    account = reset_account(f"abx_{label}", params)
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
    n_trades = len(trades)
    n_buys = sum(1 for t in trades if t.action == "BUY")
    n_sells = sum(1 for t in trades if t.action == "SELL")
    distinct_stocks = len({t.ts_code for t in trades})
    highvol_sells = sum(1 for t in trades if t.action == "SELL"
                        and "高位放量" in (t.signal_reason or ""))
    account.close()
    return {
        "label": label, "extras": extras,
        "final_nav": final_nav, "return_pct": total_ret,
        "max_drawdown_pct": mdd, "n_trades": n_trades,
        "n_buys": n_buys, "n_sells": n_sells,
        "distinct_stocks": distinct_stocks,
        "highvol_sells": highvol_sells,
        "elapsed_sec": elapsed,
    }


def main():
    print(f"\nBuilding universe (top {UNIVERSE_SIZE} by 90d return)...")
    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe: {len(universe)} stocks")
    print(f"  Window: {START} → {END}, scoring every {SCORING_FREQUENCY} days")
    print(f"  Total: {len(VARIANTS)} variants × ~50min = ~{len(VARIANTS)*50//60} hours\n")

    results = []
    for label, params in VARIANTS:
        # Resume support: skip if account already has full nav history
        with sqlite3.connect(DB_PATH) as c:
            row = c.execute(
                "SELECT COUNT(*) FROM paper_nav_history n "
                "JOIN paper_accounts a ON n.account_id=a.id "
                "WHERE a.name=?", (f"abx_{label}",)
            ).fetchone()
            n_navs = row[0] if row else 0
        if n_navs >= 120 and "--rerun" not in sys.argv:
            print(f"\n  [skip] abx_{label} already has {n_navs} nav rows; reuse")
            account = PaperAccount.load(f"abx_{label}")
            nav_rows = account.get_nav_history()
            nav_history = [r.total_equity for r in nav_rows] if nav_rows else []
            final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
            trades = account.get_trades()
            r = {
                "label": label, "extras": {k: v for k, v in params.items() if k not in BASE_PARAMS},
                "final_nav": final_nav, "return_pct": (final_nav / INITIAL_CAPITAL - 1) * 100,
                "max_drawdown_pct": max_drawdown(nav_history) if nav_history else 0,
                "n_trades": len(trades),
                "n_buys": sum(1 for t in trades if t.action == "BUY"),
                "n_sells": sum(1 for t in trades if t.action == "SELL"),
                "distinct_stocks": len({t.ts_code for t in trades}),
                "highvol_sells": sum(1 for t in trades if t.action == "SELL"
                                      and "高位放量" in (t.signal_reason or "")),
                "elapsed_sec": 0,
            }
            account.close()
            results.append(r)
            print(f"  → {label}: ret={r['return_pct']:+.2f}% MDD={r['max_drawdown_pct']:.2f}% "
                  f"(reused)")
            continue
        try:
            r = run_variant(label, params, universe)
            results.append(r)
            print(f"\n  → {label}: ret={r['return_pct']:+.2f}% MDD={r['max_drawdown_pct']:.2f}% "
                  f"trades={r['n_trades']} highvol={r['highvol_sells']} "
                  f"({r['elapsed_sec']/60:.1f}min)", flush=True)
        except Exception as e:
            import traceback
            print(f"\n  ✗ {label} FAILED: {e}", flush=True)
            traceback.print_exc()

    # ── Final comparison table ──
    print("\n\n" + "=" * 100, flush=True)
    print(f"  FULL 127-DAY BACKTEST — {START} → {END}", flush=True)
    print("=" * 100, flush=True)
    print(f"{'Variant':<22} {'VolWt':>5} {'VRisk':>5} {'EFilter':>7} {'TechW':>6} "
          f"{'Return%':>9} {'MaxDD%':>8} {'Trades':>7} {'HighVol':>8}", flush=True)
    print("-" * 100, flush=True)
    for r in results:
        e = r.get("extras", {})
        print(f"{r['label']:<22} {e.get('volume_weight', 0):>5.2f} "
              f"{str(e.get('enable_volume_risk', False)):>5} "
              f"{str(e.get('enable_event_filter', False)):>7} "
              f"{e.get('tech_weight', 0):>6.3f} "
              f"{r['return_pct']:>+8.2f}% {r['max_drawdown_pct']:>7.2f}% "
              f"{r['n_trades']:>7} {r['highvol_sells']:>8}", flush=True)
    print("=" * 100, flush=True)

    # Deltas
    if len(results) >= 2:
        v1 = results[0]
        print(f"\n  Cumulative Δ (vs V1 baseline):", flush=True)
        for r in results[1:]:
            d_ret = r["return_pct"] - v1["return_pct"]
            d_mdd = r["max_drawdown_pct"] - v1["max_drawdown_pct"]
            print(f"    {r['label']:<20}: Δret={d_ret:+.2f}pp  ΔMDD={d_mdd:+.2f}pp",
                  flush=True)

    # Save
    with open("logs/full_backtest_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nDetailed results saved to logs/full_backtest_results.json", flush=True)


if __name__ == "__main__":
    main()
