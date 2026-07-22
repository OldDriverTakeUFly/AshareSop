"""端侧AI设备产业链景气度调研——小米/阿里/腾讯下游供应商。

新增标的（排除已在之前跑过的SoC/算力/应用标的）：
1. AI眼镜光学模组（成本占比25%）
2. AI眼镜代工/结构件
3. AI眼镜电池
4. 端侧传感器（CIS/射频/麦克风）
5. 端侧设备ODM/品牌
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

pro = ts.pro_api()

STOCKS = {
    # === 1. 光学模组（AI眼镜成本占比25%）===
    "002273.SZ": ("水晶光电", "光波导+光学模组(Meta Ray-Ban/小米AI眼镜)", "光学模组"),
    "688127.SH": ("蓝特光学", "光学棱镜+玻璃非球面透镜(AR/VR)", "光学模组"),
    "002456.SZ": ("欧菲光", "摄像头模组+光学指纹+微显示", "光学模组"),
    "2382.HK": ("舜宇光学", "光学镜头(港股)", "光学模组"),  # 港股跳过

    # === 2. 代工/结构件 ===
    "002241.SZ": ("歌尔股份", "AI眼镜整机代工(Meta/小米/阿里)", "代工"),
    "002600.SZ": ("领益智造", "精密结构件(AI眼镜/手机)", "代工"),
    "300115.SZ": ("长盈精密", "精密结构件+新能源结构件", "代工"),

    # === 3. 电池 ===
    "300207.SZ": ("欣旺达", "锂聚合物电池(AI眼镜255mAh)", "电池"),
    "000049.SZ": ("德赛电池", "小型锂电池+封装", "电池"),
    "300438.SZ": ("鹏辉能源", "小型聚合物电池", "电池"),

    # === 4. 传感器/射频 ===
    "688213.SH": ("思特威", "CIS图像传感器(AI眼镜摄像头)", "传感器"),
    "300782.SZ": ("卓胜微", "射频前端芯片(端侧连接)", "传感器"),
    "300433.SZ": ("蓝思科技", "玻璃盖板+精密结构件(AI眼镜)", "结构件"),
    "002241.SZ": ("歌尔股份", "", ""),  # 重复跳过

    # === 5. 端侧设备/品牌/ODM ===
    "300866.SZ": ("安克创新", "AI+消费电子(耳机/眼镜出海)", "端侧品牌"),
    "300729.SZ": ("润建股份", "AI+算力运维+通信网络", "端侧配套"),
    "300296.SZ": ("利亚德", "Micro-LED+LED显示(虚拟拍摄/AR)", "显示"),

    # === 6. 小米产业链（手机端侧AI核心受益）===
    "300136.SZ": ("信维通信", "天线+射频(小米手机/AI设备)", "小米链"),
    "002384.SZ": ("东山精密", "FPC+PCB(小米/苹果供应链)", "小米链"),
    "600183.SH": ("生益科技", "CCL覆铜板(端侧设备PCB基材)", "小米链"),
    "300408.SZ": ("三环集团", "陶瓷后盖+MLCC(小米/电子烟)", "小米链"),

    # === 7. 存储（端侧AI刚需，DRAM/NAND）===
    "002049.SZ": ("兆易创新", "NOR Flash+DRAM(端侧AI存储)", "存储"),
}

# 去重+过滤港股
seen = set()
clean = {}
for code, (name, biz, tier) in STOCKS.items():
    if code not in seen and biz and tier and not code.endswith(".HK"):
        seen.add(code)
        clean[code] = (name, biz, tier)


def fetch_raw_financials(ts_code, periods=8):
    income = pro.income(ts_code=ts_code, fields="ts_code,end_date,ann_date,total_revenue,n_income,n_income_attr_p")
    if income is None or income.empty:
        return None
    income = income.drop_duplicates(subset=["end_date"]).sort_values("end_date", ascending=False).head(periods)
    fina = pro.fina_indicator(ts_code=ts_code, fields="ts_code,end_date,ann_date,roe,grossprofit_margin,dt_roe")
    if fina is not None and not fina.empty:
        fina = fina.drop_duplicates(subset=["end_date"]).sort_values("end_date", ascending=False).head(periods)
    else:
        fina = pd.DataFrame()
    df = income.merge(fina[["end_date","roe","grossprofit_margin"]], on="end_date", how="left") if not fina.empty else income
    df = df.sort_values("end_date").reset_index(drop=True)
    df["quarter_rev"] = df["total_revenue"]
    df["quarter_np"] = df["n_income_attr_p"]
    for i in range(len(df)-1, 0, -1):
        curr_q = df.loc[i, "end_date"][4:6]
        if curr_q != "03":
            df.loc[i, "quarter_rev"] = df.loc[i, "total_revenue"] - df.loc[i-1, "total_revenue"]
            df.loc[i, "quarter_np"] = df.loc[i, "n_income_attr_p"] - df.loc[i-1, "n_income_attr_p"]
    df["rev_yoy"] = np.nan
    df["np_yoy"] = np.nan
    for i in range(len(df)):
        curr_end = df.loc[i, "end_date"]
        curr_q = curr_end[4:6]
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
    print("=" * 100)
    print("端侧AI设备产业链景气度调研——小米/阿里/腾讯下游供应商")
    print("=" * 100)

    all_data = {}
    for ts_code, (name, biz, tier) in clean.items():
        df = fetch_raw_financials(ts_code, periods=8)
        if df is None or len(df) < 4:
            print(f"  ✗ {name:8s} {ts_code}  数据不足")
            continue
        all_data[ts_code] = {"name": name, "biz": biz, "tier": tier, "df": df}
        print(f"  ✓ {name:8s} {ts_code}  [{tier}]  {len(df)}期")

    print(f"\n成功获取 {len(all_data)} 只标的")

    results = []
    for ts_code, info in all_data.items():
        df = info["df"]
        recent4 = df.head(4)
        g_values = [row["rev_yoy"] for _, row in recent4.iterrows() if pd.notna(row["rev_yoy"])]
        np_values = [row["np_yoy"] for _, row in recent4.iterrows() if pd.notna(row["np_yoy"])]
        latest_rev_yoy = g_values[0] if g_values else 0
        latest_np_yoy = np_values[0] if np_values else 0
        delta_g = g_values[0] - g_values[1] if len(g_values) >= 2 else 0
        avg_g = np.mean(g_values) if g_values else 0

        if avg_g > 30 and delta_g > 0:
            stage = "加速期"
        elif avg_g > 15 and delta_g > 0:
            stage = "上升拐点"
        elif avg_g > 30 and delta_g < 0:
            stage = "减速期"
        elif avg_g > 0 and delta_g < 0:
            stage = "减速期"
        elif avg_g <= 0 and delta_g > 0:
            stage = "上升拐点"
        else:
            stage = "下降拐点"

        is_ignition = avg_g > 0 and delta_g > 0
        latest_roe = recent4.iloc[0].get("roe", 0) if "roe" in recent4.columns else 0
        latest_gm = recent4.iloc[0].get("grossprofit_margin", 0) if "grossprofit_margin" in recent4.columns else 0

        results.append({
            "ts_code": ts_code, "name": info["name"], "tier": info["tier"], "biz": info["biz"],
            "latest_rev_yoy": latest_rev_yoy, "latest_np_yoy": latest_np_yoy,
            "avg_g": avg_g, "delta_g": delta_g,
            "g_series": g_values, "np_series": np_values,
            "stage": stage, "is_ignition": is_ignition,
            "latest_roe": latest_roe, "latest_gm": latest_gm,
            "df": df,
        })

    results.sort(key=lambda x: (x["delta_g"], x["avg_g"]), reverse=True)

    # === 汇总表 ===
    print(f"\n{'='*100}")
    print("景气度排名总表（按 ΔG 降序）")
    print(f"{'='*100}\n")
    print(f"{'排名':>3} {'名称':8s} {'代码':12s} {'赛道':8s} {'营收yoy':>8} {'净利yoy':>8} {'均G':>8} {'ΔG':>8} {'周期':8s} {'点火':4s}")
    print("─" * 100)
    for i, r in enumerate(results, 1):
        ig = "★" if r["is_ignition"] else ""
        print(f"{i:3d}  {r['name']:8s} {r['ts_code']:12s} {r['tier']:8s} "
              f"{r['latest_rev_yoy']:+7.1f}% {r['latest_np_yoy']:+7.1f}% "
              f"{r['avg_g']:+7.1f} {r['delta_g']:+7.1f} "
              f"{r['stage']:8s} {ig:4s}")

    # === 增速轨迹 ===
    print(f"\n{'='*100}")
    print("最近4季度单季营收增速轨迹（从旧到新）")
    print(f"{'='*100}\n")
    for r in results:
        series_str = " → ".join([f"{v:+.1f}%" for v in r["g_series"][::-1]])
        arrow = "↑" if r["delta_g"] > 0 else ("→" if abs(r["delta_g"]) < 2 else "↓")
        ig = "★" if r["is_ignition"] else " "
        print(f"  {ig}{r['name']:8s} [{r['tier']:6s}]  {series_str}  {arrow} ΔG={r['delta_g']:+.1f}")

    # === 赛道分组 ===
    print(f"\n{'='*100}")
    print("按赛道分组排名")
    print(f"{'='*100}")
    tiers = {}
    for r in results:
        tiers.setdefault(r["tier"], []).append(r)
    for tier, trs in sorted(tiers.items(), key=lambda x: -np.mean([r["delta_g"] for r in x[1]])):
        trs.sort(key=lambda x: x["delta_g"], reverse=True)
        ig_count = sum(1 for r in trs if r["is_ignition"])
        avg_d = np.mean([r["delta_g"] for r in trs])
        print(f"\n  ◆ [{tier}] {len(trs)}只  ΔG均值={avg_d:+.1f}  点火={ig_count}只")
        for r in trs:
            ig = "★" if r["is_ignition"] else " "
            print(f"    {ig} {r['name']:8s} {r['ts_code']}  最新{r['latest_rev_yoy']:+.1f}%  ΔG={r['delta_g']:+.1f}  净利{r['latest_np_yoy']:+.1f}%")

    # === 二次点火 ===
    ignition = [r for r in results if r["is_ignition"]]
    print(f"\n{'='*100}")
    print(f"★ 二次点火筛选（G>0 且 ΔG>0）: {len(ignition)} 只")
    print(f"{'='*100}\n")
    for r in ignition:
        roe_str = f"{r['latest_roe']:.1f}%" if r['latest_roe'] and not np.isnan(r['latest_roe']) else "N/A"
        gm_str = f"{r['latest_gm']:.1f}%" if r['latest_gm'] and not np.isnan(r['latest_gm']) else "N/A"
        print(f"  ★ {r['name']:8s} {r['ts_code']} [{r['tier']:6s}]  G均={r['avg_g']:+.1f}%  ΔG={r['delta_g']:+.1f}  净利={r['latest_np_yoy']:+.1f}%  ROE={roe_str}  毛利率={gm_str}")
        print(f"    业务: {r['biz']}")
        print(f"    周期: {r['stage']}")


if __name__ == "__main__":
    main()
