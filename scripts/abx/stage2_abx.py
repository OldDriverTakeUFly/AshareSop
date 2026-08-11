"""Stage-2 A/B test: PE exemption + low_vol stop exemption.

Building on the Sharpe-optimized config (stop0.70_pos5, current default),
tests whether the two Stage-2 exemptions improve Sharpe further:

  S0_stop0.70_pos5 (reference)  — current default, no exemptions
  S1_pe_exempt                  — + PE exemption for platform_breakout/low_vol
  S2_lowvol_stop                — + low_vol stop exemption (50% wider)
  S3_both                       — + both exemptions

Each variant ~45 min, total ~3 hours.
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

# Sharpe-optimized base (current production default)
BASE_PARAMS = dict(
    max_positions=5,  # Sharpe-optimized
    buy_momentum=65, sell_momentum=40,
    buy_holder_min=35, buy_dividend_min=55,
    buy_forecast_min=70, buy_prosperity_min=45,
    min_secondary_dims=1,
    volume_weight=0.05, enable_volume_risk=True,
    enable_event_filter=False, event_penalty_weight=0.0,
    tech_weight=0.0,
    risk_stop_multiplier=0.70,  # Sharpe-optimized
)

VARIANTS = [
    ("S0_baseline", {**BASE_PARAMS,
                     "pe_exemption_for_volume": False,
                     "low_vol_stop_exemption": 0.0}),
    ("S1_pe_exempt", {**BASE_PARAMS,
                      "pe_exemption_for_volume": True,
                      "low_vol_stop_exemption": 0.0}),
    ("S2_lowvol_stop", {**BASE_PARAMS,
                        "pe_exemption_for_volume": False,
                        "low_vol_stop_exemption": 0.5}),
    ("S3_both", {**BASE_PARAMS,
                 "pe_exemption_for_volume": True,
                 "low_vol_stop_exemption": 0.5}),
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
    if max_dd_pct <= 0.01:
        return 0.0
    return return_pct / max_dd_pct


def run_variant(label: str, params: dict, universe: list[str]) -> dict:
    print(f"\n{'='*70}", flush=True)
    print(f"  {label}: pe_exempt={params['pe_exemption_for_volume']} "
          f"lowvol_stop={params['low_vol_stop_exemption']}", flush=True)
    print(f"{'='*70}", flush=True)
    account = reset_account(f"s2_{label}", params)
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
    lowvol_exemptions = sum(1 for t in trades if t.action == "SELL"
                             and "低位放量豁免" in (t.signal_reason or ""))
    account.close()
    return {
        "label": label,
        "pe_exemption_for_volume": params["pe_exemption_for_volume"],
        "low_vol_stop_exemption": params["low_vol_stop_exemption"],
        "final_nav": final_nav, "return_pct": total_ret,
        "max_drawdown_pct": mdd,
        "sharpe": sharpe_proxy(total_ret, mdd),
        "n_trades": len(trades),
        "highvol_sells": highvol_sells,
        "lowvol_exemptions": lowvol_exemptions,
        "elapsed_sec": elapsed,
    }


def main():
    print(f"\nBuilding universe (top {UNIVERSE_SIZE} by 90d return)...")
    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe: {len(universe)} stocks\n")

    # Reuse S0 from sharpe_sweep if available (stop0.70_pos5 has same config)
    results = []
    try:
        with sqlite3.connect(DB_PATH) as c:
            row = c.execute(
                "SELECT COUNT(*) FROM paper_nav_history n "
                "JOIN paper_accounts a ON n.account_id=a.id "
                "WHERE a.name='sh_stop0.70_pos5'"
            ).fetchone()
            if row[0] >= 120:
                print("  [reuse] S0 from sharpe_sweep (sh_stop0.70_pos5)")
                account = PaperAccount.load("sh_stop0.70_pos5")
                nav_rows = account.get_nav_history()
                nav_history = [r.total_equity for r in nav_rows] if nav_rows else []
                trades = account.get_trades()
                final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
                ret = (final_nav / INITIAL_CAPITAL - 1) * 100
                mdd = max_drawdown(nav_history) if nav_history else 0
                results.append({
                    "label": "S0_baseline (reused)",
                    "pe_exemption_for_volume": False,
                    "low_vol_stop_exemption": 0.0,
                    "final_nav": final_nav, "return_pct": ret,
                    "max_drawdown_pct": mdd, "sharpe": sharpe_proxy(ret, mdd),
                    "n_trades": len(trades),
                    "highvol_sells": sum(1 for t in trades if t.action == "SELL"
                                          and "高位放量" in (t.signal_reason or "")),
                    "lowvol_exemptions": 0,
                    "elapsed_sec": 0,
                })
                account.close()
    except Exception:
        pass

    s0_reused = any(r["label"].startswith("S0_baseline") for r in results)
    for label, params in VARIANTS:
        if s0_reused and label == "S0_baseline":
            continue
        try:
            r = run_variant(label, params, universe)
            results.append(r)
            print(f"\n  → {label}: ret={r['return_pct']:+.2f}% "
                  f"MDD={r['max_drawdown_pct']:.2f}% Sharpe={r['sharpe']:+.3f} "
                  f"trades={r['n_trades']} lv_exempt={r['lowvol_exemptions']} "
                  f"({r['elapsed_sec']/60:.1f}min)", flush=True)
        except Exception as e:
            import traceback
            print(f"\n  ✗ {label} FAILED: {e}", flush=True)
            traceback.print_exc()

    # Final table
    valid = sorted([r for r in results if "error" not in r],
                   key=lambda x: x["sharpe"], reverse=True)
    print("\n\n" + "=" * 100, flush=True)
    print(f"  STAGE-2 A/B — {START} → {END}", flush=True)
    print("=" * 100, flush=True)
    print(f"{'Rank':<5} {'Label':<22} {'PEExempt':>8} {'LVStop':>7} "
          f"{'Return%':>9} {'MaxDD%':>8} {'Sharpe':>8} {'Trades':>7} {'LVEx':>5}",
          flush=True)
    print("-" * 100, flush=True)
    for rank, r in enumerate(valid, 1):
        print(f"#{rank:<3} {r['label']:<22} "
              f"{str(r['pe_exemption_for_volume']):>8} "
              f"{r['low_vol_stop_exemption']:>7.1f} "
              f"{r['return_pct']:>+8.2f}% {r['max_drawdown_pct']:>7.2f}% "
              f"{r['sharpe']:>+8.3f} {r['n_trades']:>7} "
              f"{r.get('lowvol_exemptions', 0):>5}", flush=True)
    print("=" * 100, flush=True)

    if len(valid) >= 2:
        s0 = next((r for r in valid if "S0" in r["label"]), valid[0])
        best = valid[0]
        if best["label"] != s0["label"]:
            print(f"\n  🏆 BEST: {best['label']}", flush=True)
            print(f"     Sharpe: {best['sharpe']:+.3f} vs S0 {s0['sharpe']:+.3f} "
                  f"(Δ={best['sharpe'] - s0['sharpe']:+.3f})", flush=True)

    with open("logs/stage2_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)


if __name__ == "__main__":
    main()
