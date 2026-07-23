"""A/B test: HMM market regime vs old rule-based regime.

Two variants on same universe + window:
  R0_old_regime: _get_market_regime_rulebased (5d/20d returns + MA5/MA20 + iVIX)
  R1_hmm_regime: HMM 3-state + MA confirmation (current default)
  R2_hmm_vol:    R1 + market vol regime position adjustment

Each ~50 min. Total ~2.5 hours.
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
import davis_analyzer.paper_trading.executor as exec_mod

init_database()

START = "20260105"
END = "20260715"
INITIAL_CAPITAL = 1_000_000
UNIVERSE_SIZE = 200
SCORING_FREQUENCY = 3

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


def run_variant(label, params, universe, use_old_regime=False):
    print(f"\n{'='*70}", flush=True)
    print(f"  {label}: old_regime={use_old_regime}", flush=True)
    print(f"{'='*70}", flush=True)

    # Monkey-patch regime function if needed
    original_regime = exec_mod._get_market_regime
    if use_old_regime:
        exec_mod._get_market_regime = exec_mod._get_market_regime_rulebased

    account = reset_account(f"rf_{label}", params)
    strategy = FactorThresholdStrategy(**params)
    t0 = time.time()
    results = run_backfill_auto(account, strategy, START, END, universe_codes=universe, scoring_frequency=SCORING_FREQUENCY)
    elapsed = time.time() - t0

    # Restore
    exec_mod._get_market_regime = original_regime

    nav_rows = account.get_nav_history()
    nav_history = [r.total_equity for r in nav_rows] if nav_rows else []
    final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
    ret = (final_nav / INITIAL_CAPITAL - 1) * 100
    mdd = max_drawdown(nav_history) if nav_history else 0
    trades = account.get_trades()
    account.close()
    sharpe = ret / mdd if mdd > 0.01 else 0
    return {
        "label": label, "final_nav": final_nav, "return_pct": ret,
        "max_drawdown_pct": mdd, "sharpe": sharpe,
        "n_trades": len(trades), "distinct_stocks": len({t.ts_code for t in trades}),
        "elapsed_sec": elapsed,
    }


def main():
    print(f"\nBuilding universe (top {UNIVERSE_SIZE})...")
    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe: {len(universe)} stocks\n")

    results = []

    # Reuse sell30 from fine_params if available
    try:
        with sqlite3.connect(DB_PATH) as c:
            row = c.execute("SELECT COUNT(*) FROM paper_nav_history n JOIN paper_accounts a ON n.account_id=a.id WHERE a.name='fp_sell30'").fetchone()
            if row[0] >= 120:
                print("  [reuse] sell30 from fine_params (fp_sell30)")
                account = PaperAccount.load("fp_sell30")
                nav_rows = account.get_nav_history()
                nav_history = [r.total_equity for r in nav_rows] if nav_rows else []
                trades = account.get_trades()
                final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
                ret = (final_nav / INITIAL_CAPITAL - 1) * 100
                mdd = max_drawdown(nav_history) if nav_history else 0
                results.append({"label": "R0_sell30_old_regime (reused)", "final_nav": final_nav, "return_pct": ret,
                    "max_drawdown_pct": mdd, "sharpe": ret/mdd if mdd > 0.01 else 0,
                    "n_trades": len(trades), "distinct_stocks": len({t.ts_code for t in trades}), "elapsed_sec": 0})
                account.close()
    except Exception:
        pass

    reused = any("reused" in r["label"] for r in results)

    # R0: old regime (if not reused, run it)
    if not reused:
        results.append(run_variant("R0_old_regime", BASE, universe, use_old_regime=True))

    # R1: HMM regime (current default — already includes vol_mult)
    results.append(run_variant("R1_hmm_regime", BASE, universe, use_old_regime=False))

    # Final table
    valid = sorted(results, key=lambda x: x["sharpe"], reverse=True)
    print("\n\n" + "=" * 95, flush=True)
    print(f"  REGIME A/B — {START} → {END}", flush=True)
    print("=" * 95, flush=True)
    print(f"{'Rank':<5} {'Label':<35} {'Return%':>9} {'MaxDD%':>8} {'Sharpe':>8} {'Trades':>7}", flush=True)
    print("-" * 95, flush=True)
    for rank, r in enumerate(valid, 1):
        print(f"#{rank:<3} {r['label']:<35} {r['return_pct']:>+8.2f}% {r['max_drawdown_pct']:>7.2f}% "
              f"{r['sharpe']:>+8.3f} {r['n_trades']:>7}", flush=True)
    print("=" * 95, flush=True)

    with open("logs/regime_abx_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)


if __name__ == "__main__":
    main()
