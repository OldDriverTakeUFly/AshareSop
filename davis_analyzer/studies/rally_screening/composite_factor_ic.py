"""龙虎榜 × 技术因子 合成因子分析。

合成方式：
1. 双重排序（conditional sort）：先按龙虎榜分2组（上榜/不上榜），
   再在每组内按技术因子排序，看条件 quintile
2. Z-score 合成：龙虎榜 z-score × 0.5 + 技术因子 z-score × 0.5
3. 排名合成：龙虎榜排名百分位 × w1 + 技术排名百分位 × w2
4. 门控组合：龙虎榜净买入>0 且 技术因子 >阈值 → 等权组合

关键问题：1+1 是否 > 2？
- 龙虎榜单因子 20d Q5-Q1 = +6.07%
- RSI 单因子 20d Q5-Q1 = +1.98%
- 合成后能否超过两者最优？
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

DAILY_CACHE = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/rally_screening/cache_daily"
MSG_CACHE = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/rally_screening/cache_msg"


def load_all_data():
    """合并 tech_factor + 资金面 + daily行情 + 未来收益"""
    # 1. tech_factor
    with sqlite3.connect(str(_CACHE_DB)) as conn:
        tf = pd.read_sql("SELECT * FROM tech_factor", conn)
    tf = tf[["ts_code", "trade_date", "tech_score", "rsi", "boll_position", "kdj_j", "ma_align_score", "macd_hist"]].copy()
    tf["trade_date"] = tf["trade_date"].astype(str)
    print(f"  tech_factor: {len(tf):,} 行")

    # 2. daily 行情 + 未来收益
    files = sorted([f for f in os.listdir(DAILY_CACHE) if f.startswith("daily_") and f.endswith(".pkl")])
    all_dfs = []
    for f in files:
        trade_date = f.replace("daily_", "").replace(".pkl", "")
        df = pd.read_pickle(f"{DAILY_CACHE}/{f}")
        df["trade_date"] = trade_date
        all_dfs.append(df[["ts_code", "trade_date", "close", "amount"]])
    daily = pd.concat(all_dfs, ignore_index=True)
    daily = daily[daily["ts_code"].str.startswith(("00", "30", "60", "68"))]
    daily = daily[daily["amount"] > 10000]
    daily = daily.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    # 未来收益
    for n in [5, 10, 20]:
        daily[f"ret_{n}d"] = daily.groupby("ts_code")["close"].pct_change(n).shift(-n)
    print(f"  daily: {len(daily):,} 行")

    # 3. 龙虎榜
    tl_files = sorted([f for f in os.listdir(MSG_CACHE) if f.startswith("top_list_") and f.endswith(".pkl")])
    tl_dfs = []
    for f in tl_files:
        trade_date = f.replace("top_list_", "").replace(".pkl", "")
        df = pd.read_pickle(f"{MSG_CACHE}/{f}")
        if df is not None and not df.empty:
            df = df[["ts_code", "net_amount"]].copy()
            df["trade_date"] = trade_date
            tl_dfs.append(df)
    tl = pd.concat(tl_dfs, ignore_index=True) if tl_dfs else pd.DataFrame()
    tl["on_top_list"] = 1
    tl["top_net_amount"] = tl["net_amount"]
    tl = tl[["ts_code", "trade_date", "on_top_list", "top_net_amount"]]
    print(f"  top_list: {len(tl):,} 行")

    # 4. 合并
    df = daily.merge(tf, on=["ts_code", "trade_date"], how="left")
    df = df.merge(tl, on=["ts_code", "trade_date"], how="left")
    df["on_top_list"] = df["on_top_list"].fillna(0).astype(int)
    df["top_net_amount"] = df["top_net_amount"].fillna(0)
    print(f"  合并后: {len(df):,} 行")

    return df


def quintile_analysis(df, factor_col, ret_col, label=""):
    """标准 quintile 分析"""
    valid = df.dropna(subset=[factor_col, ret_col]).copy()
    if len(valid) < 500:
        return None

    # IC
    daily_ics = []
    for date, group in valid.groupby("trade_date"):
        if len(group) > 30:
            ic_d, _ = spearmanr(group[factor_col], group[ret_col])
            if not np.isnan(ic_d):
                daily_ics.append(ic_d)
    ic_mean = np.mean(daily_ics) if daily_ics else 0
    ic_std = np.std(daily_ics) if daily_ics else 0

    # Quintile
    valid["quintile"] = valid.groupby("trade_date")[factor_col].transform(
        lambda x: pd.qcut(x, 5, labels=False, duplicates="drop") if x.nunique() > 4 else 2
    )
    qs = valid.groupby("quintile")[ret_col].agg(["mean", "count"])
    q5_q1 = qs.loc[4, "mean"] - qs.loc[0, "mean"] if 4 in qs.index and 0 in qs.index else 0

    q5 = valid[valid["quintile"] == 4][ret_col].dropna()
    q1 = valid[valid["quintile"] == 0][ret_col].dropna()
    t_stat = ttest_ind(q5, q1)[0] if len(q5) > 30 and len(q1) > 30 else 0

    mono = "✅" if all(qs["mean"].diff().dropna() > 0) else ("⚠️" if qs["mean"].is_monotonic_decreasing else "")

    print(f"\n  {label}")
    print(f"    IC={ic_mean:+.4f} (IR={ic_mean/ic_std:.2f})  Q5-Q1={q5_q1*100:+.2f}%  t={t_stat:.1f}  {mono}")
    for q in sorted(qs.index):
        print(f"      Q{q+1}: {qs.loc[q, 'mean']*100:+.2f}% (n={int(qs.loc[q, 'count']):,})")

    return {"ic": ic_mean, "q5_q1": q5_q1, "t": t_stat, "label": label}


def main():
    print("=" * 90)
    print("龙虎榜 × 技术因子 合成分析")
    print("=" * 90)

    df = load_all_data()

    # ============================================================
    # Part 1: 单因子基准（在合并后的同一数据集上重算，确保可比）
    # ============================================================
    print(f"\n{'='*90}")
    print("Part 1: 单因子基准（同一数据集 139天可比）")
    print(f"{'='*90}")

    # 龙虎榜因子：需要做 z-score 归一化（全市场口径，不上榜=0）
    # 但为了与之前研究可比，分两个口径
    # 口径A: 全市场（不上榜=0）
    # 口径B: 仅上榜

    # 技术因子先做日度横截面 rank percentile（0-1）
    tech_cols = ["rsi", "boll_position", "tech_score", "kdj_j"]
    for col in tech_cols:
        df[f"{col}_rank"] = df.groupby("trade_date")[col].rank(pct=True)

    # 龙虎榜 rank
    df["top_net_rank"] = df.groupby("trade_date")["top_net_amount"].rank(pct=True)

    for n in [20]:
        ret_col = f"ret_{n}d"
        print(f"\n  ── {n}天持有期 ──")
        results = []
        # 单因子基准
        r = quintile_analysis(df, "top_net_amount", ret_col, "龙虎榜净买入（全市场）")
        if r: results.append(r)

        for col in ["rsi", "boll_position", "tech_score"]:
            r = quintile_analysis(df, col, ret_col, f"{col}（单因子）")
            if r: results.append(r)

    # ============================================================
    # Part 2: 合成因子 — Z-score 等权
    # ============================================================
    print(f"\n{'='*90}")
    print("Part 2: Z-score 等权合成")
    print(f"{'='*90}")

    # 日度横截面 z-score
    def daily_zscore(group, col):
        s = group[col]
        std = s.std()
        return (s - s.mean()) / std if std > 0 else s * 0

    for col in ["top_net_amount", "rsi", "boll_position", "tech_score"]:
        z_col = f"{col}_z"
        df[z_col] = df.groupby("trade_date")[col].transform(
            lambda x: (x - x.mean()) / x.std() if x.std() > 0 else x * 0
        )

    for n in [20]:
        ret_col = f"ret_{n}d"
        print(f"\n  ── {n}天持有期 ──")

        # 合成1: 龙虎榜 + RSI
        df["combo_top_rsi"] = df["top_net_amount_z"] * 0.5 + df["rsi_z"] * 0.5
        quintile_analysis(df, "combo_top_rsi", ret_col, "合成: 龙虎榜50% + RSI 50%")

        # 合成2: 龙虎榜 + boll
        df["combo_top_boll"] = df["top_net_amount_z"] * 0.5 + df["boll_position_z"] * 0.5
        quintile_analysis(df, "combo_top_boll", ret_col, "合成: 龙虎榜50% + BOLL 50%")

        # 合成3: 龙虎榜 + tech_score
        df["combo_top_tech"] = df["top_net_amount_z"] * 0.5 + df["tech_score_z"] * 0.5
        quintile_analysis(df, "combo_top_tech", ret_col, "合成: 龙虎榜50% + tech_score 50%")

        # 合成4: 三因子
        df["combo_top_rsi_boll"] = df["top_net_amount_z"] * 0.4 + df["rsi_z"] * 0.3 + df["boll_position_z"] * 0.3
        quintile_analysis(df, "combo_top_rsi_boll", ret_col, "合成: 龙虎榜40% + RSI 30% + BOLL 30%")

        # 合成5: 四因子
        df["combo_4"] = (df["top_net_amount_z"] * 0.3 + df["rsi_z"] * 0.25 +
                         df["boll_position_z"] * 0.25 + df["tech_score_z"] * 0.2)
        quintile_analysis(df, "combo_4", ret_col, "合成: 龙30% + RSI25% + BOLL25% + tech20%")

    # ============================================================
    # Part 3: 排名合成
    # ============================================================
    print(f"\n{'='*90}")
    print("Part 3: 排名百分位合成（更稳健，不受异常值影响）")
    print(f"{'='*90}")

    for n in [20]:
        ret_col = f"ret_{n}d"
        print(f"\n  ── {n}天持有期 ──")

        df["rank_combo_top_rsi"] = df["top_net_rank"] * 0.5 + df["rsi_rank"] * 0.5
        quintile_analysis(df, "rank_combo_top_rsi", ret_col, "排名合成: 龙50% + RSI 50%")

        df["rank_combo_top_tech"] = df["top_net_rank"] * 0.5 + df["tech_score_rank"] * 0.5
        quintile_analysis(df, "rank_combo_top_tech", ret_col, "排名合成: 龙50% + tech 50%")

        df["rank_combo_3"] = df["top_net_rank"] * 0.4 + df["rsi_rank"] * 0.3 + df["tech_score_rank"] * 0.3
        quintile_analysis(df, "rank_combo_3", ret_col, "排名合成: 龙40% + RSI30% + tech30%")

    # ============================================================
    # Part 4: 门控组合（gating）
    # ============================================================
    print(f"\n{'='*90}")
    print("Part 4: 门控组合（龙虎榜上榜+技术确认 vs 无门控）")
    print(f"{'='*90}")

    for n in [20]:
        ret_col = f"ret_{n}d"
        print(f"\n  ── {n}天持有期 ──")

        # 组A: 龙虎榜净买入>0 AND RSI>50（技术确认）
        mask_a = (df["on_top_list"] == 1) & (df["top_net_amount"] > 0) & (df["rsi"] > 50)
        group_a = df[mask_a].dropna(subset=[ret_col])
        # 组B: 龙虎榜净买入>0 AND RSI<50（技术不确认）
        mask_b = (df["on_top_list"] == 1) & (df["top_net_amount"] > 0) & (df["rsi"] <= 50)
        group_b = df[mask_b].dropna(subset=[ret_col])
        # 组C: 龙虎榜净买入>0（不管技术）
        mask_c = (df["on_top_list"] == 1) & (df["top_net_amount"] > 0)
        group_c = df[mask_c].dropna(subset=[ret_col])
        # 基准: 全市场
        group_all = df.dropna(subset=[ret_col])

        print(f"    组A 龙虎榜买入+RSI>50:  n={len(group_a):>6,}  均值={group_a[ret_col].mean()*100:+.2f}%")
        print(f"    组B 龙虎榜买入+RSI≤50:  n={len(group_b):>6,}  均值={group_b[ret_col].mean()*100:+.2f}%")
        print(f"    组C 龙虎榜买入(不管RSI): n={len(group_c):>6,}  均值={group_c[ret_col].mean()*100:+.2f}%")
        print(f"    基准 全市场:             n={len(group_all):>6,}  均值={group_all[ret_col].mean()*100:+.2f}%")
        if len(group_a) > 30 and len(group_b) > 30:
            t = ttest_ind(group_a[ret_col].dropna(), group_b[ret_col].dropna())[0]
            print(f"    A vs B 差异: {group_a[ret_col].mean()*100 - group_b[ret_col].mean()*100:+.2f}%  t={t:.1f}")

        # 门控2: 龙虎榜净买入>0 AND boll_position<0.5（低位确认）
        mask_d = (df["on_top_list"] == 1) & (df["top_net_amount"] > 0) & (df["boll_position"] < 0.5)
        group_d = df[mask_d].dropna(subset=[ret_col])
        mask_e = (df["on_top_list"] == 1) & (df["top_net_amount"] > 0) & (df["boll_position"] >= 0.5)
        group_e = df[mask_e].dropna(subset=[ret_col])
        print(f"\n    组D 龙虎榜买入+BOLL<0.5(低位): n={len(group_d):>6,}  均值={group_d[ret_col].mean()*100:+.2f}%")
        print(f"    组E 龙虎榜买入+BOLL≥0.5(高位): n={len(group_e):>6,}  均值={group_e[ret_col].mean()*100:+.2f}%")


if __name__ == "__main__":
    main()
