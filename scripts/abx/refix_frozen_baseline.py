"""0010 幸存者剪枝修复后的冻结宇宙基线重跑 (G2 生产配置, 口径镜像 gx_G2_bull60).

用法: UNIVERSE_SIZE=200|500 .venv/bin/python scripts/abx/refix_frozen_baseline.py
     [--start YYYYMMDD] [--end YYYYMMDD](烟测用)
脚本自设 MARKET_DB_ATTACH_DELISTED=1(须在首条 get_connection 前生效)——
宇宙构建与回测路径自动看到 退市行∪生产行 (market_db TEMP VIEW 钩子)。

输出: logs/abx/refix_frozen_u{N}.json; 账户 refix_frozen_u{N} 入口重建,
不覆盖 gx_G2_bull60 / backtest_5yr_u* 旧账户(旧剪枝口径结果保留对照)。
"""
import os, sys, time, json, sqlite3
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.environ["MARKET_DB_ATTACH_DELISTED"] = "1"
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="ERROR")

import numpy as np
import davis_analyzer.market_regime as mr
mr._MA120_BEAR_THRESHOLD = -999.0

from stockhot.data_layer.market_db import get_connection as get_market_conn
from stockhot.storage.database import init_database, DB_PATH
from davis_analyzer.paper_trading.account import PaperAccount
from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
from davis_analyzer.paper_trading.executor import run_backfill_auto
init_database()

START_DEFAULT, END_DEFAULT = "20210104", "20260731"
INITIAL_CAPITAL = 1_000_000
TOP_N = int(os.environ.get("UNIVERSE_SIZE", "200"))
OUT_PATH = f"logs/abx/refix_frozen_u{TOP_N}.json"
ACCOUNT = f"refix_frozen_u{TOP_N}"

# 与 gx_G2_bull60 完全一致的 G2 生产基线参数 (镜像 rolling_universe_g2_abx.BASE)
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
    bull_relaxed_buy_momentum=60.0,
)

# 旧剪枝口径对照 (gx_G2_bull60 / 0010 归档)
OLD_REF = {"label": f"pruned_frozen_u{TOP_N}_G2",
           "return_pct": 126.40 if TOP_N == 200 else None,
           "max_drawdown_pct": 15.6, "sharpe_real": 1.081,
           "annual": {"2021": 32.9, "2022": 19.3, "2023": 16.7,
                       "2024": -1.2, "2025": 18.6, "2026": 4.4}}


def frozen_universe() -> list[str]:
    """top-N @20210104 成交额排名 (经 TEMP VIEW 含退市股)."""
    with get_market_conn() as c:
        rows = c.execute(
            "SELECT ts_code FROM daily_price WHERE trade_date='20210104' "
            "AND close>0 AND vol>0 ORDER BY amount DESC LIMIT ?",
            (TOP_N,)).fetchall()
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
    peak, mdd = nav[0] if nav else 0, 0
    for v in nav:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > mdd: mdd = dd
    return mdd


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=START_DEFAULT)
    ap.add_argument("--end", default=END_DEFAULT)
    args = ap.parse_args()

    uni = frozen_universe()
    with get_market_conn() as c:
        alive = {r[0] for r in c.execute(
            "SELECT ts_code FROM stock_basic WHERE list_status='L'")}
    dead_in = [k for k in uni if k not in alive]
    print(f"[refix_frozen_u{TOP_N}] 宇宙 {len(uni)} 只, 其中退市股 {len(dead_in)} {dead_in}")
    print(f"  窗口 {args.start}→{args.end}  (旧剪枝口径: {OLD_REF['return_pct']})")

    account = reset_account(ACCOUNT)
    strategy = FactorThresholdStrategy(**BASE)
    t0 = time.time()
    run_backfill_auto(account, strategy, args.start, args.end,
                      universe_codes=uni, scoring_frequency=1)

    nav_rows = account.get_nav_history()
    nav = np.array([r.total_equity for r in nav_rows], float)
    ret = (nav[-1] / INITIAL_CAPITAL - 1) * 100
    mdd = max_dd(list(nav))
    rets = nav[1:] / nav[:-1] - 1
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 1e-9 else 0.0
    trades = account.get_trades()
    annual, year_rets, prev_end = {}, {}, INITIAL_CAPITAL
    for r in nav_rows:
        annual[r.trade_date[:4]] = r.total_equity
    for yr in sorted(annual):
        year_rets[yr] = round((annual[yr] / prev_end - 1) * 100, 2)
        prev_end = annual[yr]
    n_exits = sum(1 for t in trades if "退市强平" in (t.signal_reason or ""))

    result = {
        "label": f"refix_frozen_u{TOP_N}", "top_n": TOP_N,
        "window": [args.start, args.end],
        "return_pct": round(ret, 2), "max_drawdown_pct": round(mdd, 2),
        "sharpe_real": round(sharpe, 3), "n_trades": len(trades),
        "n_buys": sum(1 for t in trades if t.action == "BUY"),
        "n_delist_force_exits": n_exits,
        "universe_size": len(uni), "delisted_in_universe": dead_in,
        "annual_returns": year_rets,
        "old_pruned_reference": OLD_REF,
        "elapsed_hr": round((time.time() - t0) / 3600, 1),
    }
    with open(OUT_PATH, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    account.close()
    print(f"  → ret={ret:+.2f}% MDD={mdd:.1f}% Sharpe={sharpe:+.3f} "
          f"强平={n_exits} → {OUT_PATH}")


if __name__ == "__main__":
    main()
