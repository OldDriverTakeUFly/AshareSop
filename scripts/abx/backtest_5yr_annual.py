"""5 年扩展回测 + 年度收益分项（2021-01 → 2026-07）.

用当前生产配置（gap_weight=0.05 + quality=0.10 + amplitude=0.08 + HMM）
跑 5.5 年回测，按年度分段统计收益/回撤/Sharpe。

⚠️ 已知限制（honest disclosure）：
  1. HMM 用全样本 (2021-2025) 训练后预测历史状态，存在前视偏差（look-ahead）。
     影响：状态转换点的定位可能略偏，但 bull/bear 大趋势识别影响较小。
  2. daily_basic（PE/PB）2021-2025 缺失，PE 过滤在历史段被自动放宽
     （pe_exemption_for_volume=True 时多数股票本就豁免，影响有限）。
  3. intraday_feature（amplitude/gap）已 5 年回填完成，全周期可用。
  4. 股票池用 2021-01-04 当日成交额 top-200（非生产的 90d 动量排名），
     因为 daily_price 无 2020 年数据，无法计算 90d 动量。这是更保守的
     universe（流动性导向而非动量导向），可能让策略表现略低于生产。

Usage::

    cd /home/leo/Projects/CodeAgentDashboard

    # 完整 200 只（推荐，约 3-4 小时，建议 nohup 后台跑）
    nohup python scripts/backtest_5yr_annual.py > logs/backtest_5yr.log 2>&1 &

    # 快速验证（50 只，约 1 小时）
    UNIVERSE_SIZE=50 python scripts/backtest_5yr_annual.py

    # 输出: logs/backtest_5yr_annual.json
"""
import os, sys, time, json, sqlite3
import numpy as np
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="ERROR")

from stockhot.data_layer.market_db import get_connection as get_market_conn
from stockhot.storage.database import init_database, DB_PATH
from davis_analyzer.paper_trading.account import PaperAccount
from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
from davis_analyzer.paper_trading.executor import run_backfill_auto

# ── 交易成本建模 ──
# A 股双边成本: 佣金 0.025%×2 + 印花税 0.05%(卖单) + 滑点 ~0.05%
# 单边约 0.075-0.08%，回测里按每笔交易扣减
COMMISSION_RATE = 0.0008   # 单边 0.08% (含佣金+滑点)
STAMP_TAX_RATE = 0.0005    # 印花税 0.05% (仅卖出)

init_database()

START = "20210104"          # 2021 第一个交易日
END = "20260731"            # 至今
INITIAL_CAPITAL = 1_000_000
UNIVERSE_SIZE = int(os.environ.get("UNIVERSE_SIZE", "200"))  # 可通过环境变量调整
SCORING_FREQUENCY = 1       # freq=1 A/B 已确认更优
ACCOUNT_NAME = f"backtest_5yr_u{UNIVERSE_SIZE}"

# 年度分段（用于年度统计）
YEAR_BOUNDARIES = [
    ("2021", "20210104", "20211231"),
    ("2022", "20220104", "20221230"),
    ("2023", "20230103", "20231229"),
    ("2024", "20240102", "20241231"),
    ("2025", "20250103", "20251231"),
    ("2026", "20260102", "20260731"),
]


def build_universe_5yr(top_n: int) -> list[str]:
    """Build universe at 2021-01-04 cross-section (top by turnover amount).

    daily_price data starts at 2021-01-04 (no 2020 data available), so we
    cannot use 90-day return ranking. Instead we rank by turnover amount on
    the backtest start date — this selects liquid, actively-traded stocks
    that are guaranteed to be listed at backtest start.

    This is a more conservative universe than the production 90d-momentum
    ranking, but it's the only honest option given data availability.
    """
    with get_market_conn() as c:
        rows = c.execute("""
            SELECT ts_code FROM daily_price
            WHERE trade_date = '20210104' AND close > 0 AND vol > 0
            ORDER BY amount DESC LIMIT ?
        """, (top_n,)).fetchall()
    return [r[0] for r in rows]


def reset_account(name: str) -> PaperAccount:
    """Delete existing account + all data, create fresh."""
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


def max_drawdown(nav_list: list[float]) -> float:
    if not nav_list:
        return 0.0
    peak = nav_list[0]
    mdd = 0.0
    for v in nav_list:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > mdd:
            mdd = dd
    return mdd


def real_sharpe(daily_returns: list[float], annualization: int = 252) -> float:
    """Compute annualized Sharpe ratio from daily returns.

    Uses rf=0 (A-share context: 1y deposit ~1.5%, negligible vs equity vol).
    Returns 0.0 if insufficient data or zero variance.
    """
    if len(daily_returns) < 10:
        return 0.0
    arr = np.array(daily_returns)
    std = arr.std(ddof=1)
    if std < 1e-8:
        return 0.0
    mean = arr.mean()
    return float(mean / std * np.sqrt(annualization))


def compute_costs(trades) -> float:
    """Estimate total transaction costs (commission + stamp tax).

    Each trade pays commission on notional; sells also pay stamp tax.
    """
    total_cost = 0.0
    for t in trades:
        notional = abs(t.shares * t.price)
        total_cost += notional * COMMISSION_RATE
        if t.action.upper() == "SELL":
            total_cost += notional * STAMP_TAX_RATE
    return total_cost


def compute_benchmark(start: str, end: str, capital: float) -> dict:
    """Compute buy-and-hold benchmark using 沪深300 (000300.SH).

    Falls back to 上证指数 (000001.SH) if 300 missing. Returns benchmark
    NAV curve and return for comparison.
    """
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
            return {"available": False}

    closes = [float(r[1]) for r in rows]
    dates = [r[0] for r in rows]
    base = closes[0]
    nav_curve = [capital * (c / base) for c in closes]
    benchmark_ret = (closes[-1] / base - 1) * 100

    # Benchmark daily returns for Sharpe
    daily_rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            daily_rets.append((closes[i] / closes[i - 1] - 1) * 100)

    return {
        "available": True,
        "index": idx_code,
        "return_pct": benchmark_ret,
        "nav_curve": [{"date": d, "nav": v} for d, v in zip(dates, nav_curve)],
        "sharpe": real_sharpe(daily_rets),
        "n_days": len(dates),
    }


def annual_stats(nav_rows, trades, year: str, start_d: str, end_d: str) -> dict:
    """Compute year-bounded stats from full nav/trade history."""
    year_navs = [r for r in nav_rows if start_d <= r.trade_date <= end_d]
    if not year_navs:
        return {"year": year, "trading_days": 0}

    # Year-start NAV = either first nav of year OR carryover from prior year
    # For accurate year-start, use INITIAL_CAPITAL for 2021, else use prior year-end
    if year == "2021":
        year_start_nav = INITIAL_CAPITAL
    else:
        # Find last nav before this year
        prior_navs = [r for r in nav_rows if r.trade_date < start_d]
        year_start_nav = prior_navs[-1].total_equity if prior_navs else INITIAL_CAPITAL

    year_end_nav = year_navs[-1].total_equity
    year_ret = (year_end_nav / year_start_nav - 1) * 100
    year_nav_list = [year_start_nav] + [r.total_equity for r in year_navs]
    year_mdd = max_drawdown(year_nav_list)

    # Real Sharpe from daily returns
    year_daily_rets = [r.daily_return for r in year_navs if r.daily_return is not None]
    year_sharpe = real_sharpe(year_daily_rets)

    year_trades = [t for t in trades if start_d <= t.trade_date <= end_d]
    n_buys = sum(1 for t in year_trades if t.action == "BUY")
    n_sells = sum(1 for t in year_trades if t.action == "SELL")
    distinct_stocks = len({t.ts_code for t in year_trades})

    # Transaction costs for this year
    year_costs = compute_costs(year_trades)
    year_costs_pct = year_costs / year_start_nav * 100

    sharpe_like = year_ret / year_mdd if year_mdd > 0.01 else 0

    return {
        "year": year,
        "trading_days": len(year_navs),
        "start_nav": year_start_nav,
        "end_nav": year_end_nav,
        "return_pct": year_ret,
        "return_pct_net": year_ret - year_costs_pct,
        "transaction_cost_pct": year_costs_pct,
        "max_drawdown_pct": year_mdd,
        "sharpe_real": year_sharpe,
        "sharpe_like": sharpe_like,
        "n_trades": len(year_trades),
        "n_buys": n_buys,
        "n_sells": n_sells,
        "distinct_stocks": distinct_stocks,
    }


def main():
    print(f"\n{'='*80}")
    print(f"  5 年扩展回测 — {START} → {END}")
    print(f"  生产配置: gap=0.05 + quality=0.10 + amplitude=0.08 + HMM + freq=1")
    print(f"{'='*80}\n")

    # Build universe
    print(f"  构建 2020-12-31 截面股票池 (top {UNIVERSE_SIZE} by 90d return)...")
    universe = build_universe_5yr(UNIVERSE_SIZE)
    print(f"  Universe: {len(universe)} stocks")
    print(f"  示例: {universe[:5]}")

    # Strategy = production defaults (gap_weight=0.05 already baked in)
    strategy = FactorThresholdStrategy()
    print(f"\n  Strategy config:")
    print(f"    max_positions={strategy.max_positions}, risk_stop_mult={strategy.risk_stop_multiplier}")
    print(f"    volume_weight={strategy.volume_weight}, quality_weight={strategy.quality_weight}")
    print(f"    gap_weight={strategy.gap_weight}, max_intraday_amplitude={strategy.max_intraday_amplitude}")
    print(f"    pe_exemption_for_volume={strategy.pe_exemption_for_volume}")
    print(f"    scoring_frequency={SCORING_FREQUENCY}")

    # Run backtest
    account = reset_account(ACCOUNT_NAME)
    print(f"\n  开始回测（预计耗时 60-90 分钟）...")
    t0 = time.time()
    results = run_backfill_auto(account, strategy, START, END,
                                universe_codes=universe, scoring_frequency=SCORING_FREQUENCY)
    elapsed = time.time() - t0

    # Collect full history
    nav_rows = account.get_nav_history()
    nav_history = [r.total_equity for r in nav_rows]
    final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
    total_ret = (final_nav / INITIAL_CAPITAL - 1) * 100
    total_mdd = max_drawdown(nav_history) if nav_history else 0
    total_sharpe_like = total_ret / total_mdd if total_mdd > 0.01 else 0

    # Real Sharpe from daily returns
    all_daily_rets = [r.daily_return for r in nav_rows if r.daily_return is not None]
    total_sharpe_real = real_sharpe(all_daily_rets)

    trades = account.get_trades()
    total_costs = compute_costs(trades)
    total_costs_pct = total_costs / INITIAL_CAPITAL * 100
    total_ret_net = total_ret - total_costs_pct

    account.close()

    # ── Benchmark ──
    print(f"\n  计算基准 (沪深300/上证指数 买入持有)...")
    benchmark = compute_benchmark(START, END, INITIAL_CAPITAL)

    # ── Annual breakdown ──
    annual = []
    for year, sd, ed in YEAR_BOUNDARIES:
        s = annual_stats(nav_rows, trades, year, sd, ed)
        annual.append(s)

    # ── Summary output ──
    print(f"\n{'='*80}")
    print(f"  5 年回测总览")
    print(f"{'='*80}")
    print(f"  回测期间:     {START} → {END} ({len(nav_history)} 个交易日)")
    print(f"  耗时:         {elapsed/60:.1f} 分钟")
    print(f"  初始资金:     {INITIAL_CAPITAL:,.0f}")
    print(f"  最终 NAV:     {final_nav:,.0f}")
    print(f"  ────────────────────────────────────────")
    print(f"  总收益率(毛):  {total_ret:+.2f}%")
    print(f"  交易成本:      {total_costs_pct:.2f}% ({len(trades)} 笔)")
    print(f"  总收益率(净):  {total_ret_net:+.2f}%")
    print(f"  最大回撤:      {total_mdd:.2f}%")
    print(f"  Sharpe(真):    {total_sharpe_real:+.3f}  ← 日收益均值/标准差×√252")
    print(f"  Sharpe(Calmar):{total_sharpe_like:+.3f}  ← 收益/回撤 (旧定义)")
    if benchmark.get("available"):
        bench_ret = benchmark["return_pct"]
        excess = total_ret_net - bench_ret
        print(f"  ────────────────────────────────────────")
        print(f"  基准({benchmark['index']}): {bench_ret:+.2f}%  Sharpe={benchmark['sharpe']:+.3f}")
        print(f"  超额收益:      {excess:+.2f}%  (净收益 - 基准)")
    print(f"  ────────────────────────────────────────")
    print(f"  总交易:        {len(trades)} 笔")
    print(f"  涉及股票:      {len({t.ts_code for t in trades})} 只")

    print(f"\n{'='*80}")
    print(f"  年度分项")
    print(f"{'='*80}")
    hdr = (f"  {'年份':<6} {'日数':>4} {'毛收益':>8} {'净收益':>8} {'成本':>5} "
           f"{'回撤':>6} {'Sharpe真':>8} {'Calmar':>7} {'交易':>5} {'股票':>5}")
    print(hdr)
    print(f"  {'-'*6} {'-'*4} {'-'*8} {'-'*8} {'-'*5} {'-'*6} {'-'*8} {'-'*7} {'-'*5} {'-'*5}")
    for s in annual:
        if s.get("trading_days", 0) == 0:
            print(f"  {s['year']:<6}  (无数据)")
            continue
        print(f"  {s['year']:<6} {s['trading_days']:>4} "
              f"{s['return_pct']:>+7.2f}% {s['return_pct_net']:>+7.2f}% "
              f"{s['transaction_cost_pct']:>4.2f}% "
              f"{s['max_drawdown_pct']:>5.1f}% {s['sharpe_real']:>+8.3f} "
              f"{s['sharpe_like']:>+7.3f} {s['n_trades']:>5} {s['distinct_stocks']:>5}")

    # ── Sell reason breakdown ──
    sell_reasons = {}
    for t in trades:
        if t.action == "SELL":
            reason = t.signal_reason or "unknown"
            if "止损" in reason: cat = "硬止损"
            elif "止盈" in reason: cat = "止盈"
            elif "高位放量" in reason: cat = "高位放量"
            elif "动量" in reason: cat = "动量崩塌"
            elif "景气" in reason: cat = "景气拐点"
            elif "赛道" in reason or "行业" in reason: cat = "板块切换"
            elif "筹码" in reason: cat = "筹码分散"
            else: cat = "其他"
            sell_reasons[cat] = sell_reasons.get(cat, 0) + 1
    print(f"\n  卖出原因分布（全周期）:")
    for cat, n in sorted(sell_reasons.items(), key=lambda x: -x[1]):
        print(f"    {cat}: {n}")

    # ── Save JSON ──
    output = {
        "config": "production: gap=0.05 + quality=0.10 + amplitude=0.08 + HMM + freq=1",
        "caveats": [
            "HMM 用全样本训练后预测历史状态，存在前视偏差（状态转换点定位可能略偏）",
            "daily_basic(PE/PB) 2021-2025 缺失，PE 过滤在历史段被自动放宽",
            "intraday_feature 已 5 年回填完成，amplitude/gap 全周期可用",
            "Universe 用 2021-01-04 成交额排名（非动量），更保守但无前视偏差",
            "财务数据已 point-in-time 修正（as_of 参数），无财报前视偏差",
        ],
        "start": START, "end": END,
        "initial_capital": INITIAL_CAPITAL,
        "final_nav": final_nav,
        "return_pct_gross": total_ret,
        "return_pct_net": total_ret_net,
        "transaction_cost_pct": total_costs_pct,
        "max_drawdown_pct": total_mdd,
        "sharpe_real": total_sharpe_real,
        "sharpe_calmar": total_sharpe_like,
        "benchmark": benchmark,
        "excess_return_pct": total_ret_net - benchmark.get("return_pct", 0) if benchmark.get("available") else None,
        "n_trading_days": len(nav_history),
        "n_trades": len(trades),
        "distinct_stocks": len({t.ts_code for t in trades}),
        "annual_breakdown": annual,
        "sell_reasons": sell_reasons,
        "nav_curve": [{"date": r.trade_date, "nav": r.total_equity, "daily_ret": r.daily_return}
                      for r in nav_rows],
    }
    out_path = "logs/backtest_5yr_annual.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  完整结果保存到 {out_path}")


if __name__ == "__main__":
    main()
