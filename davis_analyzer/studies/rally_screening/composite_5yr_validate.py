"""5年窗口合成因子验证：龙虎榜 × 技术因子。

验证 139 天窗口的结论是否在 5 年(1326天)下成立：
1. 龙虎榜净买入（仅上榜内部排名）是否仍是 Q5-Q1 最大的因子
2. "龙虎榜+RSI≤50 → -7.16%" 灾难组是否在5年依然成立
3. Z-score 合成 vs 门控组合 哪个更优

新增5年特有的分析：
4. 分年度稳健性——因子在不同年份的表现差异
5. 分市场环境（牛/熊/震荡）的表现差异
"""
import os
from dotenv import load_dotenv
load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

import sys
import loguru
loguru.logger.remove()

import numpy as np
import pandas as pd
from scipy.stats import spearmanr, ttest_ind
import sqlite3
from davis_analyzer.tushare_client import _CACHE_DB


def load_data():
    """从SQLite加载tech_factor + top_list + daily_price"""
    print("加载 tech_factor...")
    with sqlite3.connect(str(_CACHE_DB)) as conn:
        tf = pd.read_sql("SELECT ts_code, trade_date, tech_score, rsi, boll_position, kdj_j, ma_align_score FROM tech_factor", conn)
    tf["trade_date"] = tf["trade_date"].astype(str)
    print(f"  {len(tf):,} 行")

    print("加载 top_list...")
    with sqlite3.connect(str(_CACHE_DB)) as conn:
        tl = pd.read_sql("SELECT ts_code, trade_date, net_amount FROM top_list", conn)
    tl["trade_date"] = tl["trade_date"].astype(str)
    tl["on_top_list"] = 1
    tl["top_net_amount"] = tl["net_amount"]
    print(f"  {len(tl):,} 行")

    print("加载 daily_price + 计算未来收益...")
    with sqlite3.connect(str(_CACHE_DB)) as conn:
        dp = pd.read_sql("SELECT ts_code, trade_date, close, amount FROM daily_price", conn)
    dp["trade_date"] = dp["trade_date"].astype(str)
    dp = dp[dp["ts_code"].str.startswith(("00", "30", "60", "68"))]
    dp = dp[dp["amount"] > 10000]
    dp = dp.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    for n in [5, 10, 20]:
        dp[f"ret_{n}d"] = dp.groupby("ts_code")["close"].pct_change(n).shift(-n)
    print(f"  {len(dp):,} 行")

    print("合并...")
    df = dp.merge(tf, on=["ts_code", "trade_date"], how="left")
    df = df.merge(tl[["ts_code", "trade_date", "on_top_list", "top_net_amount"]],
                  on=["ts_code", "trade_date"], how="left")
    df["on_top_list"] = df["on_top_list"].fillna(0).astype(int)
    df["top_net_amount"] = df["top_net_amount"].fillna(0)
    print(f"  合并后: {len(df):,} 行")
    return df


def quintile_analysis(df, factor_col, ret_col, label=""):
    valid = df.dropna(subset=[factor_col, ret_col]).copy()
    if len(valid) < 500:
        print(f"  {label}: 样本不足({len(valid)})")
        return None

    daily_ics = []
    for date, group in valid.groupby("trade_date"):
        if len(group) > 30:
            ic_d, _ = spearmanr(group[factor_col], group[ret_col])
            if not np.isnan(ic_d):
                daily_ics.append(ic_d)
    ic_mean = np.mean(daily_ics) if daily_ics else 0
    ic_std = np.std(daily_ics) if daily_ics else 0

    valid["quintile"] = valid.groupby("trade_date")[factor_col].transform(
        lambda x: pd.qcut(x, 5, labels=False, duplicates="drop") if x.nunique() > 4 else 2
    )
    qs = valid.groupby("quintile")[ret_col].agg(["mean", "count"])
    q5_q1 = qs.loc[4, "mean"] - qs.loc[0, "mean"] if 4 in qs.index and 0 in qs.index else 0
    q5 = valid[valid["quintile"] == 4][ret_col].dropna()
    q1 = valid[valid["quintile"] == 0][ret_col].dropna()
    t_stat = ttest_ind(q5, q1)[0] if len(q5) > 30 and len(q1) > 30 else 0
    mono = "✅" if all(qs["mean"].diff().dropna() > 0) else ("⚠️" if qs["mean"].is_monotonic_decreasing else "")

    print(f"  {label}")
    print(f"    IC={ic_mean:+.4f} (IR={ic_mean/ic_std:.2f})  Q5-Q1={q5_q1*100:+.2f}%  t={t_stat:.1f}  {mono}")
    for q in sorted(qs.index):
        print(f"      Q{q+1}: {qs.loc[q, 'mean']*100:+.2f}% (n={int(qs.loc[q, 'count']):,})")
    return {"ic": ic_mean, "q5_q1": q5_q1, "t": t_stat, "label": label}


def main():
    print("=" * 90)
    print("5年窗口合成因子验证（2021-02 ~ 2026-07，1326天）")
    print("=" * 90)

    df = load_data()

    # === Part 1: 单因子基准（5年）===
    print(f"\n{'='*90}")
    print("Part 1: 单因子基准（5年）")
    print(f"{'='*90}")

    for n in [20]:
        ret_col = f"ret_{n}d"
        print(f"\n  ── {n}天持有期 ──")
        # 龙虎榜（仅上榜内部排名）
        on_list = df[df["on_top_list"] == 1].copy()
        quintile_analysis(on_list, "top_net_amount", ret_col, "龙虎榜净买入（仅上榜内部，5年）")
        # 技术因子
        for col in ["rsi", "boll_position", "tech_score"]:
            quintile_analysis(df, col, ret_col, f"{col}（5年）")

    # === Part 2: 门控组合（5年）—— 核心验证 ===
    print(f"\n{'='*90}")
    print("Part 2: 门控组合验证（5年）—— 核心问题：139天的-7.16%是否依然成立")
    print(f"{'='*90}")

    for n in [20]:
        ret_col = f"ret_{n}d"
        print(f"\n  ── {n}天持有期 ──")

        groups = [
            ("龙虎榜买入+RSI>50", (df["on_top_list"]==1) & (df["top_net_amount"]>0) & (df["rsi"]>50)),
            ("龙虎榜买入+RSI≤50", (df["on_top_list"]==1) & (df["top_net_amount"]>0) & (df["rsi"]<=50)),
            ("龙虎榜买入(不管RSI)", (df["on_top_list"]==1) & (df["top_net_amount"]>0)),
            ("龙虎榜卖出", (df["on_top_list"]==1) & (df["top_net_amount"]<0)),
            ("不上榜", df["on_top_list"]==0),
            ("全市场基准", pd.Series(True, index=df.index)),
        ]
        print(f"    {'组合':25s} {'n':>8} {'均值':>8} {'vs基准':>8}")
        print("    " + "-"*60)
        base_ret = df[ret_col].mean()
        for label, mask in groups:
            g = df[mask].dropna(subset=[ret_col])
            if len(g) == 0:
                continue
            mean_ret = g[ret_col].mean()
            print(f"    {label:25s} {len(g):>8,} {mean_ret*100:>+7.2f}% {(mean_ret-base_ret)*100:>+7.2f}%")

        # A vs B t检验
        mask_a = (df["on_top_list"]==1) & (df["top_net_amount"]>0) & (df["rsi"]>50)
        mask_b = (df["on_top_list"]==1) & (df["top_net_amount"]>0) & (df["rsi"]<=50)
        ga = df[mask_a].dropna(subset=[ret_col])[ret_col]
        gb = df[mask_b].dropna(subset=[ret_col])[ret_col]
        if len(ga) > 30 and len(gb) > 30:
            t = ttest_ind(ga, gb)[0]
            print(f"\n    A(RSI>50) vs B(RSI≤50): 差异={(ga.mean()-gb.mean())*100:+.2f}%  t={t:.1f}")

    # === Part 3: 分年度稳健性 ===
    print(f"\n{'='*90}")
    print("Part 3: 分年度——龙虎榜门控组合的稳健性")
    print(f"{'='*90}")

    df["year"] = df["trade_date"].str[:4]
    print(f"\n  {'年份':>6} {'A(龙+RSI>50)':>14} {'B(龙+RSI≤50)':>14} {'A-B差异':>10} {'基准':>8}")
    print("  " + "-"*65)

    for year in sorted(df["year"].unique()):
        yd = df[df["year"] == year]
        ret_col = "ret_20d"
        mask_a = (yd["on_top_list"]==1) & (yd["top_net_amount"]>0) & (yd["rsi"]>50)
        mask_b = (yd["on_top_list"]==1) & (yd["top_net_amount"]>0) & (yd["rsi"]<=50)
        ga = yd[mask_a].dropna(subset=[ret_col])
        gb = yd[mask_b].dropna(subset=[ret_col])
        base = yd[ret_col].mean()

        a_ret = ga[ret_col].mean()*100 if len(ga) > 0 else 0
        b_ret = gb[ret_col].mean()*100 if len(gb) > 0 else 0
        diff = a_ret - b_ret
        print(f"  {year:>6} {a_ret:>+13.2f}%({len(ga):>4}) {b_ret:>+13.2f}%({len(gb):>4}) {diff:>+9.2f}% {base*100:>+7.2f}%")

    # === Part 4: Z-score 合成（5年）===
    print(f"\n{'='*90}")
    print("Part 4: Z-score 合成（5年）")
    print(f"{'='*90}")

    for col in ["top_net_amount", "rsi", "boll_position", "tech_score"]:
        z_col = f"{col}_z"
        df[z_col] = df.groupby("trade_date")[col].transform(
            lambda x: (x - x.mean()) / x.std() if x.std() > 0 else x * 0
        )

    for n in [20]:
        ret_col = f"ret_{n}d"
        print(f"\n  ── {n}天持有期 ──")
        df["combo_top_rsi"] = df["top_net_amount_z"] * 0.5 + df["rsi_z"] * 0.5
        quintile_analysis(df, "combo_top_rsi", ret_col, "合成: 龙50%+RSI50%（5年）")
        df["rank_combo_3"] = df.groupby("trade_date")["top_net_amount"].rank(pct=True) * 0.4 + \
                              df.groupby("trade_date")["rsi"].rank(pct=True) * 0.3 + \
                              df.groupby("trade_date")["tech_score"].rank(pct=True) * 0.3
        quintile_analysis(df, "rank_combo_3", ret_col, "排名合成: 龙40%+RSI30%+tech30%（5年）")


if __name__ == "__main__":
    main()
