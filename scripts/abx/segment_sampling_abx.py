"""抽样验证: G2(已固化) vs 前G2基线 在分状态时间段的稳定性对比.

用户要求 (2026-08-19): 固化后抽样检测——牛市段/熊市段/牛熊切换段/随机段,
看 G2 是否总是优于基线, 还是只在平均意义上占优.

选段规则 (事前程序化, 防止挑数字):
  - 全部 80 交易日窗口按步长 10 天滑动, 计算每窗 bull%/bear%/MA200上%/regime翻转数
  - 牛市段: bull%×MA200上% 得分 top3 (互不重叠)
  - 熊市段: bear% top3 (互不重叠, 且与已选段不重叠)
  - 切换段: regime 翻转密度 top3 (互不重叠, 与已选不重叠)
  - 随机段: seed=42 抽 4 段 (60-120 交易日), 与已选不重叠
对照: base = bull_relaxed_buy_momentum=0.0 (前G2基线), g2 = 60.0 (生产默认)
不变量检查: 纯熊段 (无 bull&MA200上 日) 两变体交易应完全一致.
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
WINDOW = 80      # 常规段长度 (交易日)
STEP = 10        # 滑动步长
SEED = 42

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


# ── 选段 ─────────────────────────────────────────────────────────────────


def select_segments():
    conn = sqlite3.connect("storage/database/market_data.db")
    rows = conn.execute(
        "SELECT trade_date, close FROM index_daily WHERE ts_code='000001.SH' "
        "ORDER BY trade_date").fetchall()
    conn.close()
    dates = [r[0] for r in rows]
    closes = np.array([r[1] for r in rows], float)

    from davis_analyzer.market_regime import get_market_regime
    regime = {}
    for d in dates:
        if d >= "20210101":
            regime[d] = get_market_regime(d)
    ma200 = {}
    for i, d in enumerate(dates):
        if i >= 199:
            ma200[d] = bool(closes[i] > closes[i - 199:i + 1].mean())

    idx_all = [i for i, d in enumerate(dates) if d in regime]
    wins = []
    for start in range(0, len(idx_all) - WINDOW, STEP):
        ii = idx_all[start:start + WINDOW]
        ds = [dates[i] for i in ii]
        bull = np.mean([regime[d] == "bull" for d in ds])
        bear = np.mean([regime[d] == "bear" for d in ds])
        mab = np.mean([ma200.get(d, False) for d in ds])
        flips = sum(regime[ds[k]] != regime[ds[k - 1]] for k in range(1, len(ds)))
        wins.append({
            "i0": ii[0], "i1": ii[-1], "d0": ds[0], "d1": ds[-1],
            "bull": bull, "bear": bear, "ma200": mab, "flips": flips,
            "relax_days": int(sum(1 for d in ds if regime[d] == "bull" and ma200.get(d))),
        })

    def overlap(a, b):
        return not (a["i1"] < b["i0"] or b["i1"] < a["i0"])

    picked = []

    def take_top(score_fn, n, tag):
        ranked = sorted(wins, key=lambda w: -score_fn(w))
        got = 0
        for w in ranked:
            if any(overlap(w, p) for p in picked):
                continue
            w["type"] = tag
            picked.append(w)
            got += 1
            if got >= n:
                break

    take_top(lambda w: w["bull"] * w["ma200"], 3, "牛市段")
    take_top(lambda w: w["bear"] * (1 - w["ma200"]), 3, "熊市段")
    take_top(lambda w: w["flips"] * (w["bull"] + w["bear"]), 3, "切换段")

    rng = random.Random(SEED)
    tries = 0
    while sum(1 for p in picked if p["type"] == "随机段") < 4 and tries < 500:
        tries += 1
        ln = rng.randint(60, 120)
        s = rng.randrange(0, len(idx_all) - ln)
        ii = idx_all[s:s + ln]
        w = {
            "i0": ii[0], "i1": ii[-1], "d0": dates[ii[0]], "d1": dates[ii[-1]],
            "bull": np.mean([regime[dates[i]] == "bull" for i in ii]),
            "bear": np.mean([regime[dates[i]] == "bear" for i in ii]),
            "ma200": np.mean([ma200.get(dates[i], False) for i in ii]),
            "flips": sum(regime[dates[i]] != regime[dates[i - 1]] for i in ii[1:]),
            "relax_days": sum(1 for i in ii if regime[dates[i]] == "bull" and ma200.get(dates[i])),
        }
        if any(overlap(w, p) for p in picked):
            continue
        w["type"] = "随机段"
        picked.append(w)

    picked.sort(key=lambda w: w["d0"])
    return picked, dates


# ── 回测 ─────────────────────────────────────────────────────────────────


def build_universe():
    with get_market_conn() as c:
        rows = c.execute(
            "SELECT ts_code FROM daily_price WHERE trade_date='20210104' "
            "AND close > 0 AND vol > 0 ORDER BY amount DESC LIMIT 200").fetchall()
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


def run_one(label, start, end, universe, relaxed):
    account = reset_account(f"seg_{label}")
    strategy = FactorThresholdStrategy(**{**BASE, "bull_relaxed_buy_momentum": relaxed})
    run_backfill_auto(account, strategy, start, end,
                      universe_codes=universe, scoring_frequency=1)
    nav_rows = account.get_nav_history()
    nav = np.array([r.total_equity for r in nav_rows], float)
    trades = account.get_trades()
    ret = (nav[-1] / INITIAL_CAPITAL - 1) * 100 if len(nav) else 0.0
    rets = nav[1:] / nav[:-1] - 1 if len(nav) > 2 else np.array([0.0])
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 1e-9 else 0.0
    mdd = max_dd(list(nav))
    sig = [(t.trade_date, t.ts_code, t.action, t.shares) for t in trades]
    account.close()
    return {"ret": round(ret, 2), "mdd": round(mdd, 2), "sharpe": round(sharpe, 3),
            "n_buys": sum(1 for t in trades if t.action == "BUY"), "sig": sig}


def main():
    print(f"\n{'=' * 88}")
    print(f"  抽样验证: G2(bull60, 已固化) vs 前G2基线 — 分状态时间段对比")
    print(f"{'=' * 88}")

    segments, _ = select_segments()
    print(f"\n  选段结果 (程序化选择, seed={SEED}):")
    print(f"  {'类型':<5} {'区间':<20} {'bull%':>6} {'bear%':>6} {'MA200上%':>8} {'翻转':>4} {'可放松日':>6}")
    for s in segments:
        print(f"  {s['type']:<5} {s['d0']}→{s['d1']} {s['bull'] * 100:>5.0f}% {s['bear'] * 100:>5.0f}% "
              f"{s['ma200'] * 100:>7.0f}% {s['flips']:>4} {s['relax_days']:>6}", flush=True)

    universe = build_universe()
    results = []
    for k, s in enumerate(segments):
        tag = f"{k + 1:02d}_{s['type']}"
        print(f"\n  [{tag}] {s['d0']}→{s['d1']} 段内可放松日 {s['relax_days']}", flush=True)
        t0 = time.time()
        base = run_one(f"{tag}_base", s["d0"], s["d1"], universe, 0.0)
        g2 = run_one(f"{tag}_g2", s["d0"], s["d1"], universe, 60.0)
        identical = base["sig"] == g2["sig"]
        r = {"tag": tag, "type": s["type"], "d0": s["d0"], "d1": s["d1"],
             "relax_days": s["relax_days"], "bull": s["bull"], "bear": s["bear"],
             "base": base, "g2": g2, "identical": identical}
        results.append(r)
        d_ret = g2["ret"] - base["ret"]
        print(f"    base: {base['ret']:+.2f}%/{base['sharpe']:+.2f} | g2: {g2['ret']:+.2f}%/{g2['sharpe']:+.2f} "
              f"| Δret {d_ret:+.2f}pp | 买 {base['n_buys']}→{g2['n_buys']}"
              f"{' [交易一致]' if identical else ''} ({time.time() - t0:.0f}s)", flush=True)

    # 汇总
    print(f"\n\n{'=' * 100}")
    print(f"  抽样验证总览")
    print(f"{'=' * 100}")
    print(f"  {'段':<12} {'类型':<5} {'base收益':>9} {'g2收益':>9} {'Δret':>8} {'baseSharpe':>10} {'g2Sharpe':>9} {'g2胜?':>5}")
    for r in results:
        win = "✓" if r["g2"]["ret"] > r["base"]["ret"] else ("=" if r["g2"]["ret"] == r["base"]["ret"] else "✗")
        print(f"  {r['tag']:<12} {r['type']:<5} {r['base']['ret']:>+8.2f}% {r['g2']['ret']:>+8.2f}% "
              f"{r['g2']['ret'] - r['base']['ret']:>+7.2f} {r['base']['sharpe']:>+10.3f} "
              f"{r['g2']['sharpe']:>+9.3f} {win:>5}")
    wins = sum(1 for r in results if r["g2"]["ret"] > r["base"]["ret"])
    ties = sum(1 for r in results if r["g2"]["ret"] == r["base"]["ret"])
    print(f"\n  G2 胜 {wins} / 平 {ties} / 负 {len(results) - wins - ties} (共 {len(results)} 段)")
    for t in ("牛市段", "熊市段", "切换段", "随机段"):
        sub = [r for r in results if r["type"] == t]
        if sub:
            w = sum(1 for r in sub if r["g2"]["ret"] > r["base"]["ret"])
            avg = np.mean([r["g2"]["ret"] - r["base"]["ret"] for r in sub])
            print(f"    {t}: 胜{w}/{len(sub)}, 平均Δ {avg:+.2f}pp")

    with open("logs/abx/segment_sampling.json", "w") as f:
        json.dump({"seed": SEED, "results": [{k: v for k, v in r.items() if k != "sig"} for r in results]},
                  f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  结果: logs/abx/segment_sampling.json")


if __name__ == "__main__":
    main()
