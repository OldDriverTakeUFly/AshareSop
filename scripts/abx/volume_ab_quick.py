"""Quick A/B test: shorter window (1 month) for fast feedback.

Same logic as volume_ab_test.py but only runs 1 month (20260601 → 20260630)
to get a quick directional read while the full A/B completes in background.
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

# Shorter window: just June 2026 (volatile month with semis rally)
START = "20260601"
END = "20260630"
INITIAL_CAPITAL = 1_000_000
UNIVERSE_SIZE = 100  # smaller universe for speed

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
    with get_market_conn() as c:
        REF_END_ROW = c.execute(
            "SELECT MAX(trade_date) FROM daily_price WHERE trade_date < ?", (START,)
        ).fetchone()
        REF_END = REF_END_ROW[0] if REF_END_ROW and REF_END_ROW[0] else "20260529"
        REF_START_BOUND = "20260301"
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
    account = reset_account(f"abq_{label}", params)
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
    distinct_stocks = len({t["ts_code"] for t in trades}) if trades else 0
    highvol_sells = sum(
        1 for t in trades
        if t.get("action") == "SELL" and "高位放量" in (t.get("reason") or "")
    )
    account.close()
    return {
        "label": label, "volume_weight": volume_weight,
        "enable_volume_risk": enable_volume_risk,
        "final_nav": final_nav, "return_pct": total_ret,
        "max_drawdown_pct": mdd, "n_trades": n_trades,
        "distinct_stocks": distinct_stocks, "highvol_sells": highvol_sells,
        "elapsed_sec": elapsed,
    }


def main():
    print(f"\nBuilding universe (top {UNIVERSE_SIZE} by 90d return as of {START})...")
    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe size: {len(universe)} stocks\n")

    results = []
    results.append(run_variant("A_legacy", 0.0, False, universe))
    results.append(run_variant("B_volume", 0.10, True, universe))

    print("\n\n" + "=" * 80, flush=True)
    print(f"  QUICK A/B — {START} → {END} (universe={UNIVERSE_SIZE})", flush=True)
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

    if len(results) == 2:
        a, b = results
        delta_ret = b["return_pct"] - a["return_pct"]
        print(f"\nΔ (B - A):", flush=True)
        print(f"  Return:      {delta_ret:+.2f} pp", flush=True)
        print(f"  HighVolSells in B: {b['highvol_sells']}", flush=True)


if __name__ == "__main__":
    main()
