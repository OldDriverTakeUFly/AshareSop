"""A/B: Amihud liquidity factor weight sweep.

  L0_no_amihud (reference) — amihud_weight=0 (current default)
  L1_amihud_05             — amihud_weight=0.05
  L2_amihud_10             — amihud_weight=0.10
  L3_amihud_15             — amihud_weight=0.15
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
START = "20260105"; END = "20260721"; INITIAL_CAPITAL = 1_000_000; UNIVERSE_SIZE = 200; SCORING_FREQUENCY = 3
BASE = dict(max_positions=5, risk_stop_multiplier=0.70, sell_momentum=30,
    volume_weight=0.05, enable_volume_risk=True, pe_exemption_for_volume=True,
    enable_event_filter=False, event_penalty_weight=0.0, tech_weight=0.0,
    low_vol_stop_exemption=0.0, enable_adaptive_sell=False, enable_dynamic_weight=False,
    buy_momentum=65, buy_holder_min=35, buy_dividend_min=55,
    buy_forecast_min=70, buy_prosperity_min=45, min_secondary_dims=1)
VARIANTS = [
    ("L0_no_amihud", {**BASE, "amihud_weight": 0.0}),
    ("L1_amihud_05", {**BASE, "amihud_weight": 0.05}),
    ("L2_amihud_10", {**BASE, "amihud_weight": 0.10}),
    ("L3_amihud_15", {**BASE, "amihud_weight": 0.15}),
]
def build_universe(top_n):
    with get_market_conn() as c:
        ref_row = c.execute("SELECT MAX(trade_date) FROM daily_price WHERE trade_date < ?", (START,)).fetchone()
        ref_end = ref_row[0] if ref_row and ref_row[0] else "20251231"
        rows = c.execute("SELECT a.ts_code FROM daily_price a JOIN daily_price b ON a.ts_code=b.ts_code AND b.trade_date = (SELECT MAX(trade_date) FROM daily_price WHERE ts_code=a.ts_code AND trade_date <= '20251001') WHERE a.trade_date = ? AND a.close > 0 AND b.close > 0 AND a.vol > 0 ORDER BY (a.close / b.close - 1) DESC LIMIT ?", (ref_end, top_n)).fetchall()
    return [r[0] for r in rows]
def reset_account(name, config):
    from davis_analyzer.paper_trading.runlock import delete_account_if_idle

    delete_account_if_idle(name)
    return PaperAccount.create(name=name, strategy_name="factor_threshold", initial_capital=INITIAL_CAPITAL, config=config)
def max_dd(nav):
    peak = nav[0] if nav else 0; mdd = 0
    for v in nav:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > mdd: mdd = dd
    return mdd
def run_variant(label, params, universe):
    aw = params.get("amihud_weight", 0)
    print(f"\n{'='*70}\n  {label}: amihud_weight={aw}\n{'='*70}", flush=True)
    account = reset_account(f"am_{label}", params)
    strategy = FactorThresholdStrategy(**params)
    t0 = time.time()
    run_backfill_auto(account, strategy, START, END, universe_codes=universe, scoring_frequency=SCORING_FREQUENCY)
    elapsed = time.time() - t0
    nav_rows = account.get_nav_history()
    nav = [r.total_equity for r in nav_rows]
    ret = (nav[-1]/INITIAL_CAPITAL-1)*100 if nav else 0
    mdd = max_dd(nav)
    trades = account.get_trades()
    account.close()
    return {"label": label, "amihud_weight": aw, "return_pct": ret, "max_drawdown_pct": mdd,
            "sharpe": ret/mdd if mdd>0.01 else 0, "n_trades": len(trades),
            "distinct_stocks": len({t.ts_code for t in trades}), "elapsed_sec": elapsed}
def main():
    print(f"\nBuilding universe (top {UNIVERSE_SIZE})...")
    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe: {len(universe)} stocks\n")
    results = []
    # Reuse L0 from production_final if available
    try:
        with sqlite3.connect(DB_PATH) as c:
            row = c.execute("SELECT COUNT(*) FROM paper_nav_history n JOIN paper_accounts a ON n.account_id=a.id WHERE a.name='production_final' AND n.trade_date >= '20260716'").fetchone()
            if row[0] >= 5:
                account = PaperAccount.load("production_final")
                nav_rows = account.get_nav_history()
                nav = [r.total_equity for r in nav_rows]
                trades = account.get_trades()
                ret = (nav[-1]/INITIAL_CAPITAL-1)*100 if nav else 0
                mdd = max_dd(nav)
                results.append({"label": "L0_no_amihud (reused)", "amihud_weight": 0.0,
                    "return_pct": ret, "max_drawdown_pct": mdd, "sharpe": ret/mdd if mdd>0.01 else 0,
                    "n_trades": len(trades), "distinct_stocks": len({t.ts_code for t in trades}), "elapsed_sec": 0})
                account.close()
                print("  [reuse] L0 from production_final")
    except: pass
    reused = any("reused" in r["label"] for r in results)
    for label, params in VARIANTS:
        if reused and label == "L0_no_amihud": continue
        r = run_variant(label, params, universe)
        results.append(r)
        print(f"\n  → {label}: ret={r['return_pct']:+.2f}% MDD={r['max_drawdown_pct']:.2f}% Sharpe={r['sharpe']:+.3f} trades={r['n_trades']} ({r['elapsed_sec']/60:.1f}min)", flush=True)
    valid = sorted(results, key=lambda x: -x["sharpe"])
    print("\n\n" + "="*95)
    print(f"  AMIHUD LIQUIDITY A/B — {START} → {END}")
    print("="*95)
    print(f"{'Variant':<22} {'AmihudWt':>8} {'Return%':>9} {'MaxDD%':>8} {'Sharpe':>8} {'Trades':>7}")
    print("-"*95)
    for r in valid:
        marker = " 🏆" if r == valid[0] else ""
        print(f"{r['label']:<22} {r['amihud_weight']:>8.2f} {r['return_pct']:>+8.2f}% {r['max_drawdown_pct']:>7.2f}% {r['sharpe']:>+8.3f} {r['n_trades']:>7}{marker}")
    print("="*95)
    with open("logs/amihud_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
if __name__ == "__main__":
    main()
