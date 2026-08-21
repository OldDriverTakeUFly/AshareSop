"""A/B: 牛市卖出豁免 — 实验0004, 攻击"脉冲急涨卖飞"(0003 T10/T13证据).

0003 测试台定位: 上涨窗 8/10 败于池, 含924的T10/T13对同暴露基准仍 -12~-16pp;
T13 逐笔证据: 入场准(9/20,9/25买入) 但高位放量/T+减仓在 +8~+17% 清仓, 吃不到主升段.

变体 (基线 = gx_G2_bull60 账户, 即当前生产, +126.40%/MDD15.6%/Sharpe1.081, 不重跑):
  E1_highvol — bull+MA200上 时仅豁免高位放量卖出
  E2_both    — 同时豁免高位放量 + T+减仓
判定线 (事前, 见实验日志0004):
  采纳: Sharpe>1.081 且 2024年度改善>=+3pp 且 无一年倒退>5pp 且 MDD<=17%
  否决: MDD>18% 或 2022倒退>5pp
逐变体完成即落盘 (长回测运行规范#2).
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
OUT_PATH = "logs/abx/bull_sell_exempt_abx.json"

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
    ("E1_highvol", dict(bull_highvol_sell_exempt=True)),
    ("E2_both", dict(bull_highvol_sell_exempt=True, bull_tplus_trim_exempt=True)),
]

BASELINE = {"label": "baseline_G2", "return_pct": 126.40, "max_drawdown_pct": 15.6,
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
    """长回测规范: 每变体完成即落盘."""
    with open(OUT_PATH, "w") as f:
        json.dump({"start": START, "end": END, "results": results},
                  f, indent=2, ensure_ascii=False, default=str)


def run_variant(label, extra, universe):
    print(f"\n{'='*70}\n  {label}: {extra}\n{'='*70}", flush=True)
    account = reset_account(f"ex_{label}")
    strategy = FactorThresholdStrategy(**{**BASE, **extra})
    t0 = time.time()
    run_backfill_auto(account, strategy, START, END,
                      universe_codes=universe, scoring_frequency=1)
    elapsed = time.time() - t0

    nav_rows = account.get_nav_history()
    nav = np.array([r.total_equity for r in nav_rows], float)
    ret = (nav[-1] / INITIAL_CAPITAL - 1) * 100
    mdd = max_dd(list(nav))
    rets = nav[1:] / nav[:-1] - 1
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 1e-9 else 0.0
    trades = account.get_trades()
    # 卖出结构 (豁免的直接观测)
    n_highvol = sum(1 for t in trades if t.action == "SELL" and "高位放量" in (t.signal_reason or ""))
    n_trim = sum(1 for t in trades if t.action == "SELL" and "T+减仓" in (t.signal_reason or ""))

    annual = {}
    for r in nav_rows:
        annual[r.trade_date[:4]] = r.total_equity
    year_rets = {}
    prev_end = INITIAL_CAPITAL
    for yr in sorted(annual):
        year_rets[yr] = round((annual[yr] / prev_end - 1) * 100, 2)
        prev_end = annual[yr]

    account.close()
    result = {
        "label": label, "extra": extra,
        "return_pct": round(ret, 2), "max_drawdown_pct": round(mdd, 2),
        "sharpe_real": round(sharpe, 3), "n_trades": len(trades),
        "n_highvol_sells": n_highvol, "n_tplus_trims": n_trim,
        "elapsed_hr": round(elapsed / 3600, 1),
        "annual_returns": year_rets,
    }
    print(f"  → {label}: ret={ret:+.2f}% MDD={mdd:.1f}% Sharpe={sharpe:+.3f} "
          f"高位放量卖{n_highvol} T+减仓{n_trim} ({elapsed/3600:.1f}h)", flush=True)
    print(f"    年度: {year_rets}", flush=True)
    return result


def main():
    print(f"\n{'='*80}")
    print(f"  牛市卖出豁免 A/B — {START} → {END}  (基线=当前生产G2: +126.4%/1.081)")
    print(f"{'='*80}\n")
    universe = build_universe(200)
    print(f"  Universe: {len(universe)} stocks\n")

    results = [BASELINE]
    dump(results)  # 基线先落盘
    for label, extra in VARIANTS:
        results.append(run_variant(label, extra, universe))
        dump(results)  # 每变体完成即落盘

    print(f"\n\n{'='*95}")
    print(f"  牛市卖出豁免 A/B 总览")
    print(f"{'='*95}")
    print(f"  {'变体':<14} {'收益%':>9} {'MDD%':>7} {'Sharpe':>8} {'高位放量':>7} {'T+减仓':>6}")
    for r in sorted(results, key=lambda x: -x["sharpe_real"]):
        print(f"  {r['label']:<14} {r['return_pct']:>+8.2f}% {r['max_drawdown_pct']:>6.1f}% "
              f"{r['sharpe_real']:>+8.3f} {r.get('n_highvol_sells', '-'):>7} {r.get('n_tplus_trims', '-'):>6}")
    print(f"\n  年度对比 (重点: 2024 是否修复>=+3pp, 其余年份无倒退>5pp):")
    all_yr = sorted({y for r in results for y in r.get("annual_returns", {})})
    for r in results:
        vals = " ".join(f"{r.get('annual_returns', {}).get(y, 0):>+7.1f}%" for y in all_yr)
        print(f"  {r['label']:<14} {vals}")
    dump(results)
    print(f"\n  结果: {OUT_PATH}")


if __name__ == "__main__":
    main()
