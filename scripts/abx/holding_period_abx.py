"""Holding period A/B test: different scoring frequencies.

Current R1 HMM uses scoring_frequency=3 (re-score every 3 days).
Tests whether longer intervals (5, 10, 20) improve Sharpe by reducing
turnover and letting winners run longer.

Technical factor study showed 20-day holding is optimal (Q5-Q1=+1.14%).
But HMM regime switching may benefit from faster response.

Variants:
  H1_freq3 (reference)  — current default (reuse R1 from regime_abx)
  H2_freq5              — re-score every 5 days
  H3_freq10             — re-score every 10 days
  H4_freq20             — re-score every 20 days (monthly)
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

BASE = dict(
    max_positions=5, risk_stop_multiplier=0.70,
    sell_momentum=30,
    volume_weight=0.05, enable_volume_risk=True,
    pe_exemption_for_volume=True,
    enable_event_filter=False, event_penalty_weight=0.0, tech_weight=0.0,
    low_vol_stop_exemption=0.0,
    buy_momentum=65, buy_holder_min=35,
    buy_dividend_min=55, buy_forecast_min=70, buy_prosperity_min=45,
    min_secondary_dims=1,
)

VARIANTS = [
    ("H1_freq3", 3),
    ("H2_freq5", 5),
    ("H3_freq10", 10),
    ("H4_freq20", 20),
]


def build_universe(top_n):
    with get_market_conn() as c:
        ref_row = c.execute("SELECT MAX(trade_date) FROM daily_price WHERE trade_date < ?", (START,)).fetchone()
        ref_end = ref_row[0] if ref_row and ref_row[0] else "20251231"
        rows = c.execute("""
            SELECT a.ts_code FROM daily_price a
            JOIN daily_price b ON a.ts_code=b.ts_code
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


def max_drawdown(nav):
    peak = nav[0] if nav else 0
    mdd = 0.0
    for v in nav:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > mdd: mdd = dd
    return mdd


def run_variant(label, freq, universe):
    print(f"\n{'='*70}", flush=True)
    print(f"  {label}: scoring_frequency={freq}", flush=True)
    print(f"{'='*70}", flush=True)
    account = reset_account(f"hp_{label}", BASE)
    strategy = FactorThresholdStrategy(**BASE)
    t0 = time.time()
    results = run_backfill_auto(account, strategy, START, END, universe_codes=universe, scoring_frequency=freq)
    elapsed = time.time() - t0
    nav_rows = account.get_nav_history()
    nav_history = [r.total_equity for r in nav_rows] if nav_rows else []
    final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
    ret = (final_nav / INITIAL_CAPITAL - 1) * 100
    mdd = max_drawdown(nav_history) if nav_history else 0
    trades = account.get_trades()
    account.close()
    sharpe = ret / mdd if mdd > 0.01 else 0
    return {
        "label": label, "freq": freq,
        "final_nav": final_nav, "return_pct": ret,
        "max_drawdown_pct": mdd, "sharpe": sharpe,
        "n_trades": len(trades), "n_buys": sum(1 for t in trades if t.action == "BUY"),
        "distinct_stocks": len({t.ts_code for t in trades}),
        "elapsed_sec": elapsed,
    }


def main():
    print(f"\nBuilding universe (top {UNIVERSE_SIZE})...")
    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe: {len(universe)} stocks\n")

    results = []

    # Reuse R1 from regime_abx (scoring_freq=3)
    try:
        with sqlite3.connect(DB_PATH) as c:
            row = c.execute("SELECT COUNT(*) FROM paper_nav_history n JOIN paper_accounts a ON n.account_id=a.id WHERE a.name='rf_R1_hmm_regime'").fetchone()
            if row[0] >= 120:
                print("  [reuse] H1_freq3 from regime_abx (rf_R1_hmm_regime)")
                account = PaperAccount.load("rf_R1_hmm_regime")
                nav_rows = account.get_nav_history()
                nav_history = [r.total_equity for r in nav_rows] if nav_rows else []
                trades = account.get_trades()
                final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
                ret = (final_nav / INITIAL_CAPITAL - 1) * 100
                mdd = max_drawdown(nav_history) if nav_history else 0
                results.append({"label": "H1_freq3 (reused)", "freq": 3,
                    "final_nav": final_nav, "return_pct": ret, "max_drawdown_pct": mdd,
                    "sharpe": ret/mdd if mdd > 0.01 else 0, "n_trades": len(trades),
                    "n_buys": sum(1 for t in trades if t.action == "BUY"),
                    "distinct_stocks": len({t.ts_code for t in trades}), "elapsed_sec": 0})
                account.close()
    except Exception:
        pass

    reused = any("reused" in r["label"] for r in results)

    for label, freq in VARIANTS:
        if reused and label == "H1_freq3":
            continue
        try:
            r = run_variant(label, freq, universe)
            results.append(r)
            print(f"\n  → {label}: ret={r['return_pct']:+.2f}% MDD={r['max_drawdown_pct']:.2f}% "
                  f"Sharpe={r['sharpe']:+.3f} trades={r['n_trades']} ({r['elapsed_sec']/60:.1f}min)", flush=True)
        except Exception as e:
            import traceback
            print(f"\n  ✗ {label} FAILED: {e}", flush=True)
            traceback.print_exc()

    # Final table
    valid = sorted(results, key=lambda x: x["sharpe"], reverse=True)
    print("\n\n" + "=" * 100, flush=True)
    print(f"  HOLDING PERIOD A/B — {START} → {END}", flush=True)
    print("=" * 100, flush=True)
    print(f"{'Rank':<5} {'Label':<22} {'Freq':>5} {'Return%':>9} {'MaxDD%':>8} {'Sharpe':>8} {'Trades':>7} {'Stocks':>7}", flush=True)
    print("-" * 100, flush=True)
    for rank, r in enumerate(valid, 1):
        marker = " 🏆" if rank == 1 else ""
        print(f"#{rank:<3} {r['label']:<22} {r['freq']:>5} {r['return_pct']:>+8.2f}% "
              f"{r['max_drawdown_pct']:>7.2f}% {r['sharpe']:>+8.3f} {r['n_trades']:>7} "
              f"{r['distinct_stocks']:>7}{marker}", flush=True)
    print("=" * 100, flush=True)

    with open("logs/holding_period_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)


if __name__ == "__main__":
    main()
