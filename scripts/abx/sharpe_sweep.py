"""Sharpe-optimization sweep: scan stop-loss multiplier × max_positions.

Building on V2 (current best: -3.35% / 17.11% = -0.20 Sharpe), tests
combinations of:
  - risk_stop_multiplier: 0.7, 0.85, 1.0 (baseline), 1.15
  - max_positions: 5, 8, 10 (baseline), 12

Goal: maximize Sharpe = return% / maxDD% (proxy for risk-adjusted return).

12 combos total. V2 is reused as the (0.85, 10) baseline.
Each combo ~45 min, total ~9 hours (background).
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

# V2 base (current production config)
BASE_PARAMS = dict(
    buy_momentum=65, sell_momentum=40,
    buy_holder_min=35, buy_dividend_min=55,
    buy_forecast_min=70, buy_prosperity_min=45,
    min_secondary_dims=1,
    volume_weight=0.05, enable_volume_risk=True,
    enable_event_filter=False, event_penalty_weight=0.0,
    tech_weight=0.0,
    # These two are swept:
    # max_positions=10, risk_stop_multiplier=1.0,
)

# 12 combos (4 stop multipliers × 3 position counts)
STOP_MULTIPLIERS = [0.7, 0.85, 1.0, 1.15]
MAX_POSITIONS_LIST = [5, 10, 12]


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


def sharpe_proxy(return_pct: float, max_dd_pct: float) -> float:
    """Crude Sharpe proxy: return / maxDD. Higher is better."""
    if max_dd_pct <= 0.01:
        return 0.0
    return return_pct / max_dd_pct


def run_combo(label: str, params: dict, universe: list[str]) -> dict:
    print(f"\n{'='*70}", flush=True)
    print(f"  {label}: stop_mult={params['risk_stop_multiplier']} "
          f"max_pos={params['max_positions']}", flush=True)
    print(f"{'='*70}", flush=True)
    account = reset_account(f"sh_{label}", params)
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
    highvol_sells = sum(1 for t in trades if t.action == "SELL"
                        and "高位放量" in (t.signal_reason or ""))
    account.close()
    return {
        "label": label,
        "risk_stop_multiplier": params["risk_stop_multiplier"],
        "max_positions": params["max_positions"],
        "final_nav": final_nav, "return_pct": total_ret,
        "max_drawdown_pct": mdd,
        "sharpe": sharpe_proxy(total_ret, mdd),
        "n_trades": len(trades),
        "highvol_sells": highvol_sells,
        "elapsed_sec": elapsed,
    }


def main():
    print(f"\nBuilding universe (top {UNIVERSE_SIZE} by 90d return)...")
    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe: {len(universe)} stocks")
    print(f"  Combos: {len(STOP_MULTIPLIERS)} stops × {len(MAX_POSITIONS_LIST)} pos = "
          f"{len(STOP_MULTIPLIERS) * len(MAX_POSITIONS_LIST)}\n")

    # Reuse V2 baseline if available (stop=1.0, pos=10 = the production config)
    results = []
    try:
        with sqlite3.connect(DB_PATH) as c:
            row = c.execute(
                "SELECT COUNT(*) FROM paper_nav_history n "
                "JOIN paper_accounts a ON n.account_id=a.id "
                "WHERE a.name='abx_V2_volume'"
            ).fetchone()
            if row[0] >= 120:
                print("  [reuse] V2 from full_backtest (abx_V2_volume)")
                account = PaperAccount.load("abx_V2_volume")
                nav_rows = account.get_nav_history()
                nav_history = [r.total_equity for r in nav_rows] if nav_rows else []
                trades = account.get_trades()
                final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
                ret = (final_nav / INITIAL_CAPITAL - 1) * 100
                mdd = max_drawdown(nav_history) if nav_history else 0
                results.append({
                    "label": "V2_baseline (reused)",
                    "risk_stop_multiplier": 1.0, "max_positions": 10,
                    "final_nav": final_nav, "return_pct": ret,
                    "max_drawdown_pct": mdd, "sharpe": sharpe_proxy(ret, mdd),
                    "n_trades": len(trades),
                    "highvol_sells": sum(1 for t in trades if t.action == "SELL"
                                          and "高位放量" in (t.signal_reason or "")),
                    "elapsed_sec": 0,
                })
                account.close()
    except Exception:
        pass

    # Run all 12 combos (skip V2's exact config if reused)
    v2_reused = any(r["label"].startswith("V2_baseline") for r in results)
    for stop_mult in STOP_MULTIPLIERS:
        for max_pos in MAX_POSITIONS_LIST:
            # Skip V2's exact config if already reused
            if v2_reused and abs(stop_mult - 1.0) < 0.01 and max_pos == 10:
                continue
            label = f"stop{stop_mult:.2f}_pos{max_pos}"
            params = {**BASE_PARAMS,
                      "risk_stop_multiplier": stop_mult,
                      "max_positions": max_pos}
            try:
                r = run_combo(label, params, universe)
                results.append(r)
                print(f"\n  → {label}: ret={r['return_pct']:+.2f}% "
                      f"MDD={r['max_drawdown_pct']:.2f}% Sharpe={r['sharpe']:+.3f} "
                      f"trades={r['n_trades']} ({r['elapsed_sec']/60:.1f}min)", flush=True)
            except Exception as e:
                import traceback
                print(f"\n  ✗ {label} FAILED: {e}", flush=True)
                traceback.print_exc()

    # ── Final table sorted by Sharpe ──
    valid = sorted([r for r in results if "error" not in r],
                   key=lambda x: x["sharpe"], reverse=True)
    print("\n\n" + "=" * 100, flush=True)
    print(f"  SHARPE-OPTIMIZATION SWEEP — {START} → {END}", flush=True)
    print("=" * 100, flush=True)
    print(f"{'Rank':<5} {'Label':<28} {'StopMult':>8} {'MaxPos':>7} "
          f"{'Return%':>9} {'MaxDD%':>8} {'Sharpe':>8} {'Trades':>7}", flush=True)
    print("-" * 100, flush=True)
    for rank, r in enumerate(valid, 1):
        print(f"#{rank:<3} {r['label']:<28} {r['risk_stop_multiplier']:>8.2f} "
              f"{r['max_positions']:>7} {r['return_pct']:>+8.2f}% "
              f"{r['max_drawdown_pct']:>7.2f}% {r['sharpe']:>+8.3f} "
              f"{r['n_trades']:>7}", flush=True)
    print("=" * 100, flush=True)

    # Compare best vs V2
    if len(valid) >= 2:
        v2 = next((r for r in valid if "V2_baseline" in r["label"]
                   or (abs(r["risk_stop_multiplier"] - 1.0) < 0.01 and r["max_positions"] == 10)), valid[0])
        best = valid[0]
        if best["label"] != v2["label"]:
            print(f"\n  🏆 BEST: {best['label']}", flush=True)
            print(f"     Sharpe: {best['sharpe']:+.3f} vs V2 {v2['sharpe']:+.3f} "
                  f"(Δ={best['sharpe'] - v2['sharpe']:+.3f})", flush=True)
            print(f"     Return: {best['return_pct']:+.2f}% vs V2 {v2['return_pct']:+.2f}% "
                  f"(Δ={best['return_pct'] - v2['return_pct']:+.2f}pp)", flush=True)
            print(f"     MaxDD:  {best['max_drawdown_pct']:.2f}% vs V2 {v2['max_drawdown_pct']:.2f}% "
                  f"(Δ={best['max_drawdown_pct'] - v2['max_drawdown_pct']:+.2f}pp)", flush=True)

    with open("logs/sharpe_sweep_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nSaved to logs/sharpe_sweep_results.json", flush=True)


if __name__ == "__main__":
    main()
