"""国产光刻胶景气度分析 V2——修正yoy计算。

问题：引擎的 yoy_revenue_growth 算出 +0.3%（不对，真实值+33%）
原因：引擎用累计数且去重有bug
方案：自己从 income 原始数据算单季同比，再喂给 batch_prosperity
"""
import os
from dotenv import load_dotenv
load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

import sys
import loguru
loguru.logger.remove()
loguru.logger.add(sys.stderr, level="ERROR")

import numpy as np
import pandas as pd
import tushare as ts
from datetime import datetime
pro = ts.pro_api()

PHOTORESIST_STOCKS = {
    "300346.SZ": ("南大光电", "KrF/ArF光刻胶+前驱体", "高端"),
    "300236.SZ": ("上海新阳", "ArF光刻胶+电镀液", "高端"),
    "603306.SH": ("华懋科技", "光刻胶单体+气囊材料", "高端"),
    "300655.SZ": ("晶瑞电材", "i线光刻胶+ArF研发", "中高端"),
    "300576.SZ": ("容大感光", "PCB光刻胶+感光油墨", "中端"),
    "300537.SZ": ("广信材料", "PCB光刻胶+UV涂料", "中端"),
    "300398.SZ": ("飞凯材料", "光刻胶+半导体材料", "中端"),
    "300429.SZ": ("强力新材", "光刻胶光引发剂+树脂", "上游"),
    "688199.SH": ("久日新材", "光刻胶树脂+光引发剂", "上游"),
    "603650.SH": ("彤程新材", "橡胶助剂+北京科华光刻胶", "延伸"),
    "002643.SZ": ("万润股份", "显示材料+光刻胶中间体", "延伸"),
    "603110.SH": ("东方材料", "油墨+光刻胶(拟收购)", "延伸"),
    "688019.SH": ("安集科技", "CMP抛光液+光刻胶去除剂", "配套"),
}


def fetch_raw_financials(ts_code, periods=8):
    """直接从 Tushare income+fina_indicator 拉取，自己算单季同比"""
    # 拉收入
    income = pro.income(ts_code=ts_code, fields="ts_code,end_date,ann_date,total_revenue,n_income,n_income_attr_p")
    if income is None or income.empty:
        return None
    income = income.drop_duplicates(subset=["end_date"]).sort_values("end_date", ascending=False).head(periods)

    # 拉指标
    fina = pro.fina_indicator(ts_code=ts_code, fields="ts_code,end_date,ann_date,roe,grossprofit_margin,dt_roe")
    if fina is not None and not fina.empty:
        fina = fina.drop_duplicates(subset=["end_date"]).sort_values("end_date", ascending=False).head(periods)
    else:
        fina = pd.DataFrame()

    # 合并
    df = income.merge(fina[["end_date","roe","grossprofit_margin"]], on="end_date", how="left") if not fina.empty else income
    df = df.sort_values("end_date", ascending=False).reset_index(drop=True)

    # 计算单季数据（累计 - 上期累计）
    df = df.sort_values("end_date").reset_index(drop=True)
    df["quarter_rev"] = df["total_revenue"]
    df["quarter_np"] = df["n_income_attr_p"]

    # 拆单季：Q1不拆，其他季度 = 本期累计 - 上期累计
    for i in range(len(df)-1, 0, -1):
        curr_q = df.loc[i, "end_date"][4:6]
        if curr_q != "03":  # 非Q1，减去上一期
            df.loc[i, "quarter_rev"] = df.loc[i, "total_revenue"] - df.loc[i-1, "total_revenue"]
            df.loc[i, "quarter_np"] = df.loc[i, "n_income_attr_p"] - df.loc[i-1, "n_income_attr_p"]

    # 计算同比（本期单季 vs 去年同期单季）
    df["rev_yoy"] = np.nan
    df["np_yoy"] = np.nan
    for i in range(len(df)):
        curr_end = df.loc[i, "end_date"]
        curr_q = curr_end[4:6]
        # 找去年同季
        prev_year = str(int(curr_end[:4]) - 1) + curr_q + curr_end[6:]
        match = df[df["end_date"] == prev_year]
        if not match.empty:
            j = match.index[0]
            if pd.notna(df.loc[i, "quarter_rev"]) and pd.notna(df.loc[j, "quarter_rev"]) and df.loc[j, "quarter_rev"] != 0:
                df.loc[i, "rev_yoy"] = (df.loc[i, "quarter_rev"] / df.loc[j, "quarter_rev"] - 1) * 100
            if pd.notna(df.loc[i, "quarter_np"]) and pd.notna(df.loc[j, "quarter_np"]) and abs(df.loc[j, "quarter_np"]) > 1e6:
                df.loc[i, "np_yoy"] = (df.loc[i, "quarter_np"] / df.loc[j, "quarter_np"] - 1) * 100

    return df.sort_values("end_date", ascending=False).reset_index(drop=True)


def main():
    print("=" * 95)
    print("国产光刻胶景气度分析 V2（单季同比修正版）")
    print("=" * 95)

    all_data = {}

    for ts_code, (name, biz, tier) in PHOTORESIST_STOCKS.items():
        df = fetch_raw_financials(ts_code, periods=8)
        if df is None or len(df) < 4:
            print(f"  ✗ {name:8s} {ts_code}  数据不足")
            continue

        all_data[ts_code] = {
            "name": name, "biz": biz, "tier": tier, "df": df
        }
        print(f"  ✓ {name:8s} {ts_code}  {len(df)}期")

    # 逐只分析
    results = []
    for ts_code, info in all_data.items():
        df = info["df"]
        name = info["name"]
        # 最近4个季度的单季同比
        recent4 = df.head(4)

        # G = 最近一期增速
        latest_rev_yoy = recent4.iloc[0]["rev_yoy"] if pd.notna(recent4.iloc[0]["rev_yoy"]) else 0
        latest_np_yoy = recent4.iloc[0]["np_yoy"] if pd.notna(recent4.iloc[0]["np_yoy"]) else 0

        # 取营收和净利增速的均值作为 G
        g_values = []
        for _, row in recent4.iterrows():
            if pd.notna(row["rev_yoy"]):
                g_values.append(row["rev_yoy"])

        # ΔG = 最近一期增速 - 上一期增速
        if len(g_values) >= 2:
            delta_g = g_values[0] - g_values[1]
        else:
            delta_g = 0

        # 增速序列趋势（4季度）
        g_series = []
        for _, row in recent4.iloc[::-1].iterrows():  # 从旧到新
            if pd.notna(row["rev_yoy"]):
                g_series.append(row["rev_yoy"])

        # 加速期判断：G>0 且 ΔG>0
        # 减速期判断：G>0 且 ΔG<0
        # 上升拐点：G 从负转正 或 ΔG 从负转正
        # 下降拐点：G 从正转负 或 ΔG 从正转负

        avg_g = np.mean(g_series) if g_series else 0

        # 周期定位
        if avg_g > 30 and delta_g > 0:
            stage = "加速期"
            mountain = "左山坡（加速期 G>30%+ΔG↑ → Davis Double 核心区）"
        elif avg_g > 15 and delta_g > 0:
            stage = "上升拐点"
            mountain = "左山坡下部（增速回升 ΔG转正 → 奇点刚过）"
        elif avg_g > 30 and delta_g < 0:
            stage = "减速期"
            mountain = "右山坡（减速期 G高ΔG↓ → 杀估值风险区）"
        elif avg_g > 0 and delta_g < 0:
            stage = "减速期"
            mountain = "右山坡（增速尚可但边际下行）"
        elif avg_g <= 0 and delta_g > 0:
            stage = "上升拐点"
            mountain = "山后回升（底部拐点 ΔG转正 G仍低）"
        else:
            stage = "下降拐点"
            mountain = "山后下行（G↓+ΔG↓ → 规避区）"

        # 二次点火
        is_ignition = (avg_g > 0 and delta_g > 0)

        # 30%阈值
        threshold_warn = latest_np_yoy < 30 if latest_np_yoy > 0 else True

        results.append({
            "ts_code": ts_code, "name": name, "tier": info["tier"], "biz": biz,
            "latest_rev_yoy": latest_rev_yoy, "latest_np_yoy": latest_np_yoy,
            "avg_g": avg_g, "delta_g": delta_g,
            "g_series": g_series,
            "stage": stage, "mountain": mountain,
            "is_ignition": is_ignition,
            "threshold_warn": threshold_warn,
            "df": df,
        })

    # 按 ΔG 排序（加速最强的在前）
    results.sort(key=lambda x: (x["delta_g"], x["avg_g"]), reverse=True)

    # === 汇总表 ===
    print(f"\n{'='*95}")
    print("景气度排名（按ΔG降序——加速最强的在前）")
    print(f"{'='*95}\n")
    print(f"{'排名':>3} {'名称':8s} {'代码':12s} {'梯队':6s} {'最新营收yoy':>10} {'最新净利yoy':>10} {'均G':>8} {'ΔG':>8} {'周期':8s} {'点火':4s}")
    print("─" * 95)
    for i, r in enumerate(results, 1):
        ig = "★" if r["is_ignition"] else ""
        print(f"{i:3d}  {r['name']:8s} {r['ts_code']:12s} {r['tier']:6s} "
              f"{r['latest_rev_yoy']:+9.1f}% {r['latest_np_yoy']:+9.1f}% "
              f"{r['avg_g']:+7.1f} {r['delta_g']:+7.1f} "
              f"{r['stage']:8s} {ig:4s}")

    # === 增速轨迹 ===
    print(f"\n{'='*95}")
    print("最近4季度单季营收增速轨迹（从旧到新）")
    print(f"{'='*95}\n")
    for r in results:
        series_str = " → ".join([f"{v:+.1f}%" for v in r["g_series"]])
        arrow = "↑" if r["delta_g"] > 0 else "↓"
        print(f"  {r['name']:8s} [{r['tier']:4s}]  {series_str}  {arrow} ΔG={r['delta_g']:+.1f}")

    # === 详细分析 ===
    print(f"\n{'='*95}")
    print("逐只标的详细分析")
    print(f"{'='*95}")

    for i, r in enumerate(results, 1):
        print(f"\n{'─'*95}")
        ig = " ★二次点火" if r["is_ignition"] else ""
        print(f"  {i}. 【{r['name']}】{r['ts_code']}  [{r['tier']}]  最新营收{r['latest_rev_yoy']:+.1f}%  ΔG={r['delta_g']:+.1f}  周期:{r['stage']}{ig}")
        print(f"{'─'*95}")
        print(f"  业务: {r['biz']}")
        print(f"  山峰理论: {r['mountain']}")

        # 最近4季明细
        print(f"\n  最近季度明细:")
        print(f"    {'报告期':12s} {'单季营收(亿)':>10} {'单季归母(亿)':>10} {'营收yoy':>8} {'净利yoy':>8} {'ROE':>6} {'毛利率':>6}")
        for _, row in r["df"].head(6).iterrows():
            rev_q = row["quarter_rev"]/1e8 if pd.notna(row["quarter_rev"]) else 0
            np_q = row["quarter_np"]/1e8 if pd.notna(row["quarter_np"]) else 0
            ry = f"{row['rev_yoy']:+.1f}%" if pd.notna(row["rev_yoy"]) else "N/A"
            py = f"{row['np_yoy']:+.1f}%" if pd.notna(row["np_yoy"]) else "N/A"
            roe = f"{row['roe']:.2f}%" if pd.notna(row.get("roe")) else "N/A"
            gm = f"{row['grossprofit_margin']:.1f}%" if pd.notna(row.get("grossprofit_margin")) else "N/A"
            print(f"    {row['end_date']:12s} {rev_q:10.2f} {np_q:10.2f} {ry:>8} {py:>8} {roe:>6} {gm:>6}")

        if r["threshold_warn"]:
            print(f"  ⚠ 30%阈值: 最新净利增速 {r['latest_np_yoy']:+.1f}% < 30%")


if __name__ == "__main__":
    main()
