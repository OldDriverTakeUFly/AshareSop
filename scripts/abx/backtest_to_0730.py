"""Full backtest to today (2026-07-30) with amplitude filter enabled."""
import os, sys, time, json, sqlite3
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="WARNING")
from stockhot.data_layer.market_db import get_connection as get_market_conn
from stockhot.storage.database import init_database, DB_PATH
from davis_analyzer.paper_trading.account import PaperAccount
from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
from davis_analyzer.paper_trading.executor import run_backfill_auto
init_database()
START = "20260105"; END = "20260730"; INITIAL_CAPITAL = 1_000_000; UNIVERSE_SIZE = 200; SCORING_FREQUENCY = 3
ACCOUNT = "production_amp08"
BASE = dict(max_positions=5, risk_stop_multiplier=0.70, sell_momentum=30,
    volume_weight=0.05, enable_volume_risk=True, pe_exemption_for_volume=True,
    enable_event_filter=False, event_penalty_weight=0.0, tech_weight=0.0,
    low_vol_stop_exemption=0.0, enable_adaptive_sell=False, enable_dynamic_weight=False,
    amihud_weight=0.0, dragon_tiger_weight=0.0, repurchase_weight=0.0,
    max_intraday_amplitude=0.08,
    buy_momentum=65, buy_holder_min=35, buy_dividend_min=55,
    buy_forecast_min=70, buy_prosperity_min=45, min_secondary_dims=1)

def build_universe(top_n):
    with get_market_conn() as c:
        ref_row = c.execute("SELECT MAX(trade_date) FROM daily_price WHERE trade_date < ?", (START,)).fetchone()
        ref_end = ref_row[0] if ref_row and ref_row[0] else "20251231"
        rows = c.execute("SELECT a.ts_code FROM daily_price a JOIN daily_price b ON a.ts_code=b.ts_code AND b.trade_date = (SELECT MAX(trade_date) FROM daily_price WHERE ts_code=a.ts_code AND trade_date <= '20251001') WHERE a.trade_date = ? AND a.close > 0 AND b.close > 0 AND a.vol > 0 ORDER BY (a.close / b.close - 1) DESC LIMIT ?", (ref_end, top_n)).fetchall()
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
    return PaperAccount.create(name=name, strategy_name="factor_threshold", initial_capital=INITIAL_CAPITAL, config=BASE)

def max_dd(nav):
    peak = nav[0] if nav else 0; mdd = 0; mdd_date = ""
    for i, v in enumerate(nav):
        if v > peak: peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > mdd: mdd = dd
    return mdd

def main():
    print(f"\n{'='*70}")
    print(f"  最终配置回测到今天 — {START} → {END}")
    print(f"  配置: HMM + amplitude_08 + 全部优化")
    print(f"{'='*70}\n")
    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe: {len(universe)} stocks")
    account = reset_account(ACCOUNT)
    strategy = FactorThresholdStrategy(**BASE)
    t0 = time.time()
    results = run_backfill_auto(account, strategy, START, END, universe_codes=universe, scoring_frequency=SCORING_FREQUENCY)
    elapsed = time.time() - t0
    nav_rows = account.get_nav_history()
    nav_history = [r.total_equity for r in nav_rows]
    final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
    total_ret = (final_nav / INITIAL_CAPITAL - 1) * 100
    mdd = max_dd(nav_history) if nav_history else 0
    sharpe = total_ret / mdd if mdd > 0.01 else 0
    trades = account.get_trades()
    n_buys = sum(1 for t in trades if t.action == "BUY")
    n_sells = sum(1 for t in trades if t.action == "SELL")
    distinct_stocks = len({t.ts_code for t in trades})
    # Monthly returns
    monthly = {}
    for r in nav_rows:
        m = r.trade_date[:6]
        if m not in monthly: monthly[m] = {"start": r.total_equity, "end": r.total_equity}
        monthly[m]["end"] = r.total_equity
    # Sell reasons
    sell_cats = {}
    for t in trades:
        if t.action == "SELL":
            reason = t.signal_reason or ""
            if "止损" in reason: cat = "硬止损"
            elif "止盈" in reason: cat = "止盈"
            elif "高位放量" in reason: cat = "高位放量"
            elif "动量" in reason: cat = "动量崩塌"
            elif "景气" in reason and "切换" in reason: cat = "板块切换"
            elif "景气" in reason: cat = "景气拐点"
            elif "T+" in reason or "T减" in reason: cat = "T+减仓"
            elif "赛道" in reason or "行业" in reason: cat = "板块切换"
            else: cat = "其他"
            sell_cats[cat] = sell_cats.get(cat, 0) + 1
    # Current positions
    positions = account.get_positions()
    account.close()
    print(f"\n{'='*70}")
    print(f"  回测结果（{START} → {END}）")
    print(f"{'='*70}")
    print(f"  交易日:       {len(nav_history)} 天")
    print(f"  耗时:         {elapsed/60:.1f} 分钟")
    print(f"  初始资金:     {INITIAL_CAPITAL:,}")
    print(f"  最终 NAV:     {final_nav:,.0f}")
    print(f"  总收益率:     {total_ret:+.2f}%")
    print(f"  最大回撤:     {mdd:.2f}%")
    print(f"  Sharpe:       {sharpe:+.3f}")
    print(f"  总交易:       {len(trades)} 笔 ({n_buys} 买 / {n_sells} 卖)")
    print(f"  涉及股票:     {distinct_stocks} 只")
    print(f"  当前持仓:     {len(positions)} 只")
    for p in positions:
        print(f"    {p.ts_code} {p.name} qty={p.shares} cost={p.avg_cost:.2f}")
    print(f"\n  月度收益:")
    for m, v in sorted(monthly.items()):
        ret = (v["end"] / v["start"] - 1) * 100
        print(f"    {m[:4]}-{m[4:]}: {ret:+.2f}% (NAV={v['end']:,.0f})")
    print(f"\n  卖出原因:")
    for cat, n in sorted(sell_cats.items(), key=lambda x: -x[1]):
        print(f"    {cat}: {n}")
    output = {"start": START, "end": END, "final_nav": final_nav, "return_pct": total_ret,
              "max_drawdown_pct": mdd, "sharpe": sharpe, "n_trades": len(trades),
              "n_buys": n_buys, "n_sells": n_sells, "distinct_stocks": distinct_stocks}
    with open("logs/backtest_to_0730.json", "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  保存到 logs/backtest_to_0730.json")

if __name__ == "__main__":
    main()
