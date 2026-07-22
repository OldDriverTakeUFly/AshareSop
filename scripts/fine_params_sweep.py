"""Fine parameter sweep on buy/sell thresholds.

Building on S1 (current best: +0.86% / 10.17% / Sharpe +0.085), scans 4
parameters that affect stock selection quality:

  - buy_momentum: 60, 65 (current), 70
  - buy_holder_min: 25, 30, 35 (current), 40
  - min_secondary_dims: 1 (current), 2
  - sell_momentum: 30, 35, 40 (current), 45

10 representative combos (not full 96-grid). Each ~45 min.
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

# S1 base (current production best: Sharpe +0.085)
BASE = dict(
    max_positions=5, risk_stop_multiplier=0.70,
    volume_weight=0.05, enable_volume_risk=True,
    pe_exemption_for_volume=True,
    enable_event_filter=False, event_penalty_weight=0.0, tech_weight=0.0,
    low_vol_stop_exemption=0.0,
    buy_dividend_min=55, buy_forecast_min=70, buy_prosperity_min=45,
    # These 4 are swept:
    # buy_momentum=65, buy_holder_min=35, min_secondary_dims=1, sell_momentum=40
)

# 10 representative combos
COMBOS = [
    # === Reference (S1 reused) ===
    ("S1_reference", {}),

    # === buy_momentum sweep ===
    ("mom60", {"buy_momentum": 60}),
    ("mom70", {"buy_momentum": 70}),

    # === buy_holder_min sweep ===
    ("holder25", {"buy_holder_min": 25}),
    ("holder40", {"buy_holder_min": 40}),

    # === min_secondary_dims ===
    ("dims2", {"min_secondary_dims": 2}),

    # === sell_momentum sweep ===
    ("sell30", {"sell_momentum": 30}),
    ("sell45", {"sell_momentum": 45}),

    # === Combined: looser buy (more candidates) ===
    ("loose_buy", {"buy_momentum": 60, "buy_holder_min": 25}),

    # === Combined: stricter quality (fewer but better) ===
    ("strict_quality", {"buy_momentum": 70, "min_secondary_dims": 2, "buy_holder_min": 40}),
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
            WHERE a.trade_date = ? AND a.close > 0 AND b.close > 0 AND a.vol > 0
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


def max_drawdown(nav_history):
    peak = nav_history[0] if nav_history else 0
    mdd = 0.0
    for v in nav_history:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > mdd: mdd = dd
    return mdd


def sharpe_proxy(ret, mdd):
    return ret / mdd if mdd > 0.01 else 0.0


def run_combo(label, overrides, universe):
    params = {**BASE,
              "buy_momentum": 65, "buy_holder_min": 35,
              "min_secondary_dims": 1, "sell_momentum": 40,
              **overrides}
    print(f"\n{'='*70}", flush=True)
    extras = {k: v for k, v in overrides.items() if k not in ()}
    print(f"  {label}: {extras}", flush=True)
    print(f"{'='*70}", flush=True)
    account = reset_account(f"fp_{label}", params)
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
    ret = (final_nav / INITIAL_CAPITAL - 1) * 100
    mdd = max_drawdown(nav_history) if nav_history else 0
    trades = account.get_trades()
    account.close()
    return {
        "label": label, "overrides": extras,
        "final_nav": final_nav, "return_pct": ret,
        "max_drawdown_pct": mdd, "sharpe": sharpe_proxy(ret, mdd),
        "n_trades": len(trades),
        "distinct_stocks": len({t.ts_code for t in trades}),
        "elapsed_sec": elapsed,
    }


def main():
    print(f"\nBuilding universe (top {UNIVERSE_SIZE})...")
    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe: {len(universe)} stocks")
    print(f"  Combos: {len(COMBOS)} × ~50min = ~{len(COMBOS)*50//60}h\n")

    # Reuse S1 from stage2 if available
    results = []
    try:
        with sqlite3.connect(DB_PATH) as c:
            row = c.execute(
                "SELECT COUNT(*) FROM paper_nav_history n "
                "JOIN paper_accounts a ON n.account_id=a.id "
                "WHERE a.name='s2_S1_pe_exempt'"
            ).fetchone()
            if row[0] >= 120:
                print("  [reuse] S1 from stage2 (s2_S1_pe_exempt)")
                account = PaperAccount.load("s2_S1_pe_exempt")
                nav_rows = account.get_nav_history()
                nav_history = [r.total_equity for r in nav_rows] if nav_rows else []
                trades = account.get_trades()
                final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
                ret = (final_nav / INITIAL_CAPITAL - 1) * 100
                mdd = max_drawdown(nav_history) if nav_history else 0
                results.append({
                    "label": "S1_reference (reused)", "overrides": {},
                    "final_nav": final_nav, "return_pct": ret,
                    "max_drawdown_pct": mdd, "sharpe": sharpe_proxy(ret, mdd),
                    "n_trades": len(trades),
                    "distinct_stocks": len({t.ts_code for t in trades}),
                    "elapsed_sec": 0,
                })
                account.close()
    except Exception:
        pass

    s1_reused = any(r["label"].startswith("S1_reference") for r in results)
    for label, overrides in COMBOS:
        if s1_reused and label == "S1_reference":
            continue
        try:
            r = run_combo(label, overrides, universe)
            results.append(r)
            print(f"\n  → {label}: ret={r['return_pct']:+.2f}% "
                  f"MDD={r['max_drawdown_pct']:.2f}% Sharpe={r['sharpe']:+.3f} "
                  f"trades={r['n_trades']} stocks={r['distinct_stocks']} "
                  f"({r['elapsed_sec']/60:.1f}min)", flush=True)
        except Exception as e:
            import traceback
            print(f"\n  ✗ {label} FAILED: {e}", flush=True)
            traceback.print_exc()

    # Final table sorted by Sharpe
    valid = sorted([r for r in results if "error" not in r],
                   key=lambda x: x["sharpe"], reverse=True)
    print("\n\n" + "=" * 105, flush=True)
    print(f"  FINE PARAMETER SWEEP — {START} → {END}", flush=True)
    print("=" * 105, flush=True)
    print(f"{'Rank':<5} {'Label':<22} {'Return%':>9} {'MaxDD%':>8} {'Sharpe':>8} "
          f"{'Trades':>7} {'Stocks':>7} {'Δ vs S1':>9}", flush=True)
    print("-" * 105, flush=True)
    s1 = next((r for r in valid if "S1" in r["label"]), valid[0])
    for rank, r in enumerate(valid, 1):
        d = r["sharpe"] - s1["sharpe"]
        marker = " 🏆" if rank == 1 else ""
        print(f"#{rank:<3} {r['label']:<22} {r['return_pct']:>+8.2f}% "
              f"{r['max_drawdown_pct']:>7.2f}% {r['sharpe']:>+8.3f} "
              f"{r['n_trades']:>7} {r['distinct_stocks']:>7} "
              f"{d:>+8.3f}{marker}", flush=True)
    print("=" * 105, flush=True)

    with open("logs/fine_params_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nSaved to logs/fine_params_results.json", flush=True)


if __name__ == "__main__":
    main()
