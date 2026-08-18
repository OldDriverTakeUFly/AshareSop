"""A/B: 牛市信号扩容 — 攻击 2024-2025 牛市踏空 (-36pp 超额).

归因诊断 (docs/回测记录/实验日志/0001) 结论:
  - 2025 年指数 99% 时间在 MA200 上, HMM bull 占 25.5%, 但 bull 天平均暴露
    只有 1.32 格 (2021 年同条件 4.66 格) → 病因是动量≥70 买入信号在普涨牛里稀缺
  - 次因: HMM 在行情起点误判 bear (2024-09 924 行情 bear 37%/暴露 0.95 格)

变体 (基线 = fp_U0_main_pool, +89.80%/Sharpe 0.886, 同 universe 同配置不再重跑):
  G1_bull65    — bull 且指数>MA200 时买入动量门槛 70→65
  G2_bull60    — 同条件 70→60 (剂量对照)
  G3_ma200bear — 指数>MA200 时 bear 判定豁免为半仓 (不阻断开仓)
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

# 与 fallen_universe_abx.U0 完全一致的生产基线
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

VARIANTS = [
    ("G1_bull65", dict(bull_relaxed_buy_momentum=65.0)),
    ("G2_bull60", dict(bull_relaxed_buy_momentum=60.0)),
    ("G3_ma200bear", dict(ma200_bear_override=True)),
]

# U0 基线 (fp_U0_main_pool, 同 universe 同 BASE, 确定性结果)
U0_BASELINE = {"label": "U0_baseline", "return_pct": 89.80, "max_drawdown_pct": 14.02,
               "sharpe_real": 0.886, "n_trades": 1013}


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


def run_variant(label, extra, universe):
    print(f"\n{'='*70}\n  {label}: {extra}\n{'='*70}", flush=True)
    account = reset_account(f"gx_{label}")
    strategy = FactorThresholdStrategy(**{**BASE, **extra})

    t0 = time.time()
    run_backfill_auto(account, strategy, START, END,
                      universe_codes=universe, scoring_frequency=1)
    elapsed = time.time() - t0

    nav_rows = account.get_nav_history()
    nav = [r.total_equity for r in nav_rows]
    final_nav = nav[-1] if nav else INITIAL_CAPITAL
    total_ret = (final_nav / INITIAL_CAPITAL - 1) * 100
    mdd = max_dd(nav)
    # 真 Sharpe: 从净值推导 (与 U0 报告的 0.886 同口径)
    arr = np.array(nav, dtype=float)
    rets = arr[1:] / arr[:-1] - 1
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 1e-9 else 0.0
    trades = account.get_trades()

    annual = {}
    for r in nav_rows:
        yr = r.trade_date[:4]
        if yr not in annual:
            annual[yr] = r.total_equity
        annual[yr] = r.total_equity
    year_rets = {}
    prev_end = INITIAL_CAPITAL
    for yr in sorted(annual):
        year_rets[yr] = round((annual[yr] / prev_end - 1) * 100, 2)
        prev_end = annual[yr]

    account.close()
    result = {
        "label": label, "extra": extra,
        "return_pct": round(total_ret, 2), "max_drawdown_pct": round(mdd, 2),
        "sharpe_real": round(sharpe, 3), "n_trades": len(trades),
        "elapsed_hr": round(elapsed / 3600, 1),
        "annual_returns": year_rets,
    }
    print(f"  → {label}: ret={total_ret:+.2f}% MDD={mdd:.1f}% Sharpe={sharpe:+.3f} "
          f"trades={len(trades)} ({elapsed/3600:.1f}h)", flush=True)
    print(f"    年度: {year_rets}", flush=True)
    return result


def main():
    print(f"\n{'='*80}")
    print(f"  牛市信号扩容 A/B — {START} → {END}  (基线 U0: +89.80%/0.886)")
    print(f"{'='*80}\n")

    universe = build_universe(200)
    print(f"  Universe: {len(universe)} stocks\n")

    results = [U0_BASELINE]
    for label, extra in VARIANTS:
        results.append(run_variant(label, extra, universe))

    print(f"\n\n{'='*95}")
    print(f"  牛市扩容 A/B 总览")
    print(f"{'='*95}")
    print(f"  {'变体':<16} {'收益%':>9} {'MDD%':>7} {'Sharpe':>8} {'交易数':>7}")
    for r in sorted(results, key=lambda x: -x["sharpe_real"]):
        print(f"  {r['label']:<16} {r['return_pct']:>+8.2f}% {r['max_drawdown_pct']:>6.1f}% "
              f"{r['sharpe_real']:>+8.3f} {r['n_trades']:>7}")

    print(f"\n  年度对比 (重点看 2024/2025 是否修复, 2021-2023 是否倒退):")
    all_yr = sorted({y for r in results for y in r.get("annual_returns", {})})
    for r in results:
        vals = " ".join(f"{r.get('annual_returns', {}).get(y, 0):>+7.1f}%" for y in all_yr)
        print(f"  {r['label']:<16} {vals}")

    out_path = "logs/abx/bull_expand_abx.json"
    with open(out_path, "w") as f:
        json.dump({"start": START, "end": END, "results": results},
                  f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  结果: {out_path}")


if __name__ == "__main__":
    main()
