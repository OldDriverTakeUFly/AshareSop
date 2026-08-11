"""A/B test: volume-price signals ON vs OFF.

Runs the same strategy + universe twice:
  A. volume_weight=0.0  (legacy, no volume-price influence)
  B. volume_weight=0.10 (new, with volume-price composite + high-vol risk sell)

Both use identical baseline params and the same top-200 universe (by 90-day
return as of day-before-START). Outputs a comparison table.
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

# Baseline strategy params (shared by both A and B)
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


def build_universe(top_n: int) -> list[str]:
    """Top-N stocks by trailing 90-day return as of the day before START."""
    with get_market_conn() as c:
        REF_END_ROW = c.execute(
            "SELECT MAX(trade_date) FROM daily_price WHERE trade_date < ?", (START,)
        ).fetchone()
        REF_END = REF_END_ROW[0] if REF_END_ROW and REF_END_ROW[0] else "20251231"
        REF_START_BOUND = "20251001"
        rows = c.execute("""
            SELECT a.ts_code, a.close AS p_end, b.close AS p_start
            FROM daily_price a
            JOIN daily_price b
              ON a.ts_code = b.ts_code
             AND b.trade_date = (
                SELECT MAX(trade_date) FROM daily_price
                WHERE ts_code = a.ts_code AND trade_date <= ?
             )
            WHERE a.trade_date = ?
              AND a.close > 0 AND b.close > 0
            ORDER BY (a.close / b.close - 1) DESC
            LIMIT ?
        """, (REF_START_BOUND, REF_END, top_n)).fetchall()
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


def run_variant(label: str, volume_weight: float, enable_volume_risk: bool, universe: list[str]) -> dict:
    print(f"\n{'='*70}", flush=True)
    print(f"  Variant {label}: volume_weight={volume_weight} enable_volume_risk={enable_volume_risk}", flush=True)
    print(f"{'='*70}", flush=True)

    params = dict(BASE_PARAMS)
    params["volume_weight"] = volume_weight
    params["enable_volume_risk"] = enable_volume_risk

    account = reset_account(f"abtest_{label}", params)
    strategy = FactorThresholdStrategy(**params)

    t0 = time.time()
    results = run_backfill_auto(
        account, strategy, START, END,
        universe_codes=universe, scoring_frequency=3,
    )
    elapsed = time.time() - t0

    nav_history = [r.get("nav", INITIAL_CAPITAL) for r in results if r.get("status") == "ok"]
    final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
    total_ret = (final_nav / INITIAL_CAPITAL - 1) * 100
    mdd = max_drawdown(nav_history) if nav_history else 0

    trades = account.get_trades()
    n_trades = len(trades)
    distinct_stocks = len({t.ts_code for t in trades}) if trades else 0

    # Count risk sells specifically triggered by 高位放量
    highvol_sells = sum(
        1 for t in trades
        if t.action == "SELL" and "高位放量" in (t.signal_reason or "")
    )

    account.close()

    return {
        "label": label,
        "volume_weight": volume_weight,
        "enable_volume_risk": enable_volume_risk,
        "final_nav": final_nav, "return_pct": total_ret,
        "max_drawdown_pct": mdd, "n_trades": n_trades,
        "distinct_stocks": distinct_stocks,
        "highvol_sells": highvol_sells,
        "elapsed_sec": elapsed,
    }


def main():
    print(f"\nBuilding universe (top {UNIVERSE_SIZE} by 90d return)...")
    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe size: {len(universe)} stocks")

    results = []
    # A: Legacy (volume fully OFF — both composite weight and risk sell)
    # If abtest_A_legacy already exists with data, skip re-running it (saves ~1hr).
    # Pass --rerun-a to force re-run.
    rerun_a = "--rerun-a" in sys.argv
    a_exists = False
    try:
        with sqlite3.connect(DB_PATH) as c:
            row = c.execute(
                "SELECT COUNT(*) FROM paper_trades t "
                "JOIN paper_accounts a ON t.account_id=a.id "
                "WHERE a.name='abtest_A_legacy'"
            ).fetchone()
            a_exists = row[0] > 0 if row else False
    except Exception:
        pass

    if a_exists and not rerun_a:
        print(f"\n  [skip] abtest_A_legacy already has data; reuse (pass --rerun-a to force)")
        # Reuse the existing account, just compute summary
        account = PaperAccount.load("abtest_A_legacy")
        nav_rows = account.get_nav_history()
        nav_history = [r.total_equity for r in nav_rows] if nav_rows else []
        final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
        total_ret = (final_nav / INITIAL_CAPITAL - 1) * 100
        mdd = max_drawdown(nav_history) if nav_history else 0
        trades = account.get_trades()
        highvol_sells = sum(1 for t in trades if t.action == "SELL" and "高位放量" in (t.signal_reason or ""))
        results.append({
            "label": "A_legacy", "volume_weight": 0.0,
            "enable_volume_risk": False,
            "final_nav": final_nav, "return_pct": total_ret,
            "max_drawdown_pct": mdd, "n_trades": len(trades),
            "distinct_stocks": len({t.ts_code for t in trades}),
            "highvol_sells": highvol_sells, "elapsed_sec": 0,
        })
        account.close()
    else:
        results.append(run_variant("A_legacy", volume_weight=0.0, enable_volume_risk=False, universe=universe))

    # B: With volume-price (both composite weight + risk sell)
    results.append(run_variant("B_volume", volume_weight=0.10, enable_volume_risk=True, universe=universe))

    # ── Comparison table ──
    print("\n\n" + "=" * 80, flush=True)
    print(f"  VOLUME-PRICE A/B TEST — {START} → {END}", flush=True)
    print("=" * 80, flush=True)
    print(f"{'Variant':<12} {'VolWt':>6} {'VRisk':>6} {'Return%':>9} {'MaxDD%':>8} "
          f"{'Trades':>7} {'Stocks':>7} {'HighVolSells':>13}", flush=True)
    print("-" * 80, flush=True)
    for r in results:
        print(f"{r['label']:<12} {r['volume_weight']:>6.2f} {str(r['enable_volume_risk']):>6} "
              f"{r['return_pct']:>+8.2f}% {r['max_drawdown_pct']:>7.2f}% "
              f"{r['n_trades']:>7} {r['distinct_stocks']:>7} "
              f"{r['highvol_sells']:>13}", flush=True)
    print("=" * 80, flush=True)

    # Delta
    if len(results) == 2:
        a, b = results
        delta_ret = b["return_pct"] - a["return_pct"]
        delta_mdd = b["max_drawdown_pct"] - a["max_drawdown_pct"]
        delta_stocks = b["distinct_stocks"] - a["distinct_stocks"]
        print(f"\nΔ (B - A):", flush=True)
        print(f"  Return:      {delta_ret:+.2f} pp", flush=True)
        print(f"  MaxDD:       {delta_mdd:+.2f} pp", flush=True)
        print(f"  # stocks:    {delta_stocks:+d}", flush=True)
        print(f"  HighVolSells in B: {b['highvol_sells']}", flush=True)

    with open("/tmp/volume_ab_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    # Also save to project logs/
    with open(os.path.join(PROJECT_ROOT, "logs/volume_ab_results.json"), "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nDetailed results saved to logs/volume_ab_results.json", flush=True)


if __name__ == "__main__":
    main()
