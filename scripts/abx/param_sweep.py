"""Focused parameter sweep around the volume-price strategy.

Starting point: the validated B_volume variant (ret=+12.15%, MDD=11.65%).
Sweeps 6 dimensions to find a better local optimum:

  1. volume_weight: 0.05, 0.10 (current), 0.15, 0.20
  2. buy_momentum: 60, 65 (current)
  3. buy_holder_min: 25, 30, 35 (current)
  4. min_secondary_dims: 1 (current), 2
  5. enable_volume_risk: True (current), False (isolate buy-side effect)
  6. max_positions: 8, 10 (current), 12

To keep total runtime manageable (~1 hour), we pick 10 representative combos
(rather than full 4×2×3×2×2×3 = 288 grid). Each combo is labeled by its
experimental focus.

Output: comparison table sorted by return, plus a "what we learned" summary.
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
SCORING_FREQUENCY = 5  # re-score every 5 trading days (sweep has many combos — favor speed)


# ── Baseline params (= the validated B_volume config) ──
BASELINE = dict(
    max_positions=10,
    buy_momentum=65,
    sell_momentum=40,
    buy_holder_min=35,
    buy_dividend_min=55,
    buy_forecast_min=70,
    buy_prosperity_min=45,
    min_secondary_dims=1,
    volume_weight=0.10,
    enable_volume_risk=True,
)


# ── 8 representative combos (down from 11 to keep sweep runtime tractable) ──
# Each row: (label, overrides_dict, focus_description)
COMBOS = [
    # === Reference point ===
    ("baseline_B",
     {},
     "Validated B_volume baseline (reference)"),

    # === Volume weight sweep (optimization direction 1) ===
    ("vw_05",
     {"volume_weight": 0.05},
     "Lower volume weight — less aggressive on volume signal"),
    ("vw_15",
     {"volume_weight": 0.15},
     "Higher volume weight"),
    ("vw_20",
     {"volume_weight": 0.20},
     "Much higher volume weight — does volume dominate?"),

    # === Buy-side only (disable risk sell) — isolate composite effect ===
    ("buy_only_no_risk",
     {"enable_volume_risk": False},
     "Volume in composite only, NO high-vol risk sell — isolate buy-side"),

    # === Risk sell only (volume_weight=0 but risk on) — isolate sell effect ===
    ("risk_only_no_buy",
     {"volume_weight": 0.0, "enable_volume_risk": True},
     "NO volume in composite, high-vol risk sell only — isolate sell effect"),

    # === Looser buy gates (more candidates) ===
    ("loose_buy",
     {"buy_momentum": 60, "buy_holder_min": 30},
     "Looser primary gates → more candidates"),

    # === Aggressive (multiple changes toward higher throughput) ===
    ("aggressive",
     {"volume_weight": 0.15, "buy_momentum": 60, "buy_holder_min": 30,
      "max_positions": 12},
     "Aggressive: high vol weight + loose gates + more positions"),
]


def build_universe(top_n: int) -> list[str]:
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


def run_combo(label: str, overrides: dict, focus: str, universe: list[str]) -> dict:
    params = dict(BASELINE)
    params.update(overrides)
    print(f"\n{'='*70}", flush=True)
    print(f"  [{label}] {focus}", flush=True)
    print(f"  params: {overrides}", flush=True)
    print(f"{'='*70}", flush=True)

    account = reset_account(f"sweep_{label}", params)
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
    distinct_stocks = len({t.ts_code for t in trades}) if trades else 0
    highvol_sells = sum(
        1 for t in trades
        if t.action == "SELL" and "高位放量" in (t.signal_reason or "")
    )
    account.close()

    return {
        "label": label, "focus": focus, "params": overrides,
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
    print(f"  Universe size: {len(universe)} stocks")
    print(f"  Window: {START} → {END}, scoring every {SCORING_FREQUENCY} days")
    # Rough estimate: 127/5=26 scoring days × ~55s + ~5s/day run × 127 = ~25 min/variant
    est_per = (127 // SCORING_FREQUENCY) * 55 / 60 + 10
    print(f"  Total combos: {len(COMBOS)} × ~{est_per:.0f} min = ~{len(COMBOS)*est_per/60:.1f} hours\n")

    all_results = []
    for i, (label, overrides, focus) in enumerate(COMBOS):
        # Skip combos already completed (have data in DB)
        skip = False
        try:
            with sqlite3.connect(DB_PATH) as c:
                row = c.execute(
                    "SELECT COUNT(*) FROM paper_trades t "
                    "JOIN paper_accounts a ON t.account_id=a.id "
                    "WHERE a.name=?", (f"sweep_{label}",)
                ).fetchone()
                skip = row[0] > 0 if row else False
        except Exception:
            pass

        if skip and "--rerun" not in sys.argv:
            print(f"\n  [skip] sweep_{label} already has data; reuse", flush=True)
            account = PaperAccount.load(f"sweep_{label}")
            nav_rows = account.get_nav_history()
            nav_history = [r.total_equity for r in nav_rows] if nav_rows else []
            final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
            total_ret = (final_nav / INITIAL_CAPITAL - 1) * 100
            mdd = max_drawdown(nav_history) if nav_history else 0
            trades = account.get_trades()
            highvol_sells = sum(1 for t in trades if t.action == "SELL" and "高位放量" in (t.signal_reason or ""))
            r = {
                "label": label, "focus": focus, "params": overrides,
                "final_nav": final_nav, "return_pct": total_ret,
                "max_drawdown_pct": mdd, "n_trades": len(trades),
                "n_buys": sum(1 for t in trades if t.action == "BUY"),
                "n_sells": sum(1 for t in trades if t.action == "SELL"),
                "distinct_stocks": len({t.ts_code for t in trades}),
                "highvol_sells": highvol_sells, "elapsed_sec": 0,
            }
            all_results.append(r)
            print(f"  → {label}: ret={r['return_pct']:+.2f}% MDD={r['max_drawdown_pct']:.2f}% "
                  f"trades={r['n_trades']} (reused)", flush=True)
            account.close()
            continue

        try:
            r = run_combo(label, overrides, focus, universe)
            all_results.append(r)
            print(f"\n  → {label}: ret={r['return_pct']:+.2f}% MDD={r['max_drawdown_pct']:.2f}% "
                  f"trades={r['n_trades']} highvol={r['highvol_sells']} "
                  f"({r['elapsed_sec']/60:.1f}min)", flush=True)
        except Exception as e:
            import traceback
            print(f"\n  ✗ {label} FAILED: {e}", flush=True)
            traceback.print_exc()
            all_results.append({"label": label, "error": str(e), "focus": focus,
                                "params": overrides})

    # ── Final comparison table (sorted by return) ──
    valid = [r for r in all_results if "error" not in r]
    valid.sort(key=lambda x: x["return_pct"], reverse=True)

    print("\n\n" + "=" * 100, flush=True)
    print(f"  PARAMETER SWEEP RESULTS — {START} → {END}", flush=True)
    print("=" * 100, flush=True)
    print(f"{'Rank':<5} {'Label':<22} {'Return%':>9} {'MaxDD%':>8} {'Trades':>7} "
          f"{'Stocks':>7} {'HighVol':>8} {'Sharpe*':>8}", flush=True)
    print("-" * 100, flush=True)
    for rank, r in enumerate(valid, 1):
        # Crude Sharpe approximation: return / maxDD
        sharpe = r["return_pct"] / r["max_drawdown_pct"] if r["max_drawdown_pct"] > 0 else 0
        print(f"#{rank:<3} {r['label']:<22} {r['return_pct']:>+8.2f}% {r['max_drawdown_pct']:>7.2f}% "
              f"{r['n_trades']:>7} {r['distinct_stocks']:>7} {r['highvol_sells']:>8} "
              f"{sharpe:>8.2f}", flush=True)
    print("=" * 100, flush=True)
    print(f"  (* Sharpe approximated as return%/MaxDD% — higher is better)", flush=True)
    print(f"  Baseline B_volume was +12.15%/11.65% — compare to rank #1 above", flush=True)

    # ── What we learned ──
    print("\n" + "=" * 100, flush=True)
    print("  KEY OBSERVATIONS", flush=True)
    print("=" * 100, flush=True)

    by_label = {r["label"]: r for r in valid}
    base = by_label.get("baseline_B")
    if base:
        # Volume weight sweep
        for label, desc in [
            ("vw_05", "Volume weight 0.05"),
            ("vw_15", "Volume weight 0.15"),
            ("vw_20", "Volume weight 0.20"),
        ]:
            r = by_label.get(label)
            if r:
                d = r["return_pct"] - base["return_pct"]
                print(f"  {desc:<25}: Δ={d:+.2f}pp  "
                      f"(ret={r['return_pct']:+.2f}%, MDD={r['max_drawdown_pct']:.2f}%)", flush=True)
        print()
        # Buy-side vs sell-side isolation
        for label, desc in [
            ("buy_only_no_risk", "Buy-side only (no risk sell)"),
            ("risk_only_no_buy", "Risk sell only (no buy weight)"),
        ]:
            r = by_label.get(label)
            if r:
                d = r["return_pct"] - base["return_pct"]
                print(f"  {desc:<25}: Δ={d:+.2f}pp  (ret={r['return_pct']:+.2f}%)", flush=True)
        print()
        # Other variants
        for label, desc in [
            ("loose_buy", "Loose buy gates"),
            ("aggressive", "Aggressive combo"),
        ]:
            r = by_label.get(label)
            if r:
                d = r["return_pct"] - base["return_pct"]
                print(f"  {desc:<25}: Δ={d:+.2f}pp  (ret={r['return_pct']:+.2f}%)", flush=True)

    # Save
    os.makedirs("logs", exist_ok=True)
    with open("logs/param_sweep_results.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nDetailed results saved to logs/param_sweep_results.json", flush=True)


if __name__ == "__main__":
    main()
