"""0012: 双随机测试台 — 随机宇宙 × 随机窗口 (G2 优势的宇宙依赖性诊断).

立项脉络: 0010 证滚动成交额宇宙崩盘(-52.2%) → 0011 证池宽退化(200→500: +60→+20.8)
与数据版本漂移(-81.7pp) → 本实验把「宇宙成员」本身变成随机变量(0003 只随机化了
时间窗与固定池内截断, 从未随机化宇宙归属), 度量 G2 优势是否/在何种池型下稳健。

预注册判读纪律 (事前定稿, 2026-09-10, 跑之前不可改):
  1. 主判据 = 分状态日度配对差值(策略日收益 − 同池等权日收益), bull/bear/neutral
     分组, 各报胜率/均值/中位(pp/日); 原始混合胜率只显示不判读(0003 教训:
     暴露口径不对等, 上涨窗策略近持币必然输池)。
  2. 参与度条件化: 有持仓天数占比 <20% 的 trial 标记「暴露不足」, 单独归类。
  3. 结论形态(诊断, 非采纳否决): bear 配对差稳定为正(胜率>60%且均值>0)=防守优势
     宇宙稳健; bull 高参与且差值不为负=参与不损; 两臂对比=池宽决策; 全负=G2 优势
     宇宙依赖坐实, 实盘宇宙方案须回答「top200@2021 为何特殊」。
  4. 口径限制(0011 沿用): 退市股无财务数据过不了因子闸——执行器只打分活股子集
     (行为等价, 免空结果逐日重取 API), 退市股仍占池名额并计入池EW基准与指纹;
     即本测试的池 = 「名额挤占后」的真实池, 强平/退市计数照记。

设计: seed=2026; 窗口 60-180 交易日(起点间距<40 重抽, 与 0003 同); 池宽两臂
  u200/u500 共享同一窗口序列(跨臂配对); 池=在窗口起点有行情且上市满3年的
  L+D 全体中随机抽 N(含退市股, MARKET_DB_ATTACH_DELISTED=1); 排除显式记账。
  基线 = 同池等权持有(池EW, 自算, 退市冻结于末价); vol_ratio 研究侧按日期缓存。

用法: .venv/bin/python scripts/abx/random_universe_0012.py [--arm u200|u500|all]
      [--trials N] [--smoke](30-40日窗×1trial 机械验证用)
输出: logs/abx/random_universe_0012_{arm}.json 逐 trial 落盘
"""
import os, sys, time, json, sqlite3, random
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.environ["MARKET_DB_ATTACH_DELISTED"] = "1"   # 退市数据混入(研究上下文)
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="ERROR")

import numpy as np
import davis_analyzer.factors.market_regime as mr
mr._MA120_BEAR_THRESHOLD = -999.0

from stockhot.data_layer.market_db import get_connection as get_market_conn, DELISTED_DB_PATH
from stockhot.storage.database import init_database, DB_PATH
from davis_analyzer.paper_trading.account import PaperAccount
from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
from davis_analyzer.paper_trading.executor import run_backfill_auto
import davis_analyzer.paper_trading.executor as _ex
init_database()

SEED = 2026
TRIALS = 16
WINDOW_RANGE = (60, 180)
POOL_SIZES = {"u200": 200, "u500": 500}
INITIAL_CAPITAL = 1_000_000
OUT_DIR = "logs/abx"

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

# ── 研究侧 vol_ratio 按日期缓存(0011 修复的补充优化, 生产零改动) ──
_VR_CACHE: dict[str, float | None] = {}
_orig_vol_ratio = _ex._compute_vol_ratio_250
def _cached_vol_ratio(td: str):
    if td not in _VR_CACHE:
        _VR_CACHE[td] = _orig_vol_ratio(td)
    return _VR_CACHE[td]
_ex._compute_vol_ratio_250 = _cached_vol_ratio

_ALIVE: set[str] | None = None
def _alive_set() -> set[str]:
    global _ALIVE
    if _ALIVE is None:
        with get_market_conn() as c:
            _ALIVE = {r[0] for r in c.execute(
                "SELECT ts_code FROM stock_basic WHERE list_status='L'")}
    return _ALIVE


def load_trading_dates():
    with get_market_conn() as c:
        rows = c.execute(
            "SELECT trade_date FROM index_daily WHERE ts_code='000001.SH' "
            "AND trade_date >= '20210104' AND trade_date <= '20260731' ORDER BY trade_date"
        ).fetchall()
        idx_close = dict(c.execute(
            "SELECT trade_date, close FROM index_daily WHERE ts_code='000001.SH' "
            "AND trade_date >= '20210104' AND trade_date <= '20260731'").fetchall())
    return [r[0] for r in rows], idx_close


def eligible_pool(w_start: str) -> tuple[list[str], dict]:
    """窗口起点 W 的可抽总体: stock_basic ∩ [W-10, W] 有行情 ∩ 上市满3年(含退市股)."""
    from datetime import datetime, timedelta
    w10 = (datetime.strptime(w_start, "%Y%m%d") - timedelta(days=10)).strftime("%Y%m%d")
    age_cut = (datetime.strptime(w_start, "%Y%m%d") - timedelta(days=3 * 365)).strftime("%Y%m%d")
    with get_market_conn() as c:
        basics = {r[0]: r[1] for r in c.execute(
            "SELECT ts_code, list_date FROM stock_basic")}
        with_data = {r[0] for r in c.execute(
            "SELECT DISTINCT ts_code FROM daily_price "
            "WHERE trade_date BETWEEN ? AND ? AND close>0 AND vol>0", (w10, w_start))}
    no_data = len(with_data - set(basics))
    ok, too_young = [], 0
    for code in with_data & set(basics):
        if (basics[code] or "19000101") <= age_cut:
            ok.append(code)
        else:
            too_young += 1
    return sorted(ok), {"eligible": len(ok), "excluded_no_basic": no_data,
                        "excluded_young": too_young}


def pool_fingerprint(pool: list[str], w_start: str) -> dict:
    alive = _alive_set()
    delisted = [k for k in pool if k not in alive]
    # 窗口起点时的历史 ST 状态(namechange 时间线, 现名兜底)
    with get_market_conn() as c:
        cur_names = {r[0]: (r[1] or "") for r in c.execute(
            "SELECT ts_code, name FROM stock_basic")}
    latest = {}
    with sqlite3.connect(DELISTED_DB_PATH) as nc:
        ph = ",".join("?" * len(pool))
        rows = nc.execute(
            f"SELECT ts_code, name, start_date FROM namechange "
            f"WHERE ts_code IN ({ph}) AND start_date <= ?", (*pool, w_start)).fetchall()
    for code, nm, sd in rows:
        if code not in latest or sd > latest[code][1]:
            latest[code] = (nm, sd)
    st_codes = [k for k in pool
                if "ST" in (latest.get(k, (None,))[0] or cur_names.get(k, ""))
                or "退" in (latest.get(k, (None,))[0] or cur_names.get(k, ""))]
    return {"n": len(pool), "delisted": len(delisted), "delisted_codes": delisted,
            "st_at_start": len(st_codes)}


def pool_ew_curve(pool: list[str], d0: str, d1: str):
    """同池等权曲线: 每股窗口内首日归一, 逐日均值; 停牌/退市冻结于最近可得价."""
    with get_market_conn() as c:
        ph = ",".join("?" * len(pool))
        rows = c.execute(
            f"SELECT ts_code, trade_date, close FROM daily_price "
            f"WHERE ts_code IN ({ph}) AND trade_date BETWEEN ? AND ? AND close > 0 "
            f"ORDER BY trade_date", (*pool, d0, d1)).fetchall()
    by_date, base_px = {}, {}
    for code, td, close in rows:
        if code not in base_px:
            base_px[code] = close
        by_date.setdefault(td, {})[code] = close / base_px[code]
    if not by_date:
        return None, 0
    held, curve = {}, []
    for td in sorted(by_date):
        held.update(by_date[td])
        curve.append((td, float(np.mean(list(held.values())))))
    return curve, len(base_px)


def draw_window(rng: random.Random, dates: list[str], used: list[int]):
    ln = rng.randint(*WINDOW_RANGE)
    s = rng.randrange(0, len(dates) - ln)
    if any(abs(s - u) < 40 for u in used):
        return None
    used.append(s)
    return dates[s], dates[s + ln - 1], ln


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


def run_trial(arm: str, idx: int, d0: str, d1: str):
    n = POOL_SIZES[arm]
    elig, elig_stats = eligible_pool(d0)
    rng = random.Random(f"{SEED}:{arm}:{idx}")
    pool = sorted(rng.sample(elig, n))
    fp = pool_fingerprint(pool, d0)
    alive = _alive_set()
    run_pool = [k for k in pool if k in alive]  # 退市股无财务过不了闸, 不送打分(见docstring口径限制)

    curve, n_priced = pool_ew_curve(pool, d0, d1)
    ew_dates = [t for t, _ in curve]
    ew_vals = np.array([v for _, v in curve])
    ew_ret = (ew_vals[-1] - 1) * 100
    peak = np.maximum.accumulate(ew_vals)
    ew_mdd = float(((peak - ew_vals) / peak).max() * 100)
    ew_peak = (ew_vals.max() - 1) * 100

    account = reset_account(f"u0012_t{idx:02d}_{arm}")
    strategy = FactorThresholdStrategy(**BASE)
    run_backfill_auto(account, strategy, d0, d1, universe_codes=run_pool, scoring_frequency=1)
    nav_rows = account.get_nav_history()
    nav = np.array([r.total_equity for r in nav_rows], float)
    trades = account.get_trades()
    account.close()
    ret = (nav[-1] / INITIAL_CAPITAL - 1) * 100
    pk, mdd = nav[0], 0.0
    for v in nav:
        pk = max(pk, v)
        mdd = max(mdd, (pk - v) / pk * 100)
    s_rets = nav[1:] / nav[:-1] - 1
    sharpe = float(s_rets.mean() / s_rets.std() * np.sqrt(252)) if s_rets.std() > 1e-9 else 0.0
    invested = sum(1 for r in nav_rows if r.positions_value > 0) / max(len(nav_rows), 1)

    # 分状态日度配对差值: 策略日收益 vs 池EW日收益, 按交易日历对齐后逐日配对
    from davis_analyzer.factors.market_regime import get_market_regime
    s_daily = {nav_rows[i + 1].trade_date: float(s_rets[i]) for i in range(len(s_rets))}
    e_daily = {ew_dates[i + 1]: float(ew_vals[i + 1] / ew_vals[i] - 1) for i in range(len(ew_vals) - 1)}
    common = sorted(set(s_daily) & set(e_daily))
    by_state = {}
    for d in common:
        st = get_market_regime(d)
        by_state.setdefault(st, []).append(s_daily[d] - e_daily[d])
    paired = {st: {"n_days": len(ds),
                   "win_rate": round(float((np.array(ds) > 0).mean()) * 100, 1),
                   "mean_pp": round(float(np.mean(ds)) * 100, 2),
                   "median_pp": round(float(np.median(ds)) * 100, 2)}
              for st, ds in by_state.items()}

    return {
        "trial": idx, "arm": arm, "d0": d0, "d1": d1, "n_days": len(nav_rows),
        "eligibility": elig_stats, "fingerprint": fp, "run_pool_n": len(run_pool),
        "strategy": {"ret": round(ret, 2), "mdd": round(mdd, 2), "sharpe": round(sharpe, 3),
                     "peak_ret": round((nav.max() / INITIAL_CAPITAL - 1) * 100, 2),
                     "n_trades": len(trades),
                     "n_buys": sum(1 for t in trades if t.action == "BUY"),
                     "participation": round(invested * 100, 1),
                     "n_delist_force_exits": sum(1 for t in trades if "退市强平" in (t.signal_reason or ""))},
        "pool_ew": {"ret": round(float(ew_ret), 2), "mdd": round(ew_mdd, 2),
                    "peak_ret": round(float(ew_peak), 2), "n_priced": n_priced},
        "paired_by_state": paired,
    }


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="all", choices=["u200", "u500", "all"])
    ap.add_argument("--trials", type=int, default=TRIALS)
    ap.add_argument("--smoke", action="store_true", help="30-40日窗×1trial 机械验证")
    args = ap.parse_args()
    global WINDOW_RANGE
    if args.smoke:
        WINDOW_RANGE = (30, 40)
        args.trials = 1

    dates, idx_close = load_trading_dates()
    arms = list(POOL_SIZES) if args.arm == "all" else [args.arm]
    with get_market_conn() as c:
        data_ver = c.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()[0]

    for arm in arms:
        out_path = f"{OUT_DIR}/random_universe_0012_{arm}.json"
        print(f"\n{'=' * 92}\n  0012 双随机测试台 — {arm} 池宽{POOL_SIZES[arm]} × {args.trials} trials"
              f"  seed={SEED}  数据版本 daily_price@{data_ver}\n{'=' * 92}", flush=True)
        # 窗口序列跨臂共享(同一 trial 序号同窗口 → 池宽配对)
        w_rng = random.Random(f"{SEED}:window")
        windows, used = [], []
        while len(windows) < args.trials:
            w = draw_window(w_rng, dates, used)
            if w:
                windows.append(w)
        results = []
        for i, (d0, d1, ln) in enumerate(windows, 1):
            t0 = time.time()
            rec = run_trial(arm, i, d0, d1)
            results.append(rec)
            with open(out_path, "w") as f:  # 逐 trial 落盘
                json.dump({"seed": SEED, "arm": arm, "window_range": list(WINDOW_RANGE),
                           "data_version": data_ver, "results": results},
                          f, indent=2, ensure_ascii=False, default=str)
            s, p = rec["strategy"], rec["pool_ew"]
            print(f"  [{i:02d}] {d0}→{d1}({ln}d) 策略{s['ret']:+7.1f}% vs 池EW{p['ret']:+7.1f}% "
                  f"参与{s['participation']:.0f}% 退市{rec['fingerprint']['delisted']} "
                  f"ST{rec['fingerprint']['st_at_start']} ({time.time()-t0:.0f}s)", flush=True)
        wins = sum(1 for r in results if r["strategy"]["ret"] > r["pool_ew"]["ret"])
        ex = [r["strategy"]["ret"] - r["pool_ew"]["ret"] for r in results]
        print(f"\n  [参考口径,非判据] 胜 {wins}/{len(results)}, 均值超额 {np.mean(ex):+.1f}pp, "
              f"中位 {np.median(ex):+.1f}pp; 主判据=分状态配对(JSON)", flush=True)
    print("\n  完成: " + ", ".join(f"random_universe_0012_{a}.json" for a in arms))


if __name__ == "__main__":
    main()
