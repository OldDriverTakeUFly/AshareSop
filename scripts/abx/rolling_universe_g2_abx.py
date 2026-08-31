"""A/B: 滚动宇宙 G2 回测 — 实验0010, 盘中线切换 G2 信号的前置口径验证.

背景: G2 生产基线(gx_G2_bull60)的五年验证用的是 2021-01-04 冻结的 top200 宇宙;
实盘/盘中线必须用滚动宇宙(T-1 成交额 top200)。切换前须验证结论不翻转.
(2026-08-30 立项, 用户批准"直接用G2筛选买入池"路线的第四步前置实验)

变体 (基线 = 冻结宇宙 G2, +126.40%/MDD15.6%/Sharpe1.081, 不重跑):
  R_q200 — 季度滚动 top200(主变体, 实盘口径)
  R_m200 — 月度滚动 top200(敏感性: 滚动频率)
  R_q300 — 季度滚动 top300(敏感性: 池宽)
工程要点: 每段宇宙 = 滚动 top-N ∪ 当前持仓(持仓必须保留因子分, 否则
动量崩塌卖出对跌出宇宙的持仓永不触发, 持仓卡死).

判定线 (事前, 2026-08-30 定稿):
  采纳(滚动宇宙可用于实盘): R_q200 Sharpe>1.0 且 收益>=100%(冻结口径~80%)
    且 MDD<=17% 且 无一年倒退>5pp(vs 冻结口径年度 32.9/19.3/16.7/-1.2/18.6/4.4)
  否决: R_q200 Sharpe<0.9 或 MDD>18% 或 任一年倒退>8pp
  中间地带: 条件采纳(实盘可用+季度监控宇宙质量)
  敏感性(支持证据, 非硬线): R_m200/R_q300 Sharpe>=0.9 且年度方向与 R_q200 大体一致
逐变体完成即落盘 (长回测运行规范#2). 烟测: --start 20210104 --end 20210331 --variants R_q200.
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

START_DEFAULT, END_DEFAULT = "20210104", "20260731"
INITIAL_CAPITAL = 1_000_000
OUT_PATH = "logs/abx/rolling_universe_g2_abx.json"

# 与 gx_G2_bull60 完全一致的生产基线 (G2 已固化)
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

VARIANTS = [
    ("R_q200", dict(top_n=200, freq="Q")),
    ("R_m200", dict(top_n=200, freq="M")),
    ("R_q300", dict(top_n=300, freq="Q")),
]

BASELINE = {"label": "baseline_frozen_G2", "return_pct": 126.40,
            "max_drawdown_pct": 15.6, "sharpe_real": 1.081, "n_trades": 1043,
            "annual_returns": {"2021": 32.9, "2022": 19.3, "2023": 16.7,
                                "2024": -1.2, "2025": 18.6, "2026": 4.4}}


def trading_days(start: str, end: str) -> list[str]:
    with get_market_conn() as c:
        rows = c.execute(
            "SELECT trade_date FROM index_daily WHERE ts_code='000001.SH' "
            "AND trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
            (start, end)).fetchall()
    return [r[0] for r in rows]


def prev_trading_day(day: str) -> str | None:
    with get_market_conn() as c:
        row = c.execute(
            "SELECT MAX(trade_date) FROM index_daily WHERE ts_code='000001.SH' "
            "AND trade_date < ?", (day,)).fetchone()
    return row[0] if row and row[0] else None


def rolling_universe(ref_date: str, top_n: int) -> list[str]:
    with get_market_conn() as c:
        rows = c.execute(
            "SELECT ts_code FROM daily_price WHERE trade_date=? "
            "AND close > 0 AND vol > 0 ORDER BY amount DESC LIMIT ?",
            (ref_date, top_n)).fetchall()
    return [r[0] for r in rows]


def segments(days: list[str], freq: str) -> list[tuple[str, str]]:
    """按季/月切 [start,end] 段列表."""
    key = (lambda d: d[:6]) if freq == "M" else (lambda d: d[:4] + "Q" + str((int(d[4:6]) - 1) // 3 + 1))
    out: list[tuple[str, str]] = []
    s = days[0]
    cur = key(days[0])
    prev_d = days[0]
    for d in days[1:]:
        k = key(d)
        if k != cur:
            out.append((s, prev_d))
            cur = k
            s = d
        prev_d = d
    out.append((s, days[-1]))
    return out


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


def dump(results):
    with open(OUT_PATH, "w") as f:
        json.dump({"results": results}, f, indent=2, ensure_ascii=False, default=str)


def run_variant(label, cfg, start, end):
    print(f"\n{'='*70}\n  {label}: {cfg}\n{'='*70}", flush=True)
    account = reset_account(f"ru_{label}")
    strategy = FactorThresholdStrategy(**BASE)
    days = trading_days(start, end)
    segs = segments(days, cfg["freq"])
    t0 = time.time()
    turnover_log = []
    prev_uni: set = set()
    for i, (s, e) in enumerate(segs, 1):
        ref = prev_trading_day(s) or s
        uni = rolling_universe(ref, cfg["top_n"])
        held = {p.ts_code for p in account.get_positions()}
        uni_codes = sorted(set(uni) | held)
        fresh = len(set(uni) - prev_uni) if prev_uni else -1
        if prev_uni:
            turnover_log.append({"seg": s, "fresh": fresh, "n": len(uni)})
        prev_uni = set(uni)
        print(f"  [段{i}/{len(segs)}] {s}→{e} ref={ref} "
              f"宇宙={len(uni)}(含持仓{len(held)}) 新进={fresh if fresh >= 0 else '-'}",
              flush=True)
        run_backfill_auto(account, strategy, s, e,
                          universe_codes=uni_codes, scoring_frequency=1)

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
    n_buys = sum(1 for t in trades if t.action == "BUY")

    account.close()
    result = {
        "label": label, "top_n": cfg["top_n"], "freq": cfg["freq"],
        "return_pct": round(ret, 2), "max_drawdown_pct": round(mdd, 2),
        "sharpe_real": round(sharpe, 3), "n_trades": len(trades), "n_buys": n_buys,
        "elapsed_hr": round((time.time() - t0) / 3600, 1),
        "annual_returns": year_rets,
        "universe_turnover": turnover_log,
    }
    print(f"  → {label}: ret={ret:+.2f}% MDD={mdd:.1f}% Sharpe={sharpe:+.3f} "
          f"笔数={len(trades)} ({(time.time()-t0)/3600:.1f}h)", flush=True)
    print(f"    年度: {year_rets}", flush=True)
    return result


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=START_DEFAULT)
    ap.add_argument("--end", default=END_DEFAULT)
    ap.add_argument("--variants", default="R_q200,R_m200,R_q300",
                    help="逗号分隔变体名(烟测只跑一个)")
    args = ap.parse_args()

    print(f"\n{'='*80}")
    print(f"  滚动宇宙 G2 A/B — {args.start} → {args.end}  (基线=冻结宇宙G2: +126.4%/1.081)")
    print(f"{'='*80}\n")

    results = [BASELINE]
    dump(results)
    for label, cfg in VARIANTS:
        if label not in args.variants.split(","):
            continue
        results.append(run_variant(label, cfg, args.start, args.end))
        dump(results)

    print(f"\n{'='*95}")
    print(f"  滚动宇宙 A/B 总览")
    print(f"{'='*95}")
    print(f"  {'变体':<16} {'收益%':>9} {'MDD%':>7} {'Sharpe':>8} {'笔数':>6}")
    for r in sorted(results, key=lambda x: -x["sharpe_real"]):
        print(f"  {r['label']:<16} {r['return_pct']:>+8.2f}% {r['max_drawdown_pct']:>6.1f}% "
              f"{r['sharpe_real']:>+8.3f} {r.get('n_trades', '-'):>6}")
    all_yr = sorted({y for r in results for y in r.get("annual_returns", {})})
    for r in results:
        vals = " ".join(f"{r.get('annual_returns', {}).get(y, 0):>+7.1f}%" for y in all_yr)
        print(f"  {r['label']:<16} {vals}")
    dump(results)
    print(f"\n  结果: {OUT_PATH}")


if __name__ == "__main__":
    main()
