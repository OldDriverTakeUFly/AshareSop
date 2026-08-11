"""5 年消融实验 (Ablation Study) — 逐个移除优化，验证每个因子的真实贡献.

核心问题：当前生产配置 5 年回测 -3.38%，但这是所有优化叠加的结果。
某个优化可能反而拖累了整体表现。通过「逐个移除」可以定位真 alpha。

对比组（全部用相同的 5 年框架 + 200 只成交额 universe + freq=1）：

  A0 当前生产 (已跑)     — gap+qual+amp+HMM+T+减仓    → -3.38% (基线)
  A1 移除 T+减仓          — 关闭 T+0 仓位管理
  A2 移除 gap             — gap_weight=0
  A3 移除 quality         — quality_weight=0
  A4 移除 amplitude       — max_intraday_amplitude=0
  A5 最简策略 V1          — 纯动量，无量价/质量/缺口/振幅/HMM
  A6 放宽 T+减仓阈值      — t_trim_threshold=0.15 (8%→15%)

⚠️ 每个配置约 9 小时，建议后台串行跑（约 2.5 天）。
   可用 START_DATE/END_DATE 缩短验证。

Usage::

    cd /home/leo/Projects/CodeAgentDashboard

    # 全部 6 个配置（约 54 小时，建议周末跑）
    nohup python scripts/ablation_5yr.py > logs/ablation_5yr.log 2>&1 &

    # 快速验证（只跑 2022 熊市，每个约 1 小时）
    START_DATE=20220101 END_DATE=20221231 python scripts/ablation_5yr.py

    # 只跑指定的（逗号分隔）
    ABLATIONS=A1,A5 python scripts/ablation_5yr.py
"""
import os, sys, time, json, sqlite3
import numpy as np
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)  # 让 stockhot 包可被 import
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="ERROR")

from stockhot.data_layer.market_db import get_connection as get_market_conn
from stockhot.storage.database import init_database, DB_PATH
from davis_analyzer.paper_trading.account import PaperAccount
from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
from davis_analyzer.paper_trading.executor import DailyExecutor, run_backfill_auto

init_database()

START = os.environ.get("START_DATE", "20210104")
END = os.environ.get("END_DATE", "20260731")
INITIAL_CAPITAL = 1_000_000
UNIVERSE_SIZE = int(os.environ.get("UNIVERSE_SIZE", "200"))
SCORING_FREQUENCY = 1
ONLY = os.environ.get("ABLATIONS", "").split(",") if os.environ.get("ABLATIONS") else None

COMMISSION_RATE = 0.0008
STAMP_TAX_RATE = 0.0005


def build_universe(top_n: int) -> list[str]:
    with get_market_conn() as c:
        rows = c.execute(
            "SELECT ts_code FROM daily_price WHERE trade_date='20210104' "
            "AND close > 0 AND vol > 0 ORDER BY amount DESC LIMIT ?",
            (top_n,),
        ).fetchall()
    return [r[0] for r in rows]


def reset_account(name: str) -> PaperAccount:
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


def max_drawdown(nav_list):
    if not nav_list:
        return 0.0
    peak, mdd = nav_list[0], 0.0
    for v in nav_list:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > mdd:
            mdd = dd
    return mdd


def real_sharpe(daily_returns, annualization=252):
    if len(daily_returns) < 10:
        return 0.0
    arr = np.array(daily_returns)
    std = arr.std(ddof=1)
    if std < 1e-8:
        return 0.0
    return float(arr.mean() / std * np.sqrt(annualization))


def compute_costs(trades):
    total = 0.0
    for t in trades:
        notional = abs(t.shares * t.price)
        total += notional * COMMISSION_RATE
        if t.action.upper() == "SELL":
            total += notional * STAMP_TAX_RATE
    return total


def compute_benchmark(start, end, capital):
    with get_market_conn() as c:
        for idx_code in ("000300.SH", "000001.SH"):
            rows = c.execute(
                "SELECT trade_date, close FROM index_daily "
                "WHERE ts_code=? AND trade_date>=? AND trade_date<=? "
                "AND close > 0 ORDER BY trade_date",
                (idx_code, start, end),
            ).fetchall()
            if len(rows) > 10:
                break
        else:
            return None
    closes = [float(r[1]) for r in rows]
    ret = (closes[-1] / closes[0] - 1) * 100
    daily_rets = [(closes[i] / closes[i-1] - 1) * 100 for i in range(1, len(closes)) if closes[i-1] > 0]
    return {"index": idx_code, "return_pct": ret, "sharpe": real_sharpe(daily_rets)}


def run_variant(label: str, strategy: FactorThresholdStrategy, universe, executor_cls=DailyExecutor):
    """Run one ablation variant over the full period."""
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}", flush=True)

    account = reset_account(f"abl_{label}")
    t0 = time.time()
    run_backfill_auto(account, strategy, START, END,
                      universe_codes=universe, scoring_frequency=SCORING_FREQUENCY)
    elapsed = time.time() - t0

    nav_rows = account.get_nav_history()
    nav_history = [r.total_equity for r in nav_rows]
    final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
    total_ret = (final_nav / INITIAL_CAPITAL - 1) * 100
    total_mdd = max_drawdown(nav_history)
    daily_rets = [r.daily_return for r in nav_rows if r.daily_return is not None]
    sharpe_real = real_sharpe(daily_rets)
    trades = account.get_trades()
    costs = compute_costs(trades)
    costs_pct = costs / INITIAL_CAPITAL * 100
    ret_net = total_ret - costs_pct

    # Annual breakdown
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
        "label": label,
        "return_pct_gross": round(total_ret, 2),
        "return_pct_net": round(ret_net, 2),
        "transaction_cost_pct": round(costs_pct, 2),
        "max_drawdown_pct": round(total_mdd, 2),
        "sharpe_real": round(sharpe_real, 3),
        "n_trades": len(trades),
        "n_distinct_stocks": len({t.ts_code for t in trades}),
        "elapsed_hr": round(elapsed / 3600, 1),
        "annual_returns": {yr: round(r, 2) for yr, r in year_rets.items()},
    }
    print(f"  → {label}: 毛收益={total_ret:+.2f}% 净={ret_net:+.2f}% "
          f"MDD={total_mdd:.1f}% Sharpe={sharpe_real:+.3f} "
          f"交易={len(trades)} ({elapsed/3600:.1f}h)", flush=True)
    print(f"    年度: {year_rets}", flush=True)
    return result


def main():
    print(f"\n{'='*80}")
    print(f"  5 年消融实验 — {START} → {END}")
    print(f"  Universe: top {UNIVERSE_SIZE} by 2021-01-04 turnover")
    print(f"{'='*80}")

    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe: {len(universe)} stocks\n")

    benchmark = compute_benchmark(START, END, INITIAL_CAPITAL)
    if benchmark:
        print(f"  基准 {benchmark['index']}: {benchmark['return_pct']:+.2f}% "
              f"Sharpe={benchmark['sharpe']:+.3f}\n")

    # ── 定义消融变体 ──
    # 生产基线参数
    BASE = dict(
        max_positions=5, risk_stop_multiplier=0.70, sell_momentum=30,
        volume_weight=0.05, enable_volume_risk=True, pe_exemption_for_volume=True,
        max_intraday_amplitude=0.08, quality_weight=0.10, gap_weight=0.05,
        enable_event_filter=False, event_penalty_weight=0.0, tech_weight=0.0,
        enable_adaptive_sell=False, enable_dynamic_weight=False,
        amihud_weight=0.0, dragon_tiger_weight=0.0, repurchase_weight=0.0,
        low_vol_stop_exemption=0.0,
        buy_momentum=70, buy_holder_min=40, buy_dividend_min=55,
        buy_forecast_min=70, buy_prosperity_min=45, min_secondary_dims=1,
    )

    variants = [
        # A0 基线（当前生产，5年回测已跑 -3.38%）
        ("A0_baseline", FactorThresholdStrategy(**BASE), "当前生产（全开）"),

        # A1 移除 T+减仓 — 通过 monkey-patch executor 关闭
        ("A1_no_t_trim", None, "移除 T+减仓（最大嫌疑，占卖出44%）"),

        # A2 移除 gap
        ("A2_no_gap", FactorThresholdStrategy(**{**BASE, "gap_weight": 0.0}), "移除 gap 因子"),

        # A3 移除 quality
        ("A3_no_quality", FactorThresholdStrategy(**{**BASE, "quality_weight": 0.0}), "移除 quality 因子"),

        # A4 移除 amplitude
        ("A4_no_amplitude", FactorThresholdStrategy(**{**BASE, "max_intraday_amplitude": 0.0}), "移除振幅过滤"),

        # A5 最简策略（纯动量，V1 时代）
        ("A5_minimal_v1", FactorThresholdStrategy(
            max_positions=10, risk_stop_multiplier=0.70, sell_momentum=40,
            volume_weight=0.0, enable_volume_risk=False, pe_exemption_for_volume=False,
            max_intraday_amplitude=0.0, quality_weight=0.0, gap_weight=0.0,
            enable_event_filter=False, event_penalty_weight=0.0, tech_weight=0.0,
            enable_adaptive_sell=False, enable_dynamic_weight=False,
            amihud_weight=0.0, dragon_tiger_weight=0.0, repurchase_weight=0.0,
            low_vol_stop_exemption=0.0,
            buy_momentum=65, buy_holder_min=35, buy_dividend_min=55,
            buy_forecast_min=70, buy_prosperity_min=45, min_secondary_dims=1,
        ), "最简 V1（纯动量+pos10）"),

        # A6 放宽 T+减仓阈值 8%→15%
        ("A6_loose_t_trim", None, "放宽 T+减仓到 15%"),
    ]

    # Filter if ONLY is set (支持前缀匹配: A1 匹配 A1_no_t_trim)
    if ONLY:
        variants = [(l, s, d) for l, s, d in variants
                    if any(l == o or l.startswith(o + "_") or l.startswith(o) for o in ONLY)]
        print(f"  只跑: {[v[0] for v in variants]}\n")

    results = []

    for label, strategy, desc in variants:
        # A0 如果已经跑过，从已有结果读取
        if label == "A0_baseline":
            existing = "logs/backtest_5yr_annual.json"
            if os.path.exists(existing):
                with open(existing) as f:
                    d = json.load(f)
                # 检查是否是同一 universe/period
                if d.get("start") == START and d.get("end") == END:
                    print(f"\n  [{label}] 已有结果，跳过（{existing}）")
                    results.append({
                        "label": label, "desc": desc,
                        "return_pct_gross": d.get("return_pct", d.get("return_pct_gross", 0)),
                        "max_drawdown_pct": d.get("max_drawdown_pct", 0),
                        "sharpe_real": d.get("sharpe_real", d.get("sharpe", 0)),
                        "n_trades": d.get("n_trades", 0),
                        "annual_returns": {s["year"]: s.get("return_pct", 0)
                                          for s in d.get("annual_breakdown", [])
                                          if s.get("trading_days", 0) > 0},
                        "elapsed_hr": 0,
                        "note": "从已有回测复用",
                    })
                    continue

        if strategy is None:
            # A1/A6: 需要修改 executor 的 T+减仓参数
            # 通过创建自定义 executor 子类
            if label == "A1_no_t_trim":
                strategy = FactorThresholdStrategy(**BASE)
                # Patch: 在 run_backfill_auto 创建的 executor 上关闭 t_trading
                # 由于 run_backfill_auto 内部创建 executor，我们用 monkey-patch
                orig_init = DailyExecutor.__init__
                def patched_init(self, *a, **kw):
                    orig_init(self, *a, **kw)
                    self.enable_t_trading = False
                DailyExecutor.__init__ = patched_init
                r = run_variant(label, strategy, universe)
                DailyExecutor.__init__ = orig_init  # restore
                r["desc"] = desc
                results.append(r)
                continue
            elif label == "A6_loose_t_trim":
                strategy = FactorThresholdStrategy(**BASE)
                orig_init = DailyExecutor.__init__
                def patched_init2(self, *a, **kw):
                    orig_init(self, *a, **kw)
                    self.t_trim_threshold = 0.15  # 8% → 15%
                DailyExecutor.__init__ = patched_init2
                r = run_variant(label, strategy, universe)
                DailyExecutor.__init__ = orig_init
                r["desc"] = desc
                results.append(r)
                continue

        r = run_variant(label, strategy, universe)
        r["desc"] = desc
        results.append(r)

    # ── Summary ──
    print(f"\n\n{'='*90}")
    print(f"  消融实验总览 — {START} → {END}")
    if benchmark:
        print(f"  基准 {benchmark['index']}: {benchmark['return_pct']:+.2f}% Sharpe={benchmark['sharpe']:+.3f}")
    print(f"{'='*90}")
    print(f"  {'变体':<22} {'描述':<28} {'净收益':>8} {'回撤':>6} {'Sharpe':>7} {'交易':>5}")
    print(f"  {'-'*22} {'-'*28} {'-'*8} {'-'*6} {'-'*7} {'-'*5}")
    for r in sorted(results, key=lambda x: -x.get("sharpe_real", -99)):
        print(f"  {r['label']:<22} {r.get('desc',''):<28} "
              f"{r.get('return_pct_net', r.get('return_pct_gross',0)):>+7.2f}% "
              f"{r.get('max_drawdown_pct',0):>5.1f}% "
              f"{r.get('sharpe_real',0):>+7.3f} "
              f"{r.get('n_trades',0):>5}")

    print(f"\n  年度收益对比:")
    all_years = sorted({yr for r in results for yr in r.get("annual_returns", {})})
    hdr = f"  {'变体':<22} " + " ".join(f"{y:>8}" for y in all_years)
    print(hdr)
    for r in results:
        vals = " ".join(f"{r.get('annual_returns',{}).get(y, 0):>+7.1f}%" for y in all_years)
        print(f"  {r['label']:<22} {vals}")

    # Save
    output = {
        "start": START, "end": END,
        "universe": f"top {UNIVERSE_SIZE} by 2021-01-04 turnover",
        "benchmark": benchmark,
        "results": results,
    }
    out_path = "logs/ablation_5yr.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  结果保存到 {out_path}")


if __name__ == "__main__":
    main()
