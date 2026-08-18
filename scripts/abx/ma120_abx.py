"""A/B: MA120 硬触发 bear 的效果验证.

  B0_baseline    — 当前 HMM（无 MA120 触发）
  B1_ma120_5pct  — MA120 -5% 硬触发 bear（当前实现）

关键验证：2022 年 bear 天数从 6 → 142，但收益是否真的改善？
⚠️ 注意：B0 不能直接跑（MA120 逻辑已编译进代码），需要用环境变量禁用。
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


def build_universe(top_n):
    with get_market_conn() as c:
        rows = c.execute(
            "SELECT ts_code FROM daily_price WHERE trade_date='20210104' "
            "AND close > 0 AND vol > 0 ORDER BY amount DESC LIMIT ?", (top_n,)
        ).fetchall()
    return [r[0] for r in rows]


def reset_account(name):
    from davis_analyzer.paper_trading.runlock import delete_account_if_idle

    delete_account_if_idle(name)
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


def compute_benchmark(start, end, capital):
    with get_market_conn() as c:
        for idx_code in ("000300.SH", "000001.SH"):
            rows = c.execute(
                "SELECT close FROM index_daily WHERE ts_code=? AND trade_date>=? AND trade_date<=? AND close>0 ORDER BY trade_date",
                (idx_code, start, end)).fetchall()
            if len(rows) > 10:
                break
        else:
            return None
    closes = [float(r[0]) for r in rows]
    return {"index": idx_code, "return_pct": (closes[-1] / closes[0] - 1) * 100}


def run_variant(label, use_ma120, universe):
    print(f"\n{'='*70}\n  {label}\n{'='*70}", flush=True)

    # 控制 MA120 触发
    import davis_analyzer.market_regime as mr
    if use_ma120:
        mr._MA120_BEAR_THRESHOLD = -0.05
    else:
        mr._MA120_BEAR_THRESHOLD = -999.0  # 禁用
    mr.reset_hmm_cache()

    account = reset_account(f"ma120_{label}")
    strategy = FactorThresholdStrategy()

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
    costs_pct = costs / INITIAL_CAPITAL * 100
    ret_net = total_ret - costs_pct

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
    mr._MA120_BEAR_THRESHOLD = -0.05  # restore

    result = {
        "label": label, "use_ma120": use_ma120,
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
    print(f"  MA120 硬触发 A/B — {START} → {END}")
    print(f"{'='*80}\n")

    universe = build_universe(UNIVERSE_SIZE)
    benchmark = compute_benchmark(START, END, INITIAL_CAPITAL)
    print(f"  Universe: {len(universe)} stocks")
    if benchmark:
        print(f"  基准 {benchmark['index']}: {benchmark['return_pct']:+.2f}%\n")

    results = []
    # B0: 无 MA120（HMM only）
    results.append(run_variant("B0_hmm_only", False, universe))
    # B1: 含 MA120 -5% 触发
    results.append(run_variant("B1_ma120_5pct", True, universe))

    # Summary
    print(f"\n\n{'='*90}")
    print(f"  MA120 A/B 总览 — {START} → {END}")
    if benchmark:
        print(f"  基准: {benchmark['return_pct']:+.2f}%")
    print(f"{'='*90}")
    print(f"  {'变体':<18} {'净收益':>8} {'回撤':>6} {'Sharpe':>7} {'交易':>5}")
    for r in sorted(results, key=lambda x: -x["sharpe_real"]):
        print(f"  {r['label']:<18} {r['return_pct_net']:>+7.2f}% "
              f"{r['max_drawdown_pct']:>5.1f}% {r['sharpe_real']:>+7.3f} {r['n_trades']:>5}")

    print(f"\n  年度对比:")
    all_years = sorted({yr for r in results for yr in r.get("annual_returns", {})})
    for r in results:
        vals = " ".join(f"{r['annual_returns'].get(y, 0):>+7.1f}%" for y in all_years)
        print(f"  {r['label']:<18} {vals}")

    out_path = "logs/ma120_abx.json"
    with open(out_path, "w") as f:
        json.dump({"start": START, "end": END, "benchmark": benchmark, "results": results},
                  f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  结果: {out_path}")


if __name__ == "__main__":
    main()
