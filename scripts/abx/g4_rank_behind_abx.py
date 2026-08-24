"""A/B: G4 放宽带排序降级 — 修复 G2 槽位挤占 (实验 0005).

0005 诊断段结论 (scripts/diag/g2_leftover_attr.py):
  - 2026 G2 独有入场 5 笔 -1.3万, 且挤掉 U0 原有赢家 5 笔 +6.3万 (共同 22 笔一致)
  - 2024 同病: G2 独有 +10.2万 被挤占机会成本 -6.1万 吃掉大半
  - 全期 G2 独有 65 笔 +43.8万 → 放宽本身净正, 病灶在排序

变体 (基线 = G2_bull60 生产默认, 账户 gx_G2_bull60 已存, 复用不重跑):
  G4_rank_behind — 同 G2 门槛 60, 但放宽带候选槽位竞争时稳定排在严门槛之后
"""
import os, sys, time, json
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

VARIANTS = [
    ("G4_rank_behind", dict(bull_relaxed_buy_momentum=60.0,
                            bull_relaxed_rank_behind=True)),
]

# G2 基线 (生产默认, gx_G2_bull60 账户, 确定性结果不重跑)
G2_BASELINE = {"label": "G2_bull60", "return_pct": 126.40, "max_drawdown_pct": 15.6,
               "sharpe_real": 1.081, "n_trades": 1043,
               "annual_returns": {"2021": 32.9, "2022": 19.3, "2023": 16.7,
                                   "2024": -1.2, "2025": 18.6, "2026": 4.4}}


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
    arr = np.array(nav, dtype=float)
    rets = arr[1:] / arr[:-1] - 1
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 1e-9 else 0.0
    trades = account.get_trades()

    annual = {}
    for r in nav_rows:
        annual[r.trade_date[:4]] = r.total_equity
    year_rets = {}
    prev_end = INITIAL_CAPITAL
    for yr in sorted(annual):
        year_rets[yr] = round((annual[yr] / prev_end - 1) * 100, 2)
        prev_end = annual[yr]

    account.close()
    return {
        "label": label, "extra": extra,
        "return_pct": round(total_ret, 2), "max_drawdown_pct": round(mdd, 2),
        "sharpe_real": round(sharpe, 3), "n_trades": len(trades),
        "elapsed_hr": round(elapsed / 3600, 1),
        "annual_returns": year_rets,
    }


def main():
    print(f"\n{'='*80}\n  G4 排序降级 A/B — {START} → {END}  (基线 G2: +126.40%/1.081)\n{'='*80}\n")
    universe = build_universe(200)
    print(f"  Universe: {len(universe)} stocks\n", flush=True)

    results = [G2_BASELINE]
    out_path = "logs/abx/g4_rank_behind_abx.json"
    for label, extra in VARIANTS:
        r = run_variant(label, extra, universe)
        results.append(r)
        with open(out_path, "w") as f:  # 逐变体落盘
            json.dump({"start": START, "end": END, "results": results},
                      f, indent=2, ensure_ascii=False, default=str)
        print(f"  → {label}: ret={r['return_pct']:+.2f}% MDD={r['max_drawdown_pct']:.1f}% "
              f"Sharpe={r['sharpe_real']:+.3f} trades={r['n_trades']}", flush=True)
        print(f"    年度: {r['annual_returns']}", flush=True)

    print(f"\n  结果: {out_path}")


if __name__ == "__main__":
    main()
