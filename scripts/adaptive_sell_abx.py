"""A/B: adaptive sell threshold vs fixed sell_momentum=30.

  A0_fixed30 (reference) — sell_momentum=30 fixed (current default)
  A1_adaptive            — enable_adaptive_sell=True (bull=25, neutral=30, bear=35)
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

START = "20260105"; END = "20260721"
INITIAL_CAPITAL = 1_000_000; UNIVERSE_SIZE = 200; SCORING_FREQUENCY = 3

BASE = dict(
    max_positions=5, risk_stop_multiplier=0.70, sell_momentum=30,
    volume_weight=0.05, enable_volume_risk=True, pe_exemption_for_volume=True,
    enable_event_filter=False, event_penalty_weight=0.0, tech_weight=0.0,
    low_vol_stop_exemption=0.0,
    buy_momentum=65, buy_holder_min=35,
    buy_dividend_min=55, buy_forecast_min=70, buy_prosperity_min=45,
    min_secondary_dims=1,
)

VARIANTS = [
    ("A0_fixed30", {**BASE, "enable_adaptive_sell": False}),
    ("A1_adaptive", {**BASE, "enable_adaptive_sell": True}),
]


def build_universe(top_n):
    with get_market_conn() as c:
        ref_row = c.execute("SELECT MAX(trade_date) FROM daily_price WHERE trade_date < ?", (START,)).fetchone()
        ref_end = ref_row[0] if ref_row and ref_row[0] else "20251231"
        rows = c.execute("""
            SELECT a.ts_code FROM daily_price a JOIN daily_price b ON a.ts_code=b.ts_code
              AND b.trade_date = (SELECT MAX(trade_date) FROM daily_price WHERE ts_code=a.ts_code AND trade_date <= '20251001')
            WHERE a.trade_date = ? AND a.close > 0 AND b.close > 0 AND a.vol > 0
            ORDER BY (a.close / b.close - 1) DESC LIMIT ?
        """, (ref_end, top_n)).fetchall()
    return [r[0] for r in rows]


def reset_account(name, config):
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute("SELECT id FROM paper_accounts WHERE name=?", (name,)).fetchone()
        if row:
            aid = row[0]
            for tbl in ("paper_positions", "paper_trades", "paper_nav_history", "paper_shadow_trades"):
                c.execute(f"DELETE FROM {tbl} WHERE account_id=?", (aid,))
            c.execute("DELETE FROM paper_accounts WHERE id=?", (aid,))
            c.commit()
    return PaperAccount.create(name=name, strategy_name="factor_threshold", initial_capital=INITIAL_CAPITAL, config=config)


def max_dd(nav):
    peak = nav[0] if nav else 0; mdd = 0
    for v in nav:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > mdd: mdd = dd
    return mdd


def run_variant(label, params, universe):
    print(f"\n{'='*70}", flush=True)
    print(f"  {label}: adaptive={params.get('enable_adaptive_sell', False)}", flush=True)
    print(f"{'='*70}", flush=True)
    account = reset_account(f"as_{label}", params)
    strategy = FactorThresholdStrategy(**params)
    t0 = time.time()
    results = run_backfill_auto(account, strategy, START, END, universe_codes=universe, scoring_frequency=SCORING_FREQUENCY)
    elapsed = time.time() - t0
    nav_rows = account.get_nav_history()
    nav_history = [r.total_equity for r in nav_rows] if nav_rows else []
    final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
    ret = (final_nav / INITIAL_CAPITAL - 1) * 100
    mdd = max_dd(nav_history) if nav_history else 0
    trades = account.get_trades()
    account.close()
    return {"label": label, "return_pct": ret, "max_drawdown_pct": mdd,
            "sharpe": ret/mdd if mdd > 0.01 else 0, "n_trades": len(trades),
            "distinct_stocks": len({t.ts_code for t in trades}), "elapsed_sec": elapsed}


def main():
    print(f"\nBuilding universe (top {UNIVERSE_SIZE})...")
    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe: {len(universe)} stocks\n")

    results = []
    for label, params in VARIANTS:
        r = run_variant(label, params, universe)
        results.append(r)
        print(f"\n  → {label}: ret={r['return_pct']:+.2f}% MDD={r['max_drawdown_pct']:.2f}% "
              f"Sharpe={r['sharpe']:+.3f} trades={r['n_trades']} ({r['elapsed_sec']/60:.1f}min)", flush=True)

    print("\n\n" + "=" * 90, flush=True)
    print(f"  ADAPTIVE SELL A/B — {START} → {END}", flush=True)
    print("=" * 90, flush=True)
    print(f"{'Variant':<20} {'Return%':>9} {'MaxDD%':>8} {'Sharpe':>8} {'Trades':>7}", flush=True)
    print("-" * 90, flush=True)
    for r in sorted(results, key=lambda x: -x["sharpe"]):
        print(f"{r['label']:<20} {r['return_pct']:>+8.2f}% {r['max_drawdown_pct']:>7.2f}% "
              f"{r['sharpe']:>+8.3f} {r['n_trades']:>7}", flush=True)
    print("=" * 90, flush=True)

    with open("logs/adaptive_sell_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)


if __name__ == "__main__":
    main()
