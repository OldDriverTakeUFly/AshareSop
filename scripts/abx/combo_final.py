"""组合配置最终验证：B0基线 + iVIX暂停 + 超跌反弹.

基于 4 组独立 A/B 的结论：
  B0 无MA120:          +43.84%  Sharpe=+0.614 ← 最优基线
  iVIX>25暂停:         +3.7pp（相对基线）
  超跌反弹:             +9.2pp（相对基线）
  MA120硬触发:          -21.8pp（关闭）
  动量×筹码共振:         -11.6pp（关闭）

本实验验证：三个有效因子叠加后，是否能达到预期的 +55%+？
  C0 = B0 基线（无MA120/iVIX/反弹/共振）→ 参考 +43.84%
  C1 = B0 + iVIX暂停                     → 预期 ~+47%
  C2 = B0 + 超跌反弹                     → 预期 ~+53%
  C3 = B0 + iVIX暂停 + 超跌反弹           → 预期 ~+57%（最终生产配置候选）
"""
import os, sys, time, json, sqlite3
import numpy as np
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="ERROR")

# 关闭 MA120（B0 证明不需要）
import davis_analyzer.market_regime as mr
mr._MA120_BEAR_THRESHOLD = -999.0

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
    holder_momentum_synergy=0.0,  # 关闭共振（A/B 证明负贡献）
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
                "SELECT trade_date, close FROM index_daily WHERE ts_code=? AND trade_date>=? AND trade_date<=? AND close>0 ORDER BY trade_date",
                (idx_code, start, end)).fetchall()
            if len(rows) > 10:
                break
        else:
            return None
    closes = [float(r[1]) for r in rows]
    daily_rets = [(closes[i] / closes[i-1] - 1) * 100 for i in range(1, len(closes)) if closes[i-1] > 0]
    return {"index": idx_code, "return_pct": (closes[-1] / closes[0] - 1) * 100, "sharpe": real_sharpe(daily_rets)}


def run_variant(label, extra_params, universe):
    print(f"\n{'='*70}\n  {label}\n{'='*70}", flush=True)

    account = reset_account(f"combo_{label}")
    params = {**BASE, **extra_params}
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

    bounce_buys = sum(1 for t in trades if "超跌反弹" in (t.signal_reason or ""))

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
        "label": label,
        "params": {k: v for k, v in extra_params.items()},
        "return_pct_gross": round(total_ret, 2),
        "return_pct_net": round(ret_net, 2),
        "max_drawdown_pct": round(total_mdd, 2),
        "sharpe_real": round(sharpe_real, 3),
        "n_trades": len(trades),
        "n_bounce_buys": bounce_buys,
        "elapsed_hr": round(elapsed / 3600, 1),
        "annual_returns": {yr: round(r, 2) for yr, r in year_rets.items()},
    }
    print(f"  → {label}: 净收益={ret_net:+.2f}% MDD={total_mdd:.1f}% "
          f"Sharpe={sharpe_real:+.3f} 交易={len(trades)} (反弹{bounce_buys}) "
          f"({elapsed/3600:.1f}h)", flush=True)
    print(f"    年度: {year_rets}", flush=True)
    return result


def main():
    print(f"\n{'='*80}")
    print(f"  组合配置最终验证 — {START} → {END}")
    print(f"  MA120=OFF, 共振=OFF（A/B 证明应关闭）")
    print(f"{'='*80}\n")

    universe = build_universe(UNIVERSE_SIZE)
    benchmark = compute_benchmark(START, END, INITIAL_CAPITAL)
    print(f"  Universe: {len(universe)} stocks")
    if benchmark:
        print(f"  基准 {benchmark['index']}: {benchmark['return_pct']:+.2f}% Sharpe={benchmark['sharpe']:+.3f}\n")

    results = []
    # C0: 纯基线（参考 B0）
    results.append(run_variant("C0_baseline", {}, universe))
    # C1: +iVIX暂停
    results.append(run_variant("C1_ivix", {"ivix_pause_threshold": 25.0}, universe))
    # C2: +超跌反弹
    results.append(run_variant("C2_bounce", {"enable_oversold_bounce": True}, universe))
    # C3: 全开（最终生产配置候选）
    results.append(run_variant("C3_full", {"ivix_pause_threshold": 25.0, "enable_oversold_bounce": True}, universe))

    # Summary
    print(f"\n\n{'='*90}")
    print(f"  组合验证总览 — {START} → {END}")
    if benchmark:
        print(f"  基准: {benchmark['return_pct']:+.2f}% Sharpe={benchmark['sharpe']:+.3f}")
    print(f"{'='*90}")
    print(f"  {'变体':<16} {'净收益':>8} {'MDD':>6} {'Sharpe':>7} {'超额':>7} {'交易':>5} {'反弹':>5}")
    for r in results:
        excess = r["return_pct_net"] - (benchmark["return_pct"] if benchmark else 0)
        marker = " 🏆" if r == max(results, key=lambda x: x["sharpe_real"]) else ""
        print(f"  {r['label']:<16} {r['return_pct_net']:>+7.2f}% {r['max_drawdown_pct']:>5.1f}% "
              f"{r['sharpe_real']:>+7.3f} {excess:>+6.1f}% {r['n_trades']:>5} {r['n_bounce_buys']:>5}{marker}")

    print(f"\n  年度对比:")
    all_years = sorted({yr for r in results for yr in r.get("annual_returns", {})})
    hdr = f"  {'变体':<16} " + " ".join(f"{y:>8}" for y in all_years)
    print(hdr)
    for r in results:
        vals = " ".join(f"{r['annual_returns'].get(y, 0):>+7.1f}%" for y in all_years)
        print(f"  {r['label']:<16} {vals}")

    out_path = "logs/combo_final.json"
    with open(out_path, "w") as f:
        json.dump({"start": START, "end": END, "benchmark": benchmark, "results": results},
                  f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  结果: {out_path}")


if __name__ == "__main__":
    main()
