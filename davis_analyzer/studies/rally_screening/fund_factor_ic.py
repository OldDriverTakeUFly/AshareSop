"""资金面因子 IC + Quintile 分析。

因子：
1. 龙虎榜净买入额（top_list net_amount）
2. 超大单净流入（buy_elg - sell_elg）
3. 大单净流入（buy_lg - sell_lg）
4. 超大+大单合计净流入
5. 全口径净流入（net_mf_amount）

方法论（复用技术因子研究验证过的方法）：
- 每日计算 Spearman IC（因子值 vs 未来N天收益）
- Quintile 分组：每天按因子值分5组，统计Q5-Q1的N天收益价差
- 持有期：5d / 10d / 20d
- t 检验
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
import tushare as ts

pro = ts.pro_api()

DAILY_CACHE = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/rally_screening/cache_daily"
MSG_CACHE = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/rally_screening/cache_msg"


def load_daily_data():
    """加载全市场daily行情，构造 ts_code×trade_date 的 close 矩阵"""
    files = sorted([f for f in os.listdir(DAILY_CACHE) if f.startswith("daily_") and f.endswith(".pkl")])
    all_dfs = []
    for f in files:
        trade_date = f.replace("daily_", "").replace(".pkl", "")
        df = pd.read_pickle(f"{DAILY_CACHE}/{f}")
        df["trade_date"] = trade_date
        all_dfs.append(df[["ts_code", "trade_date", "close", "pct_chg", "amount"]])
    big = pd.concat(all_dfs, ignore_index=True)
    # 过滤
    big = big[big["ts_code"].str.startswith(("00", "30", "60", "68"))]
    big = big[big["amount"] > 10000]  # 成交额>1000万
    return big.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def load_moneyflow():
    """加载大单/超大单净流入"""
    files = sorted([f for f in os.listdir(MSG_CACHE) if f.startswith("moneyflow_") and f.endswith(".pkl")])
    all_dfs = []
    for f in files:
        trade_date = f.replace("moneyflow_", "").replace(".pkl", "")
        df = pd.read_pickle(f"{MSG_CACHE}/{f}")
        df["trade_date"] = trade_date
        all_dfs.append(df[["ts_code", "trade_date",
                           "buy_elg_amount", "sell_elg_amount",
                           "buy_lg_amount", "sell_lg_amount",
                           "net_mf_amount"]])
    big = pd.concat(all_dfs, ignore_index=True)
    # 计算各因子
    big["elg_net"] = big["buy_elg_amount"].fillna(0) - big["sell_elg_amount"].fillna(0)
    big["lg_net"] = big["buy_lg_amount"].fillna(0) - big["sell_lg_amount"].fillna(0)
    big["big_net_total"] = big["elg_net"] + big["lg_net"]
    return big


def load_top_list():
    """加载龙虎榜净买入"""
    files = sorted([f for f in os.listdir(MSG_CACHE) if f.startswith("top_list_") and f.endswith(".pkl")])
    all_dfs = []
    for f in files:
        trade_date = f.replace("top_list_", "").replace(".pkl", "")
        df = pd.read_pickle(f"{MSG_CACHE}/{f}")
        if df is not None and not df.empty:
            df = df[["ts_code", "net_amount"]].copy()
            df["trade_date"] = trade_date
            df["on_top_list"] = 1
            all_dfs.append(df)
    if not all_dfs:
        return pd.DataFrame()
    big = pd.concat(all_dfs, ignore_index=True)
    big["top_net_amount"] = big["net_amount"]
    return big[["ts_code", "trade_date", "on_top_list", "top_net_amount"]]


def compute_forward_returns(daily_df, periods=[5, 10, 20]):
    """计算未来N天收益"""
    daily_df = daily_df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    for n in periods:
        daily_df[f"ret_{n}d"] = daily_df.groupby("ts_code")["close"].pct_change(n).shift(-n)
    return daily_df


def analyze_factor(factor_df, factor_col, label, periods=[5, 10, 20]):
    """对单个因子做 IC + Quintile 分析"""
    print(f"\n{'='*85}")
    print(f"因子: {label} ({factor_col})")
    print(f"{'='*85}")

    for n in periods:
        ret_col = f"ret_{n}d"
        valid = factor_df.dropna(subset=[factor_col, ret_col]).copy()
        if len(valid) < 500:
            print(f"  {n}d: 样本不足({len(valid)})")
            continue

        # === IC ===
        ic, p_val = spearmanr(valid[factor_col], valid[ret_col])
        ic_mean = ic
        # 按 daily 分组算 daily IC 的均值
        daily_ics = []
        for date, group in valid.groupby("trade_date"):
            if len(group) > 30:
                ic_d, _ = spearmanr(group[factor_col], group[ret_col])
                if not np.isnan(ic_d):
                    daily_ics.append(ic_d)
        daily_ic_mean = np.mean(daily_ics) if daily_ics else 0
        daily_ic_std = np.std(daily_ics) if daily_ics else 0
        icir = daily_ic_mean / daily_ic_std if daily_ic_std > 0 else 0

        # === Quintile ===
        # 按 factor_col 分5组（每天分组，避免时间偏差）
        valid["quintile"] = valid.groupby("trade_date")[factor_col].transform(
            lambda x: pd.qcut(x, 5, labels=False, duplicates="drop") if x.nunique() > 4 else 0
        )
        quintile_stats = valid.groupby("quintile")[ret_col].agg(["mean", "std", "count"])
        q5_q1 = quintile_stats.loc[4, "mean"] - quintile_stats.loc[0, "mean"] if 4 in quintile_stats.index and 0 in quintile_stats.index else 0

        # t-test Q5 vs Q1
        q5_rets = valid[valid["quintile"] == 4][ret_col].dropna()
        q1_rets = valid[valid["quintile"] == 0][ret_col].dropna()
        if len(q5_rets) > 30 and len(q1_rets) > 30:
            t_stat, t_p = ttest_ind(q5_rets, q1_rets)
        else:
            t_stat, t_p = 0, 1

        monotonic = "✅" if all(quintile_stats["mean"].diff().dropna() > 0) else ("⚠️" if quintile_stats["mean"].is_monotonic_decreasing else "❌")

        print(f"\n  ── {n}天持有期 ──")
        print(f"  Overall IC: {ic:+.4f} (p={t_p:.2e})")
        print(f"  Daily IC均值: {daily_ic_mean:+.4f}  IC标准差: {daily_ic_std:.4f}  ICIR: {icir:+.2f}")
        print(f"  Quintile收益:")
        for q in sorted(quintile_stats.index):
            row = quintile_stats.loc[q]
            print(f"    Q{q+1}: {row['mean']*100:+.2f}%  (n={int(row['count']):,})")
        print(f"  Q5-Q1: {q5_q1*100:+.2f}%  t={t_stat:.1f}  {monotonic}")

    return quintile_stats


def main():
    print("=" * 85)
    print("资金面因子 IC + Quintile 分析")
    print("=" * 85)

    # === 加载数据 ===
    print("\n加载 daily 行情...")
    daily_df = load_daily_data()
    print(f"  {len(daily_df):,} 行")

    print("加载 moneyflow...")
    mf_df = load_moneyflow()
    print(f"  {len(mf_df):,} 行")

    print("加载龙虎榜...")
    tl_df = load_top_list()
    print(f"  {len(tl_df):,} 行")

    # === 计算未来收益 ===
    print("计算未来收益...")
    daily_df = compute_forward_returns(daily_df, periods=[5, 10, 20])
    print(f"  完成")

    # === 合并因子 ===
    print("合并因子表...")
    factor_df = daily_df.merge(
        mf_df[["ts_code", "trade_date", "elg_net", "lg_net", "big_net_total", "net_mf_amount"]],
        on=["ts_code", "trade_date"], how="left"
    )
    factor_df["elg_net"] = factor_df["elg_net"].fillna(0)
    factor_df["lg_net"] = factor_df["lg_net"].fillna(0)
    factor_df["big_net_total"] = factor_df["big_net_total"].fillna(0)
    factor_df["net_mf_amount"] = factor_df["net_mf_amount"].fillna(0)

    # 龙虎榜：不上榜的标的 net_amount=0，on_top_list=0
    if not tl_df.empty:
        factor_df = factor_df.merge(
            tl_df[["ts_code", "trade_date", "on_top_list", "top_net_amount"]],
            on=["ts_code", "trade_date"], how="left"
        )
        factor_df["on_top_list"] = factor_df["on_top_list"].fillna(0).astype(int)
        factor_df["top_net_amount"] = factor_df["top_net_amount"].fillna(0)
    else:
        factor_df["on_top_list"] = 0
        factor_df["top_net_amount"] = 0

    print(f"  合并后: {len(factor_df):,} 行")

    # === 逐因子分析 ===
    # 因子1: 龙虎榜净买入（仅上榜的有值，全市场口径）
    analyze_factor(factor_df, "top_net_amount", "龙虎榜净买入额（全市场，不上榜=0）")

    # 因子1b: 龙虎榜净买入（仅上榜标的内部排名）
    on_list = factor_df[factor_df["on_top_list"] == 1].copy()
    if len(on_list) > 1000:
        analyze_factor(on_list, "top_net_amount", "龙虎榜净买入额（仅上榜标的内部排名）")

    # 因子2: 超大单净流入
    analyze_factor(factor_df, "elg_net", "超大单净流入")

    # 因子3: 大单净流入
    analyze_factor(factor_df, "lg_net", "大单净流入")

    # 因子4: 超大+大单合计
    analyze_factor(factor_df, "big_net_total", "超大单+大单合计净流入")

    # 因子5: 全口径净流入
    analyze_factor(factor_df, "net_mf_amount", "全口径资金净流入(net_mf)")

    # === 汇总 ===
    print(f"\n{'='*85}")
    print("★ 汇总：各因子 20天 Q5-Q1 价差对比")
    print(f"{'='*85}")
    print(f"  （对比技术因子基准：RSI +1.98%, boll +1.37%, tech_score +1.14%）")


if __name__ == "__main__":
    main()
