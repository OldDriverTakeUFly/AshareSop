"""随机窗口×随机票池截断 测试台 (因子稳健性验证新标准, 2026-08-19 起).

方法论 (实验日志 0003, 用户提出):
  传统五年全期 A/B 有两重过拟合风险——对特定时间窗口、对特定 top-200 票池。
  新标准: 随机抽时间窗 (60-180 交易日) × 随机截断票池 (60%-100%),
  对照 = 截断后票池的等权持有收益 (自算, 非现成指数) + 上证指数。

  因子真正有效 = 在随机窗×随机池的组合上稳定跑赢截断池基准,
  而不是只在全期全池上跑赢。

用法 (因子 A/B 示例, 改 VARIANTS):
  VARIANTS = [
      ("baseline", {}),
      ("new_factor", dict(some_param=1.0)),
  ]
  每次试验内所有变体共用同一窗口+同一截断池 (配对设计)。

基准计算:
  截断池等权指数 = 池内每只股票在窗口内首日归一化到 1.0, 逐日取均值 → 池指数曲线
  (停牌缺口用窗口内最近可得价格; 窗口首日无价的股票剔除)。
"""
import os, sys, time, json, sqlite3, random
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

INITIAL_CAPITAL = 1_000_000
SEED = 2026
TRIALS = 16
WINDOW_RANGE = (60, 180)    # 交易日
POOL_FRACTION = (0.60, 1.00)  # 截断后保留比例

# ── 变体定义: 因子 A/B 在这里改 (共用同一窗口/池, 配对设计) ──
# 当前 = 首轮校准: 生产配置 (含G2) 单变体 vs 截断池基准
VARIANTS = [
    ("production", {}),
]

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
    bull_relaxed_buy_momentum=60.0,  # G2 已固化, 生产默认
)


def build_full_universe():
    with get_market_conn() as c:
        rows = c.execute(
            "SELECT ts_code FROM daily_price WHERE trade_date='20210104' "
            "AND close > 0 AND vol > 0 ORDER BY amount DESC LIMIT 200").fetchall()
    return [r[0] for r in rows]


def load_trading_dates():
    conn = sqlite3.connect("storage/database/market_data.db")
    rows = conn.execute(
        "SELECT trade_date FROM index_daily WHERE ts_code='000001.SH' "
        "AND trade_date >= '20210104' AND trade_date <= '20260731' ORDER BY trade_date"
    ).fetchall()
    idx_close = dict(conn.execute(
        "SELECT trade_date, close FROM index_daily WHERE ts_code='000001.SH' "
        "AND trade_date >= '20210104' AND trade_date <= '20260731'").fetchall())
    conn.close()
    return [r[0] for r in rows], idx_close


def draw_trials(dates, universe, n_trials):
    rng = random.Random(SEED)
    trials = []
    used = []
    for _ in range(n_trials):
        for _try in range(200):
            ln = rng.randint(*WINDOW_RANGE)
            s = rng.randrange(0, len(dates) - ln)
            d0, d1 = dates[s], dates[s + ln - 1]
            frac = rng.uniform(*POOL_FRACTION)
            k = max(30, int(len(universe) * frac))
            pool = tuple(sorted(rng.sample(universe, k)))
            # 同窗口重叠过多则重抽 (允许部分重叠, 避免全同)
            if any(abs(s - u) < 40 for u in used):
                continue
            used.append(s)
            trials.append({"d0": d0, "d1": d1, "pool": pool, "n_days": ln})
            break
    return trials


def pool_benchmark(pool, d0, d1):
    """截断池等权指数: 每股窗口内首日归一, 逐日均值 → (total_ret%, mdd%)."""
    conn = sqlite3.connect("storage/database/market_data.db")
    ph = ",".join("?" * len(pool))
    rows = conn.execute(
        f"SELECT ts_code, trade_date, close FROM daily_price "
        f"WHERE ts_code IN ({ph}) AND trade_date BETWEEN ? AND ? AND close > 0 "
        f"ORDER BY trade_date", (*pool, d0, d1)).fetchall()
    conn.close()
    by_date = {}
    base_px = {}
    for code, td, close in rows:
        if code not in base_px:
            base_px[code] = close  # 该股窗口内首个可得价
        by_date.setdefault(td, []).append(close / base_px[code])
    if not by_date:
        return None, None, 0
    curve = [(td, np.mean(v)) for td, v in sorted(by_date.items())]
    vals = np.array([v for _, v in curve])
    ret = (vals[-1] - 1) * 100
    peak = np.maximum.accumulate(vals)
    mdd = float(((peak - vals) / peak).max() * 100)
    return round(float(ret), 2), round(mdd, 2), len(base_px)


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


def run_variant(label, d0, d1, pool, extra):
    account = reset_account(f"rw_{label}")
    strategy = FactorThresholdStrategy(**{**BASE, **extra})
    run_backfill_auto(account, strategy, d0, d1,
                      universe_codes=list(pool), scoring_frequency=1)
    nav = [r.total_equity for r in account.get_nav_history()]
    trades = account.get_trades()
    account.close()
    ret = (nav[-1] / INITIAL_CAPITAL - 1) * 100 if nav else 0.0
    peak, mdd = 0, 0
    for v in nav:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak * 100 if peak > 0 else 0)
    return {"ret": round(ret, 2), "mdd": round(mdd, 2),
            "n_buys": sum(1 for t in trades if t.action == "BUY")}


def main():
    print(f"\n{'=' * 92}")
    print(f"  随机窗口×随机票池截断 测试台 — {TRIALS} trials, seed={SEED}")
    print(f"  变体: {[v[0] for v in VARIANTS]} | 对照: 截断池等权基准 + 上证指数")
    print(f"{'=' * 92}")

    universe = build_full_universe()
    dates, idx_close = load_trading_dates()
    trials = draw_trials(dates, universe, TRIALS)

    print(f"\n  抽样结果:")
    print(f"  {'#':<3} {'窗口':<21} {'天数':>4} {'池规模':>5}")
    for i, t in enumerate(trials):
        print(f"  {i + 1:<3} {t['d0']}→{t['d1']} {t['n_days']:>4} {len(t['pool']):>5}", flush=True)

    results = []
    for i, t in enumerate(trials):
        t0 = time.time()
        pool_ret, pool_mdd, pool_n = pool_benchmark(t["pool"], t["d0"], t["d1"])
        idx_ret = (idx_close[t["d1"]] / idx_close[t["d0"]] - 1) * 100
        rec = {"trial": i + 1, "d0": t["d0"], "d1": t["d1"], "n_days": t["n_days"],
               "pool_size": len(t["pool"]), "pool_ret": pool_ret, "pool_mdd": pool_mdd,
               "idx_ret": round(idx_ret, 2)}
        for name, extra in VARIANTS:
            rec[name] = run_variant(f"{i + 1:02d}_{name}", t["d0"], t["d1"], t["pool"], extra)
        results.append(rec)
        v0 = VARIANTS[0][0]
        print(f"  [{i + 1:02d}] {t['d0']}→{t['d1']} 池{len(t['pool'])}: "
              f"策略{rec[v0]['ret']:+.1f}% vs 池{pool_ret:+.1f}% vs 指数{idx_ret:+.1f}% "
              f"({time.time() - t0:.0f}s)", flush=True)

    # 汇总
    print(f"\n\n{'=' * 100}")
    print(f"  总览 (策略 vs 截断池基准 vs 上证)")
    print(f"{'=' * 100}")
    print(f"  {'#':<3} {'窗口':<21} {'池':>4} {'策略':>8} {'池EW':>8} {'指数':>8} {'超额vs池':>8} {'超额vs指数':>9}")
    for r in results:
        v0 = VARIANTS[0][0]
        print(f"  {r['trial']:<3} {r['d0']}→{r['d1']} {r['pool_size']:>4} "
              f"{r[v0]['ret']:>+7.1f}% {r['pool_ret']:>+7.1f}% {r['idx_ret']:>+7.1f}% "
              f"{r[v0]['ret'] - r['pool_ret']:>+7.1f} {r[v0]['ret'] - r['idx_ret']:>+8.1f}")

    v0 = VARIANTS[0][0]
    wins_pool = sum(1 for r in results if r[v0]["ret"] > r["pool_ret"])
    wins_idx = sum(1 for r in results if r[v0]["ret"] > r["idx_ret"])
    avg_ex_pool = np.mean([r[v0]["ret"] - r["pool_ret"] for r in results])
    avg_ex_idx = np.mean([r[v0]["ret"] - r["idx_ret"] for r in results])
    med_ex_pool = np.median([r[v0]["ret"] - r["pool_ret"] for r in results])
    print(f"\n  策略 vs 截断池: 胜 {wins_pool}/{len(results)}, 均值超额 {avg_ex_pool:+.2f}pp, 中位 {med_ex_pool:+.2f}pp")
    print(f"  策略 vs 上证指数: 胜 {wins_idx}/{len(results)}, 均值超额 {avg_ex_idx:+.2f}pp")

    with open("logs/abx/random_window_harness.json", "w") as f:
        json.dump({"seed": SEED, "trials": TRIALS, "variants": [v[0] for v in VARIANTS],
                   "results": results}, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  结果: logs/abx/random_window_harness.json")


if __name__ == "__main__":
    main()
