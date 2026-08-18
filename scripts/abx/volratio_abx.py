"""A/B: 量能比防御信号 5 年验证.

  W0_baseline   — C3 生产配置（无量能比防御）
  W1_volratio   — 量能比 > 1.2 时降仓 + 不追新仓

逻辑:
  vol_ratio = 近20日全市场日均成交额 / 近250日均值
  > 1.2 → 防御模式（effective_max 减半 + 暂停新买入）
  < 0.85 → 正常（缩量是动量甜点）
  其他 → 正常

5年月度IC = -0.214, Q1(0.73-0.84) 月均+3.64% 胜率90%
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
from davis_analyzer.paper_trading.executor import run_backfill_auto, DailyExecutor
init_database()

START = "20210104"
END = "20260731"
INITIAL_CAPITAL = 1_000_000
UNIVERSE_SIZE = 200

BASE = dict(
    max_positions=5, risk_stop_multiplier=0.70, sell_momentum=30,
    volume_weight=0.05, enable_volume_risk=True, pe_exemption_for_volume=True,
    max_intraday_amplitude=0.08, quality_weight=0.10, gap_weight=0.05,
    enable_event_filter=False, event_penalty_weight=0.0, tech_weight=0.0,
    enable_adaptive_sell=False, enable_dynamic_weight=False,
    amihud_weight=0.0, dragon_tiger_weight=0.0, repurchase_weight=0.0,
    low_vol_stop_exemption=0.0, holder_momentum_synergy=0.0,
    ivix_pause_threshold=25.0, enable_oversold_bounce=True,
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


# ── 量能比缓存（全局，避免每天重算）──
_vol_ratio_cache: dict[str, float] = {}

def _compute_vol_ratio(trade_date: str) -> float:
    """全市场近20日均量 / 近250日均量。"""
    if trade_date in _vol_ratio_cache:
        return _vol_ratio_cache[trade_date]
    try:
        with get_market_conn() as conn:
            rows = conn.execute(
                "SELECT trade_date, SUM(amount)/1e7 FROM daily_price "
                "WHERE trade_date <= ? AND amount > 0 "
                "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 250",
                (trade_date,),
            ).fetchall()
        if len(rows) < 250:
            return 1.0
        vols = [r[1] for r in rows]
        avg_20 = np.mean(vols[:20])
        avg_250 = np.mean(vols)
        ratio = avg_20 / avg_250 if avg_250 > 0 else 1.0
        _vol_ratio_cache[trade_date] = ratio
        return ratio
    except Exception:
        return 1.0


def run_variant(label, enable_volratio, universe):
    print(f"\n{'='*70}\n  {label}: volratio_defense={enable_volratio}\n{'='*70}", flush=True)
    _vol_ratio_cache.clear()

    account = reset_account(f"volratio_{label}")
    strategy = FactorThresholdStrategy(**BASE)

    # Monkey-patch _effective_max_positions 来实现量能比防御
    if enable_volratio:
        orig_eff = strategy._effective_max_positions
        def patched_eff(market_regime, vol_mult=1.0):
            base = orig_eff(market_regime, vol_mult)
            # 量能比防御：需要从外部传入当前 trade_date
            # 这里用一个 trick：strategy 知道当前日期（通过 evaluate 的 snapshot）
            # 但 _effective_max_positions 在 evaluate 里被调用时还不知道日期
            # 所以改在 run_day 层面做更干净
            return base
        # 不在 strategy 层面 patch，改在 executor 层面

    # 更干净的方式：patch run_day 里的 effective_max 计算
    # 但 run_day 内部逻辑复杂。最简单的方式：
    # patch _effective_max_positions，让它读一个全局变量
    import davis_analyzer.paper_trading.strategy as strat_mod

    if enable_volratio:
        orig_eff = FactorThresholdStrategy._effective_max_positions
        current_trade_date = [""]  # mutable container

        def patched_eff_max(self, market_regime, vol_mult=1.0):
            base = orig_eff(self, market_regime, vol_mult)
            td = current_trade_date[0]
            if td:
                vr = _compute_vol_ratio(td)
                if vr > 1.2 and base > 0:
                    return max(1, base // 2)  # 防御：降半仓
            return base

        # Patch evaluate to capture trade_date
        orig_eval = FactorThresholdStrategy.evaluate
        def patched_eval(self, positions, snapshot, total_equity):
            current_trade_date[0] = snapshot.trade_date
            return orig_eval(self, positions, snapshot, total_equity)

        FactorThresholdStrategy._effective_max_positions = patched_eff_max
        FactorThresholdStrategy.evaluate = patched_eval

    t0 = time.time()
    run_backfill_auto(account, strategy, START, END,
                      universe_codes=universe, scoring_frequency=1)
    elapsed = time.time() - t0

    # Restore
    if enable_volratio:
        FactorThresholdStrategy._effective_max_positions = orig_eff
        FactorThresholdStrategy.evaluate = orig_eval

    nav_rows = account.get_nav_history()
    nav_history = [r.total_equity for r in nav_rows]
    final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
    total_ret = (final_nav / INITIAL_CAPITAL - 1) * 100
    total_mdd = max_dd(nav_history)
    daily_rets = [r.daily_return for r in nav_rows if r.daily_return is not None]
    sharpe_real = real_sharpe(daily_rets)
    ann_vol = np.std(daily_rets, ddof=1) * np.sqrt(252) * 100 if daily_rets else 0
    trades = account.get_trades()

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
    result = {
        "label": label, "enable_volratio": enable_volratio,
        "return_pct": round(total_ret, 2), "max_drawdown_pct": round(total_mdd, 2),
        "sharpe_real": round(sharpe_real, 3), "ann_vol_pct": round(ann_vol, 1),
        "n_trades": len(trades), "elapsed_hr": round(elapsed / 3600, 1),
        "annual_returns": {yr: round(r, 2) for yr, r in year_rets.items()},
    }
    print(f"  → {label}: 收益={total_ret:+.2f}% MDD={total_mdd:.1f}% "
          f"Sharpe={sharpe_real:+.3f} 年化波动={ann_vol:.1f}% ({elapsed/3600:.1f}h)", flush=True)
    print(f"    年度: {year_rets}", flush=True)
    return result


def main():
    print(f"\n{'='*80}")
    print(f"  量能比防御信号 A/B — {START} → {END}")
    print(f"{'='*80}\n")

    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe: {len(universe)} stocks\n")

    results = []
    results.append(run_variant("W0_baseline", False, universe))
    results.append(run_variant("W1_volratio", True, universe))

    print(f"\n\n{'='*90}")
    print(f"  量能比防御 A/B 总览")
    print(f"{'='*90}")
    print(f"  {'变体':<18} {'收益':>8} {'MDD':>6} {'Sharpe':>7} {'年化波动':>8} {'交易':>5}")
    for r in sorted(results, key=lambda x: -x["sharpe_real"]):
        print(f"  {r['label']:<18} {r['return_pct']:>+7.2f}% {r['max_drawdown_pct']:>5.1f}% "
              f"{r['sharpe_real']:>+7.3f} {r['ann_vol_pct']:>7.1f}% {r['n_trades']:>5}")

    print(f"\n  年度对比:")
    all_yr = sorted({y for r in results for y in r.get("annual_returns", {})})
    for r in results:
        vals = " ".join(f"{r['annual_returns'].get(y,0):>+7.1f}%" for y in all_yr)
        print(f"  {r['label']:<18} {vals}")

    out_path = "logs/abx/volratio_abx.json"
    with open(out_path, "w") as f:
        json.dump({"start": START, "end": END, "results": results},
                  f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  结果: {out_path}")


if __name__ == "__main__":
    main()
