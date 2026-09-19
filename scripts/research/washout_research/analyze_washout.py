"""洗盘事件统计分析 — 读取 detect_washout.py 产出的 episodes CSV。

产出:
1. 样本量与结局基率(低位/高位组,分年度)
2. 判别特征:按结局分组的特征中位数 + 分桶 P(continue) 表
3. 谷底后前向收益(5/10/20/60d,相对上证超额)
4. 筹码假设检验:洗盘是否抬高平均成本/压缩获利盘
5. 高位组 vs 低位组对比(洗盘 vs 出货的实证底色)
6. 近期(2026-06 后)episodes 观察名单
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.chdir(PROJECT_ROOT)
OUT_DIR = "studies/output/washout"

KEY_FEATURES = [
    ("depth", "回调深度"),
    ("wash_days", "洗盘天数"),
    ("leg_gain", "启动段涨幅"),
    ("vol_ratio", "缩量比(洗盘/启动段量)"),
    ("vol_vs_base", "洗盘量/启动前基准量"),
    ("down_vol_adv", "下跌日/上涨日量能比"),
    ("churn_days", "冲高回落日数"),
    ("upper_shadow_mean", "上影线占振幅比(0-1)"),
    ("close_position_mean", "收盘位置均值"),
    ("amplitude_mean", "振幅(0-1比率)"),
    ("mf_leg", "启动段主力净流入强度"),
    ("mf_wash", "洗盘段主力净流入强度"),
    ("trough_vs_support", "谷底/启动平台-1"),
    ("close_vs_ma10", "谷底收盘/MA10-1"),
    ("close_vs_ma20", "谷底收盘/MA20-1"),
    ("anchor_turnover", "首板日换手率%"),
    ("chip_prof_pre", "启动前获利盘%"),
    ("chip_prof_trough", "谷底获利盘%"),
]


def load(group: str) -> pd.DataFrame:
    df = pd.read_csv(f"{OUT_DIR}/episodes_{group}.csv")
    df["year"] = df["t0_date"] // 10000
    return df


def pct(x: float) -> str:
    return f"{x*100:+.1f}%"


def outcome_table(df: pd.DataFrame, title: str) -> None:
    print(f"\n{'='*72}\n{title}\n{'='*72}")
    closed = df[df["outcome"].isin(["continue", "breakdown", "timeout"])]
    n = len(closed)
    vc = closed["outcome"].value_counts()
    print(f"已完结样本 {n}(open {len(df)-n} 不计)")
    for k in ["continue", "breakdown", "timeout"]:
        print(f"  {k:<10} {vc.get(k,0):>6}  ({vc.get(k,0)/n*100:.1f}%)")
    print("\n分年度 continue 率:")
    by = closed.groupby("year")["outcome"].apply(lambda s: (s == "continue").mean() * 100)
    cnt = closed.groupby("year").size()
    for y in by.index:
        print(f"  {y}: {by[y]:.1f}%  (n={cnt[y]})")


def feature_split(df: pd.DataFrame, title: str) -> None:
    """按结局分组的核心特征中位数。wash_days=0(单日长上影洗盘)单独一类。"""
    print(f"\n{'='*72}\n{title}\n{'='*72}")
    closed = df[df["outcome"].isin(["continue", "breakdown"])]
    rows = []
    for col, label in KEY_FEATURES:
        if col not in closed.columns:
            continue
        med_con = closed.loc[closed["outcome"] == "continue", col].median()
        med_brk = closed.loc[closed["outcome"] == "breakdown", col].median()
        rows.append({"特征": label, "列名": col, "continue中位": med_con, "breakdown中位": med_brk})
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    print("\n洗盘子类结构(按 wash_days):")
    sub = df[df["outcome"].isin(["continue", "breakdown"])].copy()
    sub["子类"] = np.select(
        [sub["wash_days"] == 0, sub["wash_days"] == 1,
         sub["wash_days"].between(2, 3), sub["wash_days"] >= 4],
        ["0天(当日长上影)", "1天", "2-3天", "≥4天"], default="?")
    g = sub.groupby("子类", observed=True).agg(
        n=("outcome", "size"),
        P续涨=("outcome", lambda x: (x == "continue").mean() * 100),
        fwd20中位=("fwd20", "median"),
        depth中位=("depth", "median"))
    print(g.to_string(float_format=lambda x: f"{x:.3f}"))


def bucket_table(df: pd.DataFrame, col: str, label: str, q: int = 4) -> None:
    """特征分桶 → P(continue) 与 fwd20 中位数。只用于已完结样本。"""
    closed = df[df["outcome"].isin(["continue", "breakdown"])]
    s = closed[[col, "outcome", "fwd20"]].dropna(subset=[col])
    if len(s) < 100:
        return
    try:
        s["bucket"] = pd.qcut(s[col], q, duplicates="drop")
    except ValueError:
        return
    g = s.groupby("bucket", observed=True).agg(
        n=("outcome", "size"),
        p_continue=("outcome", lambda x: (x == "continue").mean() * 100),
        fwd20_med=("fwd20", "median"),
        fwd20_mean=("fwd20", "mean"),
    )
    overall = (s["outcome"] == "continue").mean() * 100
    print(f"\n── {label} ({col}) 四分位分桶,基线 P(continue)={overall:.1f}% ──")
    print(g.to_string(float_format=lambda x: f"{x:.3f}"))


def forward_returns(df: pd.DataFrame, title: str) -> None:
    print(f"\n{'='*72}\n{title}\n{'='*72}")
    closed = df[df["outcome"].isin(["continue", "breakdown"])]
    rows = []
    for oc in ["continue", "breakdown"]:
        sub = closed[closed["outcome"] == oc]
        r = {"结局": oc, "n": len(sub)}
        for hz in [5, 10, 20, 60]:
            r[f"fwd{hz}中位"] = sub[f"fwd{hz}"].median()
            r[f"fwd{hz}均值"] = sub[f"fwd{hz}"].mean()
        r["超额20d中位"] = (sub["fwd20"] - sub["idx_fwd20"]).median()
        r["胜率20d"] = f"{(sub['fwd20'] > 0).mean()*100:.1f}%"
        rows.append(r)
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f"{x*100:+.1f}%"))


def chip_test(df: pd.DataFrame, title: str) -> None:
    """用户假设检验:洗盘抬高平均持股成本、压缩获利盘。"""
    print(f"\n{'='*72}\n{title}\n{'='*72}")
    closed = df[df["outcome"].isin(["continue", "breakdown"])].copy()
    # 相对启动前:平均成本涨幅 vs 价格涨幅
    closed["d_cost"] = closed["chip_avg_trough"] / closed["chip_avg_pre"] - 1
    closed["d_price"] = closed["_close_trough"] / closed["_support"] - 1
    closed["d_prof"] = closed["chip_prof_trough"] - closed["chip_prof_pre"]
    for oc in ["continue", "breakdown"]:
        sub = closed[closed["outcome"] == oc]
        print(f"\n[{oc}] n={len(sub)}")
        print(f"  价格(启动前→谷底)涨幅中位: {sub['d_price'].median()*100:+.1f}%")
        print(f"  筹码平均成本涨幅中位:       {sub['d_cost'].median()*100:+.1f}%")
        print(f"  成本抬升/价格抬升 比:        {(sub['d_cost'].median()/sub['d_price'].median()):.2f}")
        print(f"  获利盘变化(启动前→谷底):   {sub['d_prof'].median():+.1f}pp (谷底获利盘中位 {sub['chip_prof_trough'].median():.1f}%)")
    print("\n(成本涨幅若显著低于价格涨幅=低位筹码未充分换手;获利盘在谷底大幅压缩=浮筹被清洗)")


def group_compare(low: pd.DataFrame, high: pd.DataFrame) -> None:
    print(f"\n{'='*72}\n高位组 vs 低位组对比(洗盘 vs 出货的底色)\n{'='*72}")
    for name, df in [("低位组", low), ("高位组", high)]:
        closed = df[df["outcome"].isin(["continue", "breakdown"])]
        n = len(closed)
        p_con = (closed["outcome"] == "continue").mean() * 100
        print(f"\n[{name}] 已完结={n}  P(continue)={p_con:.1f}%")
        print(f"  depth 中位 {closed['depth'].median()*100:.1f}% | wash_days 中位 {closed['wash_days'].median():.0f} | vol_ratio 中位 {closed['vol_ratio'].median():.2f}")
        print(f"  fwd20 中位 {closed['fwd20'].median()*100:+.1f}% | fwd20 均值 {closed['fwd20'].mean()*100:+.1f}% | fwd60 中位 {closed['fwd60'].median()*100:+.1f}%")
        print(f"  fwd20_maxdd 中位 {closed['fwd20_maxdd'].median()*100:.1f}% (谷底后再回撤)")
    print("\n高位组若 P(continue) 显著更低 + fwd20 均值为负 → 高位回调更多是出货而非洗盘")


def recent_list(df: pd.DataFrame, since: int = 20260601) -> None:
    print(f"\n{'='*72}\n近期 episodes 观察名单(谷底日期 ≥ {since})\n{'='*72}")
    import sqlite3
    con = sqlite3.connect("storage/database/market_data.db")
    names = pd.read_sql("SELECT ts_code, name FROM stock_basic", con).set_index("ts_code")["name"].to_dict()
    con.close()
    rec = df[df["trough_date"] >= since].sort_values("trough_date")
    cols = ["ts_code", "trough_date", "outcome", "depth", "wash_days", "vol_ratio",
            "churn_days", "trough_vs_support", "close_vs_ma10", "chip_prof_trough", "fwd10", "fwd20"]
    t = rec[cols].copy()
    t["名称"] = t["ts_code"].map(names)
    print(t.to_string(index=False, float_format=lambda x: f"{x:.3f}"))


def main() -> None:
    pd.set_option("display.width", 200)
    low = load("low")
    high = load("high")
    print(f"低位组样本 {len(low)},高位组样本 {len(high)}")

    outcome_table(low, "一、低位启动组:结局基率")
    forward_returns(low, "二、低位组:谷底后前向收益")
    feature_split(low, "三、低位组:判别特征(按结局中位数)")
    print(f"\n{'='*72}\n四、低位组:分桶判别力\n{'='*72}")
    for col, label in [
        ("vol_ratio", "缩量比"), ("down_vol_adv", "下跌日量能优势"),
        ("depth", "回调深度"), ("wash_days", "洗盘天数"),
        ("trough_vs_support", "距启动平台"), ("close_vs_ma10", "距MA10"),
        ("churn_days", "冲高回落日数"), ("mf_wash", "洗盘段主力净流入"),
        ("chip_prof_pre", "启动前获利盘"), ("anchor_turnover", "首板换手率"),
    ]:
        bucket_table(low, col, label)
    chip_test(low, "五、低位组:筹码假设检验(抬高成本/压缩获利盘)")
    group_compare(low, high)
    print(f"\n{'='*72}\n六、高位组:判别特征\n{'='*72}")
    feature_split(high, "高位组:判别特征(按结局中位数)")
    for col, label in [("vol_ratio", "缩量比"), ("down_vol_adv", "下跌日量能"),
                       ("depth", "回调深度"), ("mf_wash", "洗盘段主力净流入"),
                       ("close_vs_ma20", "距MA20")]:
        bucket_table(high, col, label)
    recent_list(low)
    recent_list(high)


if __name__ == "__main__":
    main()
