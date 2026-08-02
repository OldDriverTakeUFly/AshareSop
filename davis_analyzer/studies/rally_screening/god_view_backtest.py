"""上帝视角回测：每天选全市场涨幅第一名，T+1满仓滚动。

规则：
- 每个交易日，选当日全市场涨幅最高的标的（排除ST/退市/北交所/成交额<1000万）
- 当天开盘价买入（满仓），次日开盘价卖出
- 循环至最后一天
- 简化：不考虑涨停板买不进的问题（"最完美决策"假设能买进）
- 简化：不考虑交易成本（佣金/印花税/滑点）

输出：
- 每日操作记录（买卖标的+价格+当日涨幅）
- 累计收益曲线
- 统计指标（总收益/年化/夏普/回撤/胜率）
"""
import os
from dotenv import load_dotenv
load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

import sys
import loguru
loguru.logger.remove()

import json
import time
import numpy as np
import pandas as pd
from datetime import datetime

from davis_analyzer.studies.rally_screening.utils import (
    get_name_map as _get_name_map,
    get_trade_dates as _get_trade_dates,
    load_daily_by_date,
)

# ===== 参数 =====
START_DATE = "20251201"
END_DATE = "20260725"
INITIAL_CAPITAL = 100_0000  # 初始100万


def get_name_map():
    return _get_name_map()


def get_trade_dates(start, end):
    return _get_trade_dates(start, end)


def get_daily_cache(trade_date):
    """获取某日全市场日线（从 SQLite 读取）"""
    return load_daily_by_date(trade_date)


def filter_candidates(df, name_map):
    """筛选候选标的"""
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["name"] = df["ts_code"].map(name_map).fillna("?")
    # 排除ST
    df = df[~df["name"].str.contains("ST", na=False)]
    # 排除退市
    df = df[~df["name"].str.contains("退", na=False)]
    # 只保留主板+创业板+科创板
    df = df[df["ts_code"].str.startswith(("00", "30", "60", "68"))]
    # 成交额>1000万
    df = df[df["amount"] > 10000]  # amount单位千元，10000千元=1千万
    # 涨跌幅非空
    df = df[df["pct_chg"].notna()]
    return df


def get_next_day_open(ts_code, current_date, trade_dates):
    """获取某标的次日的开盘价"""
    idx = trade_dates.index(current_date)
    if idx + 1 >= len(trade_dates):
        return None, None  # 最后一天，无法卖出
    next_date = trade_dates[idx + 1]
    df = load_daily_by_date(next_date)
    row = df[df["ts_code"] == ts_code]
    if not row.empty:
        return float(row.iloc[0]["open"]), next_date
    return None, None


def main():
    print("=" * 90)
    print("上帝视角回测：每天满仓涨幅第一名，T+1滚动")
    print(f"区间: {START_DATE} ~ {END_DATE}  初始资金: {INITIAL_CAPITAL/10000:.0f}万")
    print("=" * 90)

    name_map = get_name_map()
    trade_dates = get_trade_dates(START_DATE, END_DATE)
    print(f"交易日数: {len(trade_dates)}")
    print(f"数据从 SQLite 读取（零 API 调用）\n")

    # ===== 回测 =====
    capital = INITIAL_CAPITAL
    equity_curve = []
    trades = []
    daily_returns = []

    for i, d in enumerate(trade_dates[:-1]):  # 最后一天不买（无法卖）
        df = get_daily_cache(d)
        candidates = filter_candidates(df, name_map)
        if candidates.empty:
            continue

        # 选涨幅第一名
        top1 = candidates.sort_values("pct_chg", ascending=False).iloc[0]
        ts_code = top1["ts_code"]
        name = top1["name"]
        buy_price = float(top1["open"])
        pct_chg = float(top1["pct_chg"])
        close_price = float(top1["close"])

        if buy_price <= 0:
            continue

        # 次日开盘卖出
        sell_price, sell_date = get_next_day_open(ts_code, d, trade_dates)
        if sell_price is None or sell_price <= 0:
            # 次日停牌或退市，用当日收盘价平仓
            sell_price = close_price
            sell_date = d

        # 计算收益
        ret = (sell_price - buy_price) / buy_price
        new_capital = capital * (1 + ret)
        daily_returns.append(ret)

        trade = {
            "idx": i + 1,
            "date": d,
            "sell_date": sell_date,
            "ts_code": ts_code,
            "name": name,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "pct_chg_day": pct_chg,
            "holding_return": ret * 100,
            "capital_before": capital,
            "capital_after": new_capital,
            "multiple": new_capital / INITIAL_CAPITAL,
        }
        trades.append(trade)
        equity_curve.append({"date": d, "capital": new_capital})

        capital = new_capital

        # 每日输出
        marker = "★" if pct_chg >= 19.9 else ("◆" if pct_chg >= 10 else "·")
        if (i + 1) <= 10 or (i + 1) == len(trade_dates) - 1 or (i + 1) % 20 == 0:
            print(f"  Day{i+1:3d} {d} {marker} {name:6s} {ts_code}  "
                  f"买{buy_price:7.2f}→卖{sell_price:7.2f}  "
                  f"当日{pct_chg:+5.1f}%  收益{ret*100:+6.2f}%  "
                  f"资金{capital/10000:10.1f}万  ({capital/INITIAL_CAPITAL:.2f}x)")

    # ===== 统计 =====
    trades_df = pd.DataFrame(trades)
    daily_returns_arr = np.array(daily_returns)

    total_return = (capital / INITIAL_CAPITAL - 1) * 100
    days = len(trades)
    # 年化（252交易日）
    annualized = ((capital / INITIAL_CAPITAL) ** (252 / max(days, 1)) - 1) * 100
    # 夏普（日收益均值/标准差 * sqrt(252)）
    if len(daily_returns_arr) > 1 and np.std(daily_returns_arr) > 0:
        sharpe = np.mean(daily_returns_arr) / np.std(daily_returns_arr) * np.sqrt(252)
    else:
        sharpe = 0
    # 最大回撤
    equity = [INITIAL_CAPITAL] + [e["capital"] for e in equity_curve]
    peak = equity[0]
    max_dd = 0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd
    # 胜率
    wins = (trades_df["holding_return"] > 0).sum()
    win_rate = wins / len(trades_df) * 100

    # 平均持仓收益
    avg_ret = trades_df["holding_return"].mean()
    best_trade = trades_df.loc[trades_df["holding_return"].idxmax()]
    worst_trade = trades_df.loc[trades_df["holding_return"].idxmin()]

    # 涨停板次数（当日涨幅>=19.9%）
    limit_up_count = (trades_df["pct_chg_day"] >= 19.9).sum()
    # 大涨(>=10%)次数
    big_gain = (trades_df["pct_chg_day"] >= 10).sum()

    print(f"\n{'='*90}")
    print(f"★ 回测结果")
    print(f"{'='*90}")
    print(f"  初始资金:     {INITIAL_CAPITAL/10000:.0f}万")
    print(f"  最终资金:     {capital/10000:,.1f}万")
    print(f"  总收益:       {total_return:+,.1f}%  ({capital/INITIAL_CAPITAL:.2f}倍)")
    print(f"  交易天数:     {days}")
    print(f"  年化收益:     {annualized:+,.1f}%")
    print(f"  夏普比率:     {sharpe:.2f}")
    print(f"  最大回撤:     {max_dd*100:.1f}%")
    print(f"  胜率:         {win_rate:.1f}%  ({wins}/{len(trades_df)})")
    print(f"  平均每日收益: {avg_ret:+.2f}%")
    print(f"  涨停板买入:   {limit_up_count}次 (占比{limit_up_count/len(trades_df)*100:.0f}%)")
    print(f"  大涨(≥10%):   {big_gain}次 (占比{big_gain/len(trades_df)*100:.0f}%)")
    print(f"\n  最佳交易: Day{best_trade['idx']} {best_trade['name']}({best_trade['ts_code']}) "
          f"{best_trade['date']} 收益{best_trade['holding_return']:+.1f}%")
    print(f"  最差交易: Day{worst_trade['idx']} {worst_trade['name']}({worst_trade['ts_code']}) "
          f"{worst_trade['date']} 收益{worst_trade['holding_return']:+.1f}%")

    # 基准对比（沪深300）
    print(f"\n  === 基准对比（沪深300同期）===")
    from davis_analyzer.studies.rally_screening.utils import _conn
    with _conn() as conn:
        hs300 = pd.read_sql(
            "SELECT trade_date, open, close FROM index_daily "
            "WHERE ts_code='000300.SH' AND trade_date >= ? AND trade_date <= ? "
            "ORDER BY trade_date",
            conn, params=(START_DATE, END_DATE),
        )
        sh = pd.read_sql(
            "SELECT trade_date, open, close FROM index_daily "
            "WHERE ts_code='000001.SH' AND trade_date >= ? AND trade_date <= ? "
            "ORDER BY trade_date",
            conn, params=(START_DATE, END_DATE),
        )
    if not hs300.empty:
        hs300_ret = (hs300.iloc[-1]["close"] / hs300.iloc[0]["open"] - 1) * 100
        print(f"  沪深300同期:  {hs300_ret:+.1f}%")
        print(f"  超额收益:     {total_return - hs300_ret:+.1f}%")

    if not sh.empty:
        sh_ret = (sh.iloc[-1]["close"] / sh.iloc[0]["open"] - 1) * 100
        print(f"  上证综指同期: {sh_ret:+.1f}%")

    # 保存交易记录
    trades_out = trades_df[["idx", "date", "sell_date", "ts_code", "name",
                             "buy_price", "sell_price", "pct_chg_day", "holding_return",
                             "capital_after", "multiple"]]
    OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
    trades_out.to_csv(f"{OUTPUT_DIR}/god_view_trades.csv", index=False)
    print(f"\n  交易记录已保存: {OUTPUT_DIR}/god_view_trades.csv")

    # 保存收益曲线
    eq_df = pd.DataFrame(equity_curve)
    eq_df.to_csv(f"{OUTPUT_DIR}/god_view_equity.csv", index=False)

    # 资金里程碑
    print(f"\n  === 资金里程碑 ===")
    milestones = [2, 3, 5, 10, 20, 50, 100, 200, 500, 1000]
    for m in milestones:
        hit = trades_df[trades_df["multiple"] >= m]
        if not hit.empty:
            first = hit.iloc[0]
            print(f"  {m:>5}x: Day{first['idx']:3d} {first['date']} ({first['name']})")

    # 月度收益分布
    print(f"\n  === 月度统计 ===")
    trades_df["month"] = trades_df["date"].str[:6]
    for month, group in trades_df.groupby("month"):
        m_start = group.iloc[0]["capital_before"]
        m_end = group.iloc[-1]["capital_after"]
        m_ret = (m_end / m_start - 1) * 100
        m_wins = (group["holding_return"] > 0).sum()
        print(f"  {month}: {len(group):2d}笔  月收益{m_ret:+7.1f}%  "
              f"胜率{m_wins/len(group)*100:.0f}%  资金{m_end/10000:>10,.0f}万")


if __name__ == "__main__":
    main()
