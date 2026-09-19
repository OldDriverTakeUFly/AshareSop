"""漂移哨兵 — 数据版本漂移的持续监控(0011/B2 三证人之后的长效机制).

背景: 缓存演化使同一口径回测数字漂移(实测样例: 22天-81.7pp / 10天+5.0pp)。
本哨兵每周用**完全固定的口径**(冻结宇宙×固定窗口×固定参数, 生产数据视图关)
重跑一次, 把数字轨迹记入台账——漂移量从此可观测、可比对, 并作为一切
「回测数字 vs 实盘数字」比较的置信区间参照; 漂移突然放大=数据层异常告警.

口径(定死, 改动须在 plan 文件追记): 宇宙=top200@20210104(生产表, 视图关) |
窗口=2023 全年(20230103→20231229) | 参数=G2 生产(gx_G2_bull60) | 账户=drift_sentinel 每次重建
台账: logs/abx/drift_sentinel_ledger.json 追加式
调度: crontab 周六 00:30(避周五锦标赛尾段)
用法: .venv/bin/python scripts/abx/drift_sentinel.py [--start --end 烟测用]
"""
import os, sys, time, json, sqlite3
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.environ.pop("MARKET_DB_ATTACH_DELISTED", None)  # 生产口径, 视图必须关
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="ERROR")

import numpy as np
import davis_analyzer.factors.market_regime as mr
mr._MA120_BEAR_THRESHOLD = -999.0

from stockhot.data_layer.market_db import get_connection as get_market_conn
from stockhot.storage.database import init_database, DB_PATH
from davis_analyzer.paper_trading.account import PaperAccount
from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
from davis_analyzer.paper_trading.executor import run_backfill_auto
init_database()

START, END = "20230103", "20231229"
INITIAL_CAPITAL = 1_000_000
ACCOUNT = "drift_sentinel"
LEDGER = "logs/abx/drift_sentinel_ledger.json"

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


def frozen_universe() -> list[str]:
    with get_market_conn() as c:
        rows = c.execute(
            "SELECT ts_code FROM daily_price WHERE trade_date='20210104' "
            "AND close>0 AND vol>0 ORDER BY amount DESC LIMIT 200").fetchall()
    return [r[0] for r in rows]


def reset_account():
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute("SELECT id FROM paper_accounts WHERE name=?", (ACCOUNT,)).fetchone()
        if row:
            aid = row[0]
            for tbl in ("paper_positions", "paper_trades", "paper_nav_history", "paper_shadow_trades"):
                c.execute(f"DELETE FROM {tbl} WHERE account_id=?", (aid,))
            c.execute("DELETE FROM paper_accounts WHERE id=?", (aid,))
            c.commit()
    return PaperAccount.create(name=ACCOUNT, strategy_name="factor_threshold",
                               initial_capital=INITIAL_CAPITAL, config={})


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=START)
    ap.add_argument("--end", default=END)
    args = ap.parse_args()

    with get_market_conn() as c:
        data_ver = c.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()[0]
    uni = frozen_universe()
    account = reset_account()
    t0 = time.time()
    run_backfill_auto(account, FactorThresholdStrategy(**BASE), args.start, args.end,
                      universe_codes=uni, scoring_frequency=1)
    nav_rows = account.get_nav_history()
    nav = np.array([r.total_equity for r in nav_rows], float)
    n_trades = len(account.get_trades())
    account.close()
    ret = (nav[-1] / INITIAL_CAPITAL - 1) * 100
    peak, mdd = nav[0], 0.0
    for v in nav:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak * 100)
    rets = nav[1:] / nav[:-1] - 1
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 1e-9 else 0.0

    entry = {"run_at": time.strftime("%F %T"), "data_version": data_ver,
             "window": [args.start, args.end], "n_days": len(nav_rows),
             "ret_pct": round(ret, 2), "mdd_pct": round(mdd, 2),
             "sharpe": round(sharpe, 3), "n_trades": n_trades,
             "elapsed_min": round((time.time() - t0) / 60, 1)}
    ledger = []
    try:
        ledger = json.load(open(LEDGER))
    except Exception:
        pass
    if ledger:
        base = ledger[0]
        last = ledger[-1]
        entry["delta_vs_baseline_pp"] = round(entry["ret_pct"] - base["ret_pct"], 2)
        entry["delta_vs_last_week_pp"] = round(entry["ret_pct"] - last["ret_pct"], 2)
    ledger.append(entry)
    with open(LEDGER, "w") as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False)
    print(f"[drift_sentinel] {entry['window']} 数据@{data_ver}: ret={entry['ret_pct']:+.2f}% "
          f"mdd={entry['mdd_pct']:.2f} sharpe={entry['sharpe']:+.3f} "
          f"({entry['elapsed_min']}min) "
          + (f"Δ基线={entry.get('delta_vs_baseline_pp','—')}pp "
             f"Δ上周={entry.get('delta_vs_last_week_pp','—')}pp" if len(ledger) > 1 else "(基线)"))
    print(f"  台账: {LEDGER} ({len(ledger)} 条)")


if __name__ == "__main__":
    main()
