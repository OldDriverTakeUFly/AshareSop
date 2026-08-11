"""A/B: iVIX 恐慌暂停因子的效果验证.

  I0_baseline   — 当前生产配置（无 iVIX 暂停）
  I1_ivix_25    — iVIX > 25 且非bear时暂停买入

实证依据:
  高VIX(>25)+上涨趋势 → 5d收益 -0.42%（追涨被套）
  高VIX(>25)+下跌趋势 → 5d收益 +2.29%（恐慌反弹，不暂停）
  正常VIX+上涨趋势   → 5d收益 -0.03%（基准）

设计: 只在非bear+高VIX时暂停买入，不影响bear时的恐慌抄底。
"""
import os, sys, time, json, sqlite3
import numpy as np
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="ERROR")

from stockhot.data_layer.market_db import get_connection as get_market_conn
from stockhot.storage.database import init_database, DB_PATH
from davis_analyzer.paper_trading.account import PaperAccount
from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
from davis_analyzer.paper_trading.executor import run_backfill_auto
init_database()

START = "20210104"
END = "20260731"
INITIAL_CAPITAL = 1_000_000
UNIVERSE_SIZE = 200
SCORING_FREQUENCY = 1
COMMISSION_RATE = 0.0008
STAMP_TAX_RATE = 0.0005

BASE = dict(
    max_positions=5, risk_stop_multiplier=0.70, sell_momentum=30,
    volume_weight=0.05, enable_volume_risk=True, pe_exemption_for_volume=True,
    max_intraday_amplitude=0.08, quality_weight=0.10, gap_weight=0.05,
    enable_event_filter=False, event_penalty_weight=0.0, tech_weight=0.0,
    enable_adaptive_sell=False, enable_dynamic_weight=False,
    amihud_weight=0.0, dragon_tiger_weight=0.0, repurchase_weight=0.0,
    low_vol_stop_exemption=0.0,
    buy_momentum=70, buy_holder_min=40, buy_dividend_min=55,
    buy_forecast_min=70, buy_prosperity_min=45, min_secondary_dims=1,
)


def build_universe(top_n):
    with get_market_conn() as c:
        rows = c.execute(
            "SELECT ts_code FROM daily_price WHERE trade_date='20210104' "
            "AND close > 0 AND vol > 0 ORDER BY amount DESC LIMIT ?", (top_n,)
        ).fetchall()
    return [r[0] for r in rows]


def reset_account(name):
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute("SELECT id FROM paper_accounts WHERE name=?", (name,)).fetchone()
        if row:
            aid = row[0]
            for tbl in ("paper_positions", "paper_trades", "paper_nav_history", "paper_shadow_trades"):
                c.execute(f"DELETE FROM {tbl} WHERE account_id=?", (aid,))
            c.execute("DELETE FROM paper_accounts WHERE id=?", (aid,))
            c.commit()
    return PaperAccount.create(name=name, strategy_name="factor_threshold",
                               initial_capital=INITIAL_CAPITAL, config={})


def max_dd(nav):
    peak = nav[0] if nav else 0
    mdd = 0
    for v in nav:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > mdd:
            mdd = dd
    return mdd


def real_sharpe(daily_returns, annualization=252):
    if len(daily_returns) < 10:
        return 0.0
    arr = np.array(daily_returns)
    std = arr.std(ddof=1)
    if std < 1e-8:
        return 0.0
    return float(arr.mean() / std * np.sqrt(annualization))


def compute_costs(trades):
    total = 0.0
    for t in trades:
        notional = abs(t.shares * t.price)
        total += notional * COMMISSION_RATE
        if t.action.upper() == "SELL":
            total += notional * STAMP_TAX_RATE
    return total


def run_variant(label, ivix_threshold, universe):
    print(f"\n{'='*70}\n  {label}: ivix_pause={ivix_threshold}\n{'='*70}", flush=True)

    account = reset_account(f"ivix_{label}")
    params = {**BASE, "ivix_pause_threshold": ivix_threshold}
    strategy = FactorThresholdStrategy(**params)

    t0 = time.time()
    run_backfill_auto(account, strategy, START, END,
                      universe_codes=universe, scoring_frequency=SCORING_FREQUENCY)
    elapsed = time.time() - t0

    nav_rows = account.get_nav_history()
    nav_history = [r.total_equity for r in nav_rows]
    final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
    total_ret = (final_nav / INITIAL_CAPITAL - 1) * 100
    total_mdd = max_dd(nav_history)
    daily_rets = [r.daily_return for r in nav_rows if r.daily_return is not None]
    sharpe_real = real_sharpe(daily_rets)
    trades = account.get_trades()
    costs = compute_costs(trades)
    ret_net = total_ret - costs / INITIAL_CAPITAL * 100

    # 年度
    annual = {}
    for r in nav_rows:
        yr = r.trade_date[:4]
        if yr not in annual:
            annual[yr] = {"start": r.total_equity, "end": r.total_equity}
        annual[yr]["end"] = r.total_equity
    year_rets = {}
    prev_end = INITIAL_CAPITAL
    for yr in sorted(annual):
        year_rets[yr] = (annual[yr]["end"] / prev_end - 1) * 100
        prev_end = annual[yr]["end"]

    account.close()

    result = {
        "label": label, "ivix_pause_threshold": ivix_threshold,
        "return_pct_gross": round(total_ret, 2),
        "return_pct_net": round(ret_net, 2),
        "max_drawdown_pct": round(total_mdd, 2),
        "sharpe_real": round(sharpe_real, 3),
        "n_trades": len(trades),
        "elapsed_hr": round(elapsed / 3600, 1),
        "annual_returns": {yr: round(r, 2) for yr, r in year_rets.items()},
    }
    print(f"  → {label}: 毛收益={total_ret:+.2f}% 净={ret_net:+.2f}% "
          f"MDD={total_mdd:.1f}% Sharpe={sharpe_real:+.3f} "
          f"交易={len(trades)} ({elapsed/3600:.1f}h)", flush=True)
    print(f"    年度: {year_rets}", flush=True)
    return result


def main():
    print(f"\n{'='*80}")
    print(f"  iVIX 恐慌暂停 A/B — {START} → {END}")
    print(f"{'='*80}\n")

    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe: {len(universe)} stocks\n")

    results = []
    results.append(run_variant("I0_baseline", 0.0, universe))   # 无 iVIX
    results.append(run_variant("I1_ivix_25", 25.0, universe))   # VIX>25暂停

    # Summary
    print(f"\n\n{'='*90}")
    print(f"  iVIX A/B 总览 — {START} → {END}")
    print(f"{'='*90}")
    print(f"  {'变体':<16} {'ivix阈值':>8} {'净收益':>8} {'回撤':>6} {'Sharpe':>7} {'交易':>5}")
    for r in sorted(results, key=lambda x: -x["sharpe_real"]):
        print(f"  {r['label']:<16} {r['ivix_pause_threshold']:>8.1f} "
              f"{r['return_pct_net']:>+7.2f}% {r['max_drawdown_pct']:>5.1f}% "
              f"{r['sharpe_real']:>+7.3f} {r['n_trades']:>5}")

    print(f"\n  年度对比:")
    all_years = sorted({yr for r in results for yr in r.get("annual_returns", {})})
    for r in results:
        vals = " ".join(f"{r['annual_returns'].get(y, 0):>+7.1f}%" for y in all_years)
        print(f"  {r['label']:<16} {vals}")

    out_path = "logs/ivix_abx.json"
    with open(out_path, "w") as f:
        json.dump({"start": START, "end": END, "results": results},
                  f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  结果: {out_path}")


if __name__ == "__main__":
    main()
