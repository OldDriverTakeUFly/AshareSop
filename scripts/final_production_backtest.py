"""最终生产配置回测 + 完整交易记录导出.

用当前最优配置（HMM + 量价 + Sharpe 优化 + PE 豁免 + sell30）跑 127 天回测，
导出完整交易明细 + 净值曲线 + 信号日志。
"""
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

START = "20260105"
END = "20260715"
INITIAL_CAPITAL = 1_000_000
UNIVERSE_SIZE = 200
SCORING_FREQUENCY = 3
ACCOUNT_NAME = "production_final"


def build_universe(top_n):
    with get_market_conn() as c:
        ref_row = c.execute("SELECT MAX(trade_date) FROM daily_price WHERE trade_date < ?", (START,)).fetchone()
        ref_end = ref_row[0] if ref_row and ref_row[0] else "20251231"
        rows = c.execute("""
            SELECT a.ts_code FROM daily_price a
            JOIN daily_price b ON a.ts_code=b.ts_code
              AND b.trade_date = (SELECT MAX(trade_date) FROM daily_price WHERE ts_code=a.ts_code AND trade_date <= '20251001')
            WHERE a.trade_date = ? AND a.close > 0 AND b.close > 0 AND a.vol > 0
            ORDER BY (a.close / b.close - 1) DESC LIMIT ?
        """, (ref_end, top_n)).fetchall()
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


def max_drawdown(nav):
    peak = nav[0] if nav else 0
    mdd = 0.0
    mdd_date = ""
    for i, v in enumerate(nav):
        if v > peak: peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > mdd:
            mdd = dd
    return mdd


def main():
    print(f"\n{'='*70}")
    print(f"  最终生产配置回测 — {START} → {END}")
    print(f"{'='*70}\n")

    # Build universe
    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe: {len(universe)} stocks (top {UNIVERSE_SIZE} by 90d return)")

    # Strategy = current production defaults
    strategy = FactorThresholdStrategy(
        buy_momentum=65, buy_holder_min=35,  # 回测用 65/35（扫描确认最优）
        sell_momentum=30,
    )
    print(f"\n  Strategy config:")
    print(f"    max_positions={strategy.max_positions}")
    print(f"    risk_stop_multiplier={strategy.risk_stop_multiplier}")
    print(f"    sell_momentum={strategy.sell_momentum}")
    print(f"    volume_weight={strategy.volume_weight}")
    print(f"    pe_exemption_for_volume={strategy.pe_exemption_for_volume}")
    print(f"    scoring_frequency={SCORING_FREQUENCY}")
    print(f"    + HMM regime + vol adjustment + industry dual-confirm")

    # Run backtest
    account = reset_account(ACCOUNT_NAME)
    t0 = time.time()
    results = run_backfill_auto(account, strategy, START, END,
                                universe_codes=universe, scoring_frequency=SCORING_FREQUENCY)
    elapsed = time.time() - t0

    # Collect stats
    nav_rows = account.get_nav_history()
    nav_history = [r.total_equity for r in nav_rows]
    final_nav = nav_history[-1] if nav_history else INITIAL_CAPITAL
    total_ret = (final_nav / INITIAL_CAPITAL - 1) * 100
    mdd = max_drawdown(nav_history) if nav_history else 0
    sharpe = total_ret / mdd if mdd > 0.01 else 0
    trades = account.get_trades()
    n_buys = sum(1 for t in trades if t.action == "BUY")
    n_sells = sum(1 for t in trades if t.action == "SELL")
    distinct_stocks = len({t.ts_code for t in trades})

    # Sell reason breakdown
    sell_reasons = {}
    for t in trades:
        if t.action == "SELL":
            reason = t.signal_reason or "unknown"
            # Categorize
            if "止损" in reason: cat = "硬止损"
            elif "止盈" in reason: cat = "止盈"
            elif "高位放量" in reason: cat = "高位放量"
            elif "动量" in reason: cat = "动量崩塌"
            elif "景气" in reason: cat = "景气拐点"
            elif "赛道" in reason or "行业" in reason: cat = "板块切换"
            elif "筹码" in reason: cat = "筹码分散"
            else: cat = "其他"
            sell_reasons[cat] = sell_reasons.get(cat, 0) + 1

    account.close()

    # ── Summary output ──
    print(f"\n{'='*70}")
    print(f"  回测结果")
    print(f"{'='*70}")
    print(f"  回测期间:     {START} → {END} ({len(nav_history)} 个交易日)")
    print(f"  耗时:         {elapsed/60:.1f} 分钟")
    print(f"  初始资金:     {INITIAL_CAPITAL:,.0f}")
    print(f"  最终 NAV:     {final_nav:,.0f}")
    print(f"  总收益率:     {total_ret:+.2f}%")
    print(f"  最大回撤:     {mdd:.2f}%")
    print(f"  Sharpe:       {sharpe:+.3f}")
    print(f"  总交易:       {len(trades)} 笔 ({n_buys} 买 / {n_sells} 卖)")
    print(f"  涉及股票:     {distinct_stocks} 只")
    print(f"\n  卖出原因分布:")
    for cat, n in sorted(sell_reasons.items(), key=lambda x: -x[1]):
        print(f"    {cat}: {n}")

    # ── Monthly returns ──
    print(f"\n  月度收益:")
    monthly = {}
    for r in nav_rows:
        month = r.trade_date[:6]
        if month not in monthly:
            monthly[month] = {"start": r.total_equity, "end": r.total_equity}
        monthly[month]["end"] = r.total_equity
    for month, vals in sorted(monthly.items()):
        if len(monthly) > 1:
            ret = (vals["end"] / vals["start"] - 1) * 100
        else:
            ret = (vals["end"] / INITIAL_CAPITAL - 1) * 100
        print(f"    {month}: {ret:+.2f}% (NAV={vals['end']:,.0f})")

    # ── Trade detail (first 30 + last 10) ──
    print(f"\n  交易明细（前 30 笔）:")
    print(f"    {'日期':<10} {'代码':<12} {'动作':<5} {'数量':>6} {'价格':>8} {'金额':>10} {'原因'}")
    print(f"    {'-'*10} {'-'*12} {'-'*5} {'-'*6} {'-'*8} {'-'*10} {'-'*30}")
    # Re-read trades
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        trade_rows = c.execute("""
            SELECT trade_date, ts_code, action, shares, price, amount, signal_reason
            FROM paper_trades t JOIN paper_accounts a ON t.account_id=a.id
            WHERE a.name=? ORDER BY trade_date, action
        """, (ACCOUNT_NAME,)).fetchall()

    for t in trade_rows[:30]:
        reason = (t["signal_reason"] or "")[:28]
        print(f"    {t['trade_date']:<10} {t['ts_code']:<12} {t['action']:<5} "
              f"{t['shares']:>6} {t['price']:>8.2f} {t['amount']:>10,.0f} {reason}")
    if len(trade_rows) > 30:
        print(f"    ... ({len(trade_rows) - 30} more trades)")
        for t in trade_rows[-5:]:
            reason = (t["signal_reason"] or "")[:28]
            print(f"    {t['trade_date']:<10} {t['ts_code']:<12} {t['action']:<5} "
                  f"{t['shares']:>6} {t['price']:>8.2f} {t['amount']:>10,.0f} {reason}")

    # ── Save JSON ──
    output = {
        "config": "HMM + volume_price + Sharpe_optimized + PE_exempt + sell30",
        "start": START, "end": END,
        "initial_capital": INITIAL_CAPITAL,
        "final_nav": final_nav,
        "return_pct": total_ret,
        "max_drawdown_pct": mdd,
        "sharpe": sharpe,
        "n_trades": len(trades), "n_buys": n_buys, "n_sells": n_sells,
        "distinct_stocks": distinct_stocks,
        "sell_reasons": sell_reasons,
        "monthly_returns": {m: {"ret_pct": (v["end"]/v["start"]-1)*100, "nav": v["end"]}
                           for m, v in monthly.items()},
        "nav_curve": [{"date": r.trade_date, "nav": r.total_equity, "daily_ret": r.daily_return}
                      for r in nav_rows],
    }
    out_path = "logs/production_backtest_final.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  完整结果保存到 {out_path}")


if __name__ == "__main__":
    main()
