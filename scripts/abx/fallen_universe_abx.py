"""A/B: 反弹专用「全市场暴跌池」vs 主池.

  U0_main_pool    — 反弹从主池(200只成交额股)选（当前默认）
  U1_fallen_100   — 反弹日扩展全市场最深100只暴跌股（排除ST）

executor 已内置 oversold_fallen_pool_size 参数（反弹触发日自动扩展候选池）。
全市场实证: <-20%跌幅 → +10.5%/20d 胜率78%（黄金区），主池难有这种深度。
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
init_database()

START = "20210104"
END = "20260731"
INITIAL_CAPITAL = 1_000_000

BASE = dict(
    max_positions=5, risk_stop_multiplier=0.70, sell_momentum=30,
    volume_weight=0.05, enable_volume_risk=True, pe_exemption_for_volume=True,
    max_intraday_amplitude=0.08, quality_weight=0.10, gap_weight=0.05,
    enable_event_filter=False, event_penalty_weight=0.0, tech_weight=0.0,
    enable_adaptive_sell=False, enable_dynamic_weight=False,
    amihud_weight=0.0, dragon_tiger_weight=0.0, repurchase_weight=0.0,
    low_vol_stop_exemption=0.0, holder_momentum_synergy=0.0,
    ivix_pause_threshold=25.0, enable_oversold_bounce=True,
    vol_ratio_defense=1.2, oversold_bounce_slots=1,
    oversold_candidate_min_drop=-3.0,
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


def run_variant(label, fallen_size, universe):
    print(f"\n{'='*70}\n  {label}: fallen_pool_size={fallen_size}\n{'='*70}", flush=True)
    account = reset_account(f"fp_{label}")
    params = {**BASE, "oversold_fallen_pool_size": fallen_size}
    strategy = FactorThresholdStrategy(**params)

    t0 = time.time()
    run_backfill_auto(account, strategy, START, END,
                      universe_codes=universe, scoring_frequency=1)
    elapsed = time.time() - t0

    nav_rows = account.get_nav_history()
    nav_history = [r.total_equity for r in nav_rows]
    final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
    total_ret = (final_nav / INITIAL_CAPITAL - 1) * 100
    total_mdd = max_dd(nav_history)
    daily_rets = [r.daily_return for r in nav_rows if r.daily_return is not None]
    sharpe_real = real_sharpe(daily_rets)
    trades = account.get_trades()

    bounce_buys = [t for t in trades if "超跌反弹" in (t.signal_reason or "") and t.action == "BUY"]

    holdings_pair = {}
    bounce_rets = []
    for t in trades:
        code = t.ts_code
        if t.action == 'BUY' and '超跌反弹' in (t.signal_reason or ''):
            holdings_pair[code] = (t.trade_date, t.price)
        elif t.action == 'SELL' and code in holdings_pair:
            buy = holdings_pair.pop(code)
            if buy[1] > 0:
                bounce_rets.append((t.price / buy[1] - 1) * 100)

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
    avg_bounce = np.mean(bounce_rets) if bounce_rets else 0
    result = {
        "label": label, "fallen_pool_size": fallen_size,
        "return_pct": round(total_ret, 2), "max_drawdown_pct": round(total_mdd, 2),
        "sharpe_real": round(sharpe_real, 3), "n_trades": len(trades),
        "n_bounce_buys": len(bounce_buys),
        "avg_bounce_ret": round(float(avg_bounce), 2),
        "elapsed_hr": round(elapsed / 3600, 1),
        "annual_returns": {yr: round(r, 2) for yr, r in year_rets.items()},
    }
    print(f"  → {label}: ret={total_ret:+.2f}% MDD={total_mdd:.1f}% "
          f"Sharpe={sharpe_real:+.3f} 反弹买{len(bounce_buys)} "
          f"笔均{avg_bounce:+.1f}% ({elapsed/3600:.1f}h)", flush=True)
    print(f"    年度: {year_rets}", flush=True)
    return result


def main():
    print(f"\n{'='*80}")
    print(f"  超跌股票池 A/B — {START} → {END}")
    print(f"{'='*80}\n")

    universe = build_universe(200)
    print(f"  主池 Universe: {len(universe)} stocks\n")

    results = []
    
    results.append(run_variant("U1_fallen_100", 100, universe))

    print(f"\n\n{'='*95}")
    print(f"  超跌股票池 A/B 总览")
    print(f"{'='*95}")
    print(f"  {'变体':<18} {'收益':>8} {'MDD':>6} {'Sharpe':>7} {'反弹买':>6} {'反弹笔均':>8}")
    for r in sorted(results, key=lambda x: -x["sharpe_real"]):
        print(f"  {r['label']:<18} {r['return_pct']:>+7.2f}% {r['max_drawdown_pct']:>5.1f}% "
              f"{r['sharpe_real']:>+7.3f} {r['n_bounce_buys']:>6} {r['avg_bounce_ret']:>+7.1f}%")

    print(f"\n  年度对比:")
    all_yr = sorted({y for r in results for y in r.get("annual_returns", {})})
    for r in results:
        vals = " ".join(f"{r['annual_returns'].get(y,0):>+7.1f}%" for y in all_yr)
        print(f"  {r['label']:<18} {vals}")

    out_path = "logs/abx/fallen_pool_abx.json"
    with open(out_path, "w") as f:
        json.dump({"start": START, "end": END, "results": results},
                  f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  结果: {out_path}")


if __name__ == "__main__":
    main()
