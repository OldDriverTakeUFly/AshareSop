"""A/B: multi-index HMM (SH+CYB) vs single-index HMM (SH only).

  H0_single (reference) — single SH index HMM (previous version)
  H1_multi              — dual SH+CYB index HMM (current upgrade)
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
import davis_analyzer.market_regime as mr
import davis_analyzer.paper_trading.executor as exec_mod

init_database()

START = "20260105"; END = "20260721"
INITIAL_CAPITAL = 1_000_000; UNIVERSE_SIZE = 200; SCORING_FREQUENCY = 3

BASE = dict(
    max_positions=5, risk_stop_multiplier=0.70, sell_momentum=30,
    volume_weight=0.05, enable_volume_risk=True, pe_exemption_for_volume=True,
    enable_event_filter=False, event_penalty_weight=0.0, tech_weight=0.0,
    low_vol_stop_exemption=0.0, enable_adaptive_sell=False,
    buy_momentum=65, buy_holder_min=35,
    buy_dividend_min=55, buy_forecast_min=70, buy_prosperity_min=45,
    min_secondary_dims=1,
)


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


def run_single_index_hmm(universe):
    """Patch market_regime to use single-index, run backtest."""
    # Save original
    mr.reset_hmm_cache()
    original_ensure = mr._ensure_model_trained

    # Monkey-patch: force single-index
    def single_index_train(end_date="20260723"):
        import pandas as pd, numpy as np
        if mr._hmm_model is not None and mr._hmm_train_end_date >= end_date:
            return
        df = mr._load_index_returns("000001.SH", "20210101", end_date)
        if len(df) < 100: return
        features = df["ret"].values
        mr._hmm_model, mr._hmm_state_labels = mr._train_hmm(features, n_states=3)
        mr._hmm_train_end_date = end_date
        predictions = mr._hmm_model.predict(features.reshape(-1, 1))
        mr._hmm_predictions = {df["trade_date"].iloc[i]: mr._hmm_state_labels.get(int(p), "neutral")
                               for i, p in enumerate(predictions)}

    mr._ensure_model_trained = single_index_train

    account = reset_account("hmm_h0_single", BASE)
    strategy = FactorThresholdStrategy(**BASE)
    t0 = time.time()
    run_backfill_auto(account, strategy, START, END, universe_codes=universe, scoring_frequency=SCORING_FREQUENCY)
    elapsed = time.time() - t0
    nav_rows = account.get_nav_history()
    nav = [r.total_equity for r in nav_rows]
    ret = (nav[-1]/INITIAL_CAPITAL-1)*100 if nav else 0
    mdd = max_dd(nav)
    trades = account.get_trades()
    account.close()

    # Restore
    mr._ensure_model_trained = original_ensure
    mr.reset_hmm_cache()

    return {"label": "H0_single_index", "return_pct": ret, "max_drawdown_pct": mdd,
            "sharpe": ret/mdd if mdd>0.01 else 0, "n_trades": len(trades),
            "distinct_stocks": len({t.ts_code for t in trades}), "elapsed_sec": elapsed}


def run_multi_index_hmm(universe):
    """Run with multi-index HMM (current default)."""
    mr.reset_hmm_cache()
    account = reset_account("hmm_h1_multi", BASE)
    strategy = FactorThresholdStrategy(**BASE)
    t0 = time.time()
    run_backfill_auto(account, strategy, START, END, universe_codes=universe, scoring_frequency=SCORING_FREQUENCY)
    elapsed = time.time() - t0
    nav_rows = account.get_nav_history()
    nav = [r.total_equity for r in nav_rows]
    ret = (nav[-1]/INITIAL_CAPITAL-1)*100 if nav else 0
    mdd = max_dd(nav)
    trades = account.get_trades()
    account.close()
    mr.reset_hmm_cache()
    return {"label": "H1_multi_index", "return_pct": ret, "max_drawdown_pct": mdd,
            "sharpe": ret/mdd if mdd>0.01 else 0, "n_trades": len(trades),
            "distinct_stocks": len({t.ts_code for t in trades}), "elapsed_sec": elapsed}


def main():
    print(f"\nBuilding universe (top {UNIVERSE_SIZE})...")
    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe: {len(universe)} stocks\n")

    print("Running H0 (single-index HMM)...")
    r0 = run_single_index_hmm(universe)
    print(f"  → {r0['label']}: ret={r0['return_pct']:+.2f}% MDD={r0['max_drawdown_pct']:.2f}% "
          f"Sharpe={r0['sharpe']:+.3f} trades={r0['n_trades']} ({r0['elapsed_sec']/60:.1f}min)", flush=True)

    print("\nRunning H1 (multi-index HMM)...")
    r1 = run_multi_index_hmm(universe)
    print(f"  → {r1['label']}: ret={r1['return_pct']:+.2f}% MDD={r1['max_drawdown_pct']:.2f}% "
          f"Sharpe={r1['sharpe']:+.3f} trades={r1['n_trades']} ({r1['elapsed_sec']/60:.1f}min)", flush=True)

    results = sorted([r0, r1], key=lambda x: -x["sharpe"])
    print("\n\n" + "=" * 90, flush=True)
    print(f"  HMM MULTI-INDEX A/B — {START} → {END}", flush=True)
    print("=" * 90, flush=True)
    print(f"{'Variant':<22} {'Return%':>9} {'MaxDD%':>8} {'Sharpe':>8} {'Trades':>7}", flush=True)
    print("-" * 90, flush=True)
    for r in results:
        marker = " 🏆" if r == results[0] else ""
        print(f"{r['label']:<22} {r['return_pct']:>+8.2f}% {r['max_drawdown_pct']:>7.2f}% "
              f"{r['sharpe']:>+8.3f} {r['n_trades']:>7}{marker}", flush=True)
    print("=" * 90, flush=True)

    with open("logs/hmm_multiindex_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)


if __name__ == "__main__":
    main()
