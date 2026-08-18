"""最终组合验证：全部有效因子叠加.

  F0_baseline    — HMM only（无任何增强因子）
  F1_production  — 当前生产默认配置（HMM+iVIX+超跌反弹+止损自适应+量能比防御）
  F2_full        — F1 + 海外宏观叠加（overseas overlay）

注：海外叠加已默认开启（executor._get_market_regime 用 overseas 版本），
F1 通过 monkey-patch 禁用海外来对比。
"""
import os, sys, time, json, sqlite3
import numpy as np
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="ERROR")

import davis_analyzer.market_regime as mr
mr._MA120_BEAR_THRESHOLD = -999.0

from stockhot.data_layer.market_db import get_connection as get_market_conn
from stockhot.storage.database import init_database, DB_PATH
from davis_analyzer.paper_trading.account import PaperAccount
from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
from davis_analyzer.paper_trading.executor import run_backfill_auto
import davis_analyzer.paper_trading.executor as exe
init_database()

START = "20210104"
END = "20260731"
INITIAL_CAPITAL = 1_000_000

# 生产配置（全部有效因子）
PRODUCTION = dict(
    max_positions=5, risk_stop_multiplier=0.70, sell_momentum=30,
    volume_weight=0.05, enable_volume_risk=True, pe_exemption_for_volume=True,
    max_intraday_amplitude=0.08, quality_weight=0.10, gap_weight=0.05,
    enable_event_filter=False, event_penalty_weight=0.0, tech_weight=0.0,
    enable_adaptive_sell=False, enable_dynamic_weight=False,
    amihud_weight=0.0, dragon_tiger_weight=0.0, repurchase_weight=0.0,
    low_vol_stop_exemption=0.0, holder_momentum_synergy=0.0,
    ivix_pause_threshold=25.0, enable_oversold_bounce=True,
    vol_ratio_defense=1.2,
    trailing_drawback=0.0, min_hold_days=0, quick_stop_pct=0.0,
    buy_momentum=70, buy_holder_min=40, buy_dividend_min=55,
    buy_forecast_min=70, buy_prosperity_min=45, min_secondary_dims=1,
)

# 最简配置（仅 HMM + 基本动量）
MINIMAL = dict(
    max_positions=5, risk_stop_multiplier=0.70, sell_momentum=30,
    volume_weight=0.0, enable_volume_risk=False, pe_exemption_for_volume=False,
    max_intraday_amplitude=0.0, quality_weight=0.0, gap_weight=0.0,
    enable_event_filter=False, event_penalty_weight=0.0, tech_weight=0.0,
    enable_adaptive_sell=False, enable_dynamic_weight=False,
    amihud_weight=0.0, dragon_tiger_weight=0.0, repurchase_weight=0.0,
    low_vol_stop_exemption=0.0, holder_momentum_synergy=0.0,
    ivix_pause_threshold=0.0, enable_oversold_bounce=False,
    vol_ratio_defense=0.0,
    trailing_drawback=0.0, min_hold_days=0, quick_stop_pct=0.0,
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
        if v > peak: peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > mdd: mdd = dd
    return mdd


def real_sharpe(daily_returns, annualization=252):
    if len(daily_returns) < 10: return 0.0
    arr = np.array(daily_returns)
    std = arr.std(ddof=1)
    if std < 1e-8: return 0.0
    return float(arr.mean() / std * np.sqrt(annualization))


def compute_benchmark(start, end):
    with get_market_conn() as c:
        for idx in ("000300.SH", "000001.SH"):
            rows = c.execute(
                "SELECT close FROM index_daily WHERE ts_code=? AND trade_date>=? AND trade_date<=? AND close>0 ORDER BY trade_date",
                (idx, start, end)).fetchall()
            if len(rows) > 10: break
    closes = [float(r[0]) for r in rows]
    daily_rets = [(closes[i]/closes[i-1]-1)*100 for i in range(1,len(closes)) if closes[i-1]>0]
    return {"index": idx, "return_pct": (closes[-1]/closes[0]-1)*100, "sharpe": real_sharpe(daily_rets)}


def run_variant(label, params, use_overseas, universe):
    print(f"\n{'='*70}\n  {label}: overseas={use_overseas}\n{'='*70}", flush=True)

    # 控制 overseas overlay
    orig_get_regime = exe._get_market_regime
    if not use_overseas:
        def regime_no_overseas(td):
            from davis_analyzer.market_regime import get_market_regime
            return get_market_regime(td)
        exe._get_market_regime = regime_no_overseas
    else:
        exe._get_market_regime = orig_get_regime

    account = reset_account(f"final_{label}")
    strategy = FactorThresholdStrategy(**params)

    t0 = time.time()
    run_backfill_auto(account, strategy, START, END,
                      universe_codes=universe, scoring_frequency=1)
    elapsed = time.time() - t0

    exe._get_market_regime = orig_get_regime

    nav_rows = account.get_nav_history()
    nav_history = [r.total_equity for r in nav_rows]
    final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
    total_ret = (final_nav / INITIAL_CAPITAL - 1) * 100
    total_mdd = max_dd(nav_history)
    daily_rets = [r.daily_return for r in nav_rows if r.daily_return is not None]
    sharpe_real = real_sharpe(daily_rets)
    ann_vol = np.std(daily_rets, ddof=1) * np.sqrt(252) if daily_rets else 0
    trades = account.get_trades()
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
        "label": label, "use_overseas": use_overseas,
        "return_pct": round(total_ret, 2), "max_drawdown_pct": round(total_mdd, 2),
        "sharpe_real": round(sharpe_real, 3), "ann_vol_pct": round(ann_vol*100, 1),
        "n_trades": len(trades), "n_bounce_buys": bounce_buys,
        "elapsed_hr": round(elapsed / 3600, 1),
        "annual_returns": {yr: round(r, 2) for yr, r in year_rets.items()},
    }
    print(f"  → {label}: ret={total_ret:+.2f}% MDD={total_mdd:.1f}% "
          f"Sharpe={sharpe_real:+.3f} vol={ann_vol*100:.1f}% "
          f"trades={len(trades)}(反弹{bounce_buys}) ({elapsed/3600:.1f}h)", flush=True)
    print(f"    年度: {year_rets}", flush=True)
    return result


def main():
    print(f"\n{'='*80}")
    print(f"  最终组合验证 — {START} → {END}")
    print(f"{'='*80}\n")

    universe = build_universe(200)
    benchmark = compute_benchmark(START, END)
    print(f"  Universe: {len(universe)} stocks")
    if benchmark:
        print(f"  基准 {benchmark['index']}: {benchmark['return_pct']:+.2f}% Sharpe={benchmark['sharpe']:+.3f}\n")

    results = []
    # F0: 最简基线（HMM only，无海外）
    results.append(run_variant("F0_minimal", MINIMAL, False, universe))
    # F1: 生产配置（无海外叠加）
    results.append(run_variant("F1_production", PRODUCTION, False, universe))
    # F2: 生产配置 + 海外叠加（完整配置）
    results.append(run_variant("F2_full_overseas", PRODUCTION, True, universe))

    print(f"\n\n{'='*90}")
    print(f"  最终组合验证总览 — {START} → {END}")
    if benchmark:
        print(f"  基准: {benchmark['return_pct']:+.2f}% Sharpe={benchmark['sharpe']:+.3f}")
    print(f"{'='*90}")
    print(f"  {'变体':<24} {'收益':>8} {'MDD':>6} {'Sharpe':>7} {'波动':>6} {'超额':>7} {'交易':>5}")
    for r in results:
        excess = r["return_pct"] - (benchmark["return_pct"] if benchmark else 0)
        print(f"  {r['label']:<24} {r['return_pct']:>+7.2f}% {r['max_drawdown_pct']:>5.1f}% "
              f"{r['sharpe_real']:>+7.3f} {r['ann_vol_pct']:>5.1f}% {excess:>+6.1f}% {r['n_trades']:>5}")

    print(f"\n  年度对比:")
    all_yr = sorted({y for r in results for y in r.get("annual_returns", {})})
    for r in results:
        vals = " ".join(f"{r['annual_returns'].get(y,0):>+7.1f}%" for y in all_yr)
        print(f"  {r['label']:<24} {vals}")

    print(f"\n  增量贡献:")
    if len(results) >= 3:
        f0, f1, f2 = results[0], results[1], results[2]
        print(f"    F0→F1 (生产因子):     ret {f0['return_pct']:+.1f}% → {f1['return_pct']:+.1f}%  "
              f"Sharpe {f0['sharpe_real']:+.3f} → {f1['sharpe_real']:+.3f}")
        print(f"    F1→F2 (海外叠加):     ret {f1['return_pct']:+.1f}% → {f2['return_pct']:+.1f}%  "
              f"Sharpe {f1['sharpe_real']:+.3f} → {f2['sharpe_real']:+.3f}")
        print(f"    F0→F2 (全部):         ret {f0['return_pct']:+.1f}% → {f2['return_pct']:+.1f}%  "
              f"Sharpe {f0['sharpe_real']:+.3f} → {f2['sharpe_real']:+.3f}")

    out_path = "logs/combo_final_v2.json"
    with open(out_path, "w") as f:
        json.dump({"start": START, "end": END, "benchmark": benchmark, "results": results},
                  f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  结果: {out_path}")


if __name__ == "__main__":
    main()
