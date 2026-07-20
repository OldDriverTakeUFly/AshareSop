"""Compute A/B summary directly from DB without re-running backfills.

Reads the existing abtest_A_legacy and abtest_B_volume accounts' NAV history
and trades to produce the comparison table.
"""
import os, sys, sqlite3, json
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)

from stockhot.storage.database import DB_PATH, init_database
from davis_analyzer.paper_trading.account import PaperAccount

init_database()
INITIAL_CAPITAL = 1_000_000


def max_drawdown(nav_history: list[float]) -> float:
    peak = nav_history[0] if nav_history else 0
    mdd = 0.0
    for v in nav_history:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > mdd: mdd = dd
    return mdd


def summarize(name: str) -> dict:
    try:
        account = PaperAccount.load(name)
    except ValueError as e:
        return {"label": name, "error": str(e)}

    nav_rows = account.get_nav_history()
    nav_history = [row.total_equity for row in nav_rows] if nav_rows else []
    final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
    total_ret = (final_nav / INITIAL_CAPITAL - 1) * 100
    mdd = max_drawdown(nav_history) if nav_history else 0

    trades = account.get_trades()
    n_trades = len(trades)
    distinct_stocks = len({t.ts_code for t in trades}) if trades else 0
    n_buys = sum(1 for t in trades if t.action == "BUY")
    n_sells = sum(1 for t in trades if t.action == "SELL")
    highvol_sells = sum(
        1 for t in trades
        if t.action == "SELL" and "高位放量" in (t.signal_reason or "")
    )

    # Read config to confirm volume_weight + enable_volume_risk
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute(
            "SELECT config_json FROM paper_accounts WHERE name=?", (name,)
        ).fetchone()
    config = json.loads(row[0]) if row and row[0] else {}

    account.close()
    return {
        "label": name,
        "volume_weight": config.get("volume_weight", "?"),
        "enable_volume_risk": config.get("enable_volume_risk", "?"),
        "final_nav": final_nav,
        "return_pct": total_ret,
        "max_drawdown_pct": mdd,
        "n_trades": n_trades,
        "n_buys": n_buys,
        "n_sells": n_sells,
        "distinct_stocks": distinct_stocks,
        "highvol_sells": highvol_sells,
        "n_nav_days": len(nav_history),
    }


results = []
for name in ("abtest_A_legacy", "abtest_B_volume"):
    r = summarize(name)
    results.append(r)
    if "error" in r:
        print(f"\n✗ {name}: {r['error']}")
    else:
        print(f"\n✓ {name}: vol_wt={r['volume_weight']} v_risk={r['enable_volume_risk']} "
              f"ret={r['return_pct']:+.2f}% MDD={r['max_drawdown_pct']:.2f}% "
              f"trades={r['n_trades']} stocks={r['distinct_stocks']} "
              f"highvol_sells={r['highvol_sells']}")

# Comparison table
print("\n" + "=" * 90)
print("  VOLUME-PRICE A/B RESULTS — 20260105 → 20260715")
print("=" * 90)
print(f"{'Variant':<18} {'VolWt':>6} {'VRisk':>6} {'Return%':>9} {'MaxDD%':>8} "
      f"{'Trades':>7} {'Buys':>6} {'Sells':>6} {'Stocks':>7} {'HighVol':>8}")
print("-" * 90)
for r in results:
    if "error" in r:
        print(f"{r['label']:<18}  (not run yet — {r['error']})")
        continue
    print(f"{r['label']:<18} {str(r['volume_weight']):>6} {str(r['enable_volume_risk']):>6} "
          f"{r['return_pct']:>+8.2f}% {r['max_drawdown_pct']:>7.2f}% "
          f"{r['n_trades']:>7} {r['n_buys']:>6} {r['n_sells']:>6} "
          f"{r['distinct_stocks']:>7} {r['highvol_sells']:>8}")
print("=" * 90)

# Delta
valid = [r for r in results if "error" not in r]
if len(valid) == 2:
    a, b = valid
    d_ret = b["return_pct"] - a["return_pct"]
    d_mdd = b["max_drawdown_pct"] - a["max_drawdown_pct"]
    d_stocks = b["distinct_stocks"] - a["distinct_stocks"]
    print(f"\nΔ (B - A):")
    print(f"  Return:      {d_ret:+.2f} pp")
    print(f"  MaxDD:       {d_mdd:+.2f} pp")
    print(f"  # stocks:    {d_stocks:+d}")
    print(f"  HighVol sells in B: {b['highvol_sells']}")

# Save
with open("logs/volume_ab_summary.json", "w") as f:
    json.dump(results, f, indent=2, default=str)
print(f"\nSaved to logs/volume_ab_summary.json")
