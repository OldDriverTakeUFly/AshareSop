"""AI应用 + 端侧AI 景气度全景调研。

赛道分类（基于2026年产业趋势）：
1. 端侧AI SoC（AI眼镜/AI耳机/AIoT芯片）
2. AI眼镜光学模组+结构件
3. AI应用·Agent/B端SaaS
4. AI应用·内容/教育/营销
5. 端侧AI·声学/麦克风阵列
6. AI算力配套（边缘计算/存储）

使用单季同比修正版景气度引擎（photoresist_v2同款逻辑）
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

# ===== AI应用 + 端侧AI 标的池 =====
STOCKS = {
    # === 1. 端侧AI SoC（核心赛道） ===
    "688608.SH": ("恒玄科技", "AI眼镜SoC(BES2800/6nm)+AI耳机", "端侧SoC"),
    "688332.SH": ("中科蓝讯", "讯龙三代BT895x(CPU+DSP+NPU)+Wi-Fi", "端侧SoC"),
    "603893.SH": ("瑞芯微", "AIoT边缘侧SoC+机器视觉", "端侧SoC"),
    "688018.SH": ("乐鑫科技", "Wi-Fi MCU+AI语音芯片ESP32", "端侧SoC"),
    "688049.SH": ("炬芯科技", "智能音频SoC+可穿戴芯片", "端侧SoC"),
    "300458.SH": ("全志科技", "智能终端SoC+车载+AIoT", "端侧SoC"),
    "688521.SH": ("芯原股份", "AI芯片IP授权+NPU设计", "端侧SoC"),

    # === 2. AI眼镜光学/显示模组 ===
    "002241.SZ": ("歌尔股份", "AI眼镜代工+声学+光学模组", "AI眼镜"),
    "002475.SZ": ("立讯精密", "AI眼镜/可穿戴精密制造", "AI眼镜"),
    "300432.SH": ("富瀚微", "AI眼镜视觉芯片+安防SoC", "AI眼镜"),
    "300624.SH": ("万孚信息", "AR/VR光学+衍射光波导", "AI眼镜"),
    "300323.SZ": ("华灿光电", "Micro-LED+AI眼镜微显示", "AI眼镜"),
    "002969.SZ": ("嘉禾智能", "智能眼镜声学+精密结构件", "AI眼镜"),

    # === 3. AI应用·Agent/B端 ===
    "002230.SZ": ("科大讯飞", "讯飞星火大模型+AI教育+AI医疗", "AI应用"),
    "300624.SZ": ("万孚信息", "AI应用", "AI应用"),  # 重复了，下面去重
    "300479.SZ": ("神州信息", "AI+金融科技+数字人民币", "AI应用"),
    "600588.SH": ("用友网络", "BIP企业云+AI Agent(数字员工)", "AI应用"),
    "300496.SZ": ("中科创达", "智能操作系统+边缘AI+机器人", "AI应用"),
    "688561.SH": ("奇安信", "AI安全+智能体安全运营", "AI应用"),
    "300364.SZ": ("中文在线", "AI+数字内容+AIGC", "AI应用"),

    # === 4. AI应用·内容/教育/营销 ===
    "300624.SH": ("万孚信息", "AI应用", "AI应用"),  # 去重
    "002602.SZ": ("世纪华通", "AI游戏+脑科学+IDC", "AI应用"),
    "300251.SZ": ("光线传媒", "AI+动画+AIGC内容", "AI内容"),
    "300464.SZ": ("星辉娱乐", "AI游戏+体育IP", "AI内容"),
    "002555.SZ": ("三七互娱", "AI游戏+小游戏出海", "AI内容"),
    "300498.SZ": ("温氏股份", "", ""),  # 误入，删掉

    # === 5. 端侧AI·声学/传感器 ===
    "002241.SZ": ("歌尔股份", "", ""),  # 重复
    "300033.SZ": ("同花顺", "AI+金融信息+iFinD智能助手", "AI应用"),
    "300682.SZ": ("朗新集团", "AI+能源互联网", "AI应用"),

    # === 6. AI算力配套/边缘计算 ===
    "002236.SZ": ("大华股份", "AI视觉+边缘计算+智慧城市", "AI算力"),
    "002415.SZ": ("海康威视", "AI+机器视觉+智能制造", "AI算力"),
    "300024.SZ": ("机器人", "AI+工业机器人+具身智能", "AI算力"),
    "688169.SH": ("石头科技", "AI+扫地机器人+具身智能", "AI算力"),
    "300459.SZ": ("汤姆猫", "AI+AI伴侣+AI玩具", "AI内容"),
    "300223.SZ": ("北京君正", "AIoT处理器+DRAM+模拟芯片", "端侧SoC"),
    "688256.SH": ("寒武纪", "AI芯片/云端训练+边缘推理", "AI算力"),
    "688041.SH": ("海光信息", "AI CPU/DCU+国产算力", "AI算力"),
}

# 去重
seen = set()
clean_stocks = {}
for code, (name, biz, tier) in STOCKS.items():
    if code not in seen and biz and tier:
        seen.add(code)
        clean_stocks[code] = (name, biz, tier)


def fetch_raw_financials(ts_code, periods=8):
    """直接从 Tushare income+fina_indicator 拉取，自己算单季同比"""
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
    df = df.sort_values("end_date", ascending=False).reset_index(drop=True)

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
    print("AI应用 + 端侧AI 景气度全景调研（G+ΔG框架）")
    print("=" * 100)

    all_data = {}

    for ts_code, (name, biz, tier) in clean_stocks.items():
        df = fetch_raw_financials(ts_code, periods=8)
        if df is None or len(df) < 4:
            print(f"  ✗ {name:8s} {ts_code}  数据不足")
            continue
        all_data[ts_code] = {"name": name, "biz": biz, "tier": tier, "df": df}
        print(f"  ✓ {name:8s} {ts_code}  [{tier}]  {len(df)}期")

    print(f"\n成功获取 {len(all_data)} 只标的")

    # 逐只分析
    results = []
    for ts_code, info in all_data.items():
        df = info["df"]
        recent4 = df.head(4)

        latest_rev_yoy = recent4.iloc[0]["rev_yoy"] if pd.notna(recent4.iloc[0]["rev_yoy"]) else 0
        latest_np_yoy = recent4.iloc[0]["np_yoy"] if pd.notna(recent4.iloc[0]["np_yoy"]) else 0

        g_values = []
        for _, row in recent4.iterrows():
            if pd.notna(row["rev_yoy"]):
                g_values.append(row["rev_yoy"])

        delta_g = g_values[0] - g_values[1] if len(g_values) >= 2 else 0
        avg_g = np.mean(g_values) if g_values else 0
        g_series = [v for _, v in sorted(enumerate(g_values), key=lambda x: -x[0])]  # 从新到旧

        # 净利增速序列
        np_values = []
        for _, row in recent4.iterrows():
            if pd.notna(row["np_yoy"]):
                np_values.append(row["np_yoy"])
        latest_np = np_values[0] if np_values else 0

        # 周期定位
        if avg_g > 30 and delta_g > 0:
            stage = "加速期"
            mountain = "左山坡（加速期 G>30%+ΔG↑）"
        elif avg_g > 15 and delta_g > 0:
            stage = "上升拐点"
            mountain = "左山坡下（增速回升）"
        elif avg_g > 30 and delta_g < 0:
            stage = "减速期"
            mountain = "右山坡（G高ΔG↓）"
        elif avg_g > 0 and delta_g < 0:
            stage = "减速期"
            mountain = "右山坡（边际下行）"
        elif avg_g <= 0 and delta_g > 0:
            stage = "上升拐点"
            mountain = "山后回升（底部拐点）"
        else:
            stage = "下降拐点"
            mountain = "山后下行（G↓+ΔG↓）"

        is_ignition = (avg_g > 0 and delta_g > 0)
        threshold_warn = latest_np < 30 if latest_np > 0 else True

        # 毛利率和ROE
        latest_roe = recent4.iloc[0].get("roe", 0) if "roe" in recent4.columns else 0
        latest_gm = recent4.iloc[0].get("grossprofit_margin", 0) if "grossprofit_margin" in recent4.columns else 0

        results.append({
            "ts_code": ts_code, "name": info["name"], "tier": info["tier"], "biz": info["biz"],
            "latest_rev_yoy": latest_rev_yoy, "latest_np_yoy": latest_np,
            "avg_g": avg_g, "delta_g": delta_g,
            "g_series": g_values, "np_series": np_values,
            "stage": stage, "mountain": mountain,
            "is_ignition": is_ignition, "threshold_warn": threshold_warn,
            "latest_roe": latest_roe, "latest_gm": latest_gm,
            "df": df,
        })

    # 按 ΔG 排序
    results.sort(key=lambda x: (x["delta_g"], x["avg_g"]), reverse=True)

    # === 汇总表（按赛道分组）===
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

    # === 按赛道分组排名 ===
    print(f"\n{'='*100}")
    print("按赛道分组排名")
    print(f"{'='*100}")
    tiers = {}
    for r in results:
        tiers.setdefault(r["tier"], []).append(r)

    for tier, tier_results in sorted(tiers.items(), key=lambda x: -sum(1 for r in x[1] if r["delta_g"] > 0)):
        tier_results.sort(key=lambda x: x["delta_g"], reverse=True)
        ignition_count = sum(1 for r in tier_results if r["is_ignition"])
        avg_delta = np.mean([r["delta_g"] for r in tier_results])
        print(f"\n  ◆ [{tier}] {len(tier_results)}只  ΔG均值={avg_delta:+.1f}  点火={ignition_count}只")
        for r in tier_results:
            ig = "★" if r["is_ignition"] else " "
            series_str = "→".join([f"{v:+.0f}" for v in r["g_series"][:4]])
            print(f"    {ig} {r['name']:8s} {r['ts_code']}  最新{r['latest_rev_yoy']:+.1f}%  ΔG={r['delta_g']:+.1f}  轨迹:{series_str}")

    # === 增速轨迹 ===
    print(f"\n{'='*100}")
    print("最近4季度单季营收增速轨迹（从旧到新）")
    print(f"{'='*100}\n")
    for r in results:
        series_str = " → ".join([f"{v:+.1f}%" for v in r["g_series"][::-1]])  # 从旧到新
        arrow = "↑" if r["delta_g"] > 0 else ("→" if abs(r["delta_g"]) < 2 else "↓")
        ig = "★" if r["is_ignition"] else " "
        print(f"  {ig}{r['name']:8s} [{r['tier']:6s}]  {series_str}  {arrow} ΔG={r['delta_g']:+.1f}")

    # === 二次点火标的 ===
    ignition = [r for r in results if r["is_ignition"]]
    print(f"\n{'='*100}")
    print(f"★ 二次点火筛选（G>0 且 ΔG>0）: {len(ignition)} 只")
    print(f"{'='*100}\n")
    for r in ignition:
        roe_str = f"{r['latest_roe']:.1f}%" if r['latest_roe'] and not np.isnan(r['latest_roe']) else "N/A"
        gm_str = f"{r['latest_gm']:.1f}%" if r['latest_gm'] and not np.isnan(r['latest_gm']) else "N/A"
        print(f"  ★ {r['name']:8s} {r['ts_code']} [{r['tier']:6s}]  G均={r['avg_g']:+.1f}%  ΔG={r['delta_g']:+.1f}  净利={r['latest_np_yoy']:+.1f}%  ROE={roe_str}  毛利率={gm_str}")
        print(f"    业务: {r['biz']}")
        print(f"    定位: {r['mountain']}")


if __name__ == "__main__":
    main()
