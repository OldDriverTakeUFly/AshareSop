"""首板打板调研：板上卖出结构（大单 vs 小单）与晋级成功率、经验成交可能性.

一次性研究脚本（studies 惯例）。输出:
1. 全历史首板事件的卖出单型结构分档 × 晋级率
2. 近期首板候选清单（含卖出结构标签）
3. 经验开板率（打板成交可能性的现实锚）
报告写入 davis_analyzer/limitup/reports/first_board_sell_structure.md
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
from loguru import logger

from davis_analyzer.limitup import db, patterns
from davis_analyzer.limitup.events import build_events

START, END = "20210104", "20260731"
# 先验粗档位（禁调参）：大单卖出占比 ≥0.50 大单主导；0.30–0.50 均衡；<0.30 小单主导
LG_DOMINANT, LG_BALANCED = 0.50, 0.30


def sell_structure(mf: pd.DataFrame) -> pd.DataFrame:
    mf = mf.copy()
    mf["sell_total"] = (
        mf["sell_sm_amount"].fillna(0) + mf["sell_md_amount"].fillna(0)
        + mf["sell_lg_amount"].fillna(0) + mf["sell_elg_amount"].fillna(0)
    )
    ok = mf["sell_total"] > 0
    mf.loc[ok, "lg_sell_share"] = (
        (mf.loc[ok, "sell_lg_amount"].fillna(0) + mf.loc[ok, "sell_elg_amount"].fillna(0))
        / mf.loc[ok, "sell_total"]
    )
    mf.loc[ok, "sm_sell_share"] = mf.loc[ok, "sell_sm_amount"].fillna(0) / mf.loc[ok, "sell_total"]
    return mf


def bucket_lg(share: float) -> str:
    if pd.isna(share):
        return "无数据"
    if share >= LG_DOMINANT:
        return "大单主导"
    if share >= LG_BALANCED:
        return "均衡"
    return "小单主导"


def promo_table(df: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    valid = df[df["ret_open_1"].notna()]  # 末尾无 T+1 数据的事件剔除（宁缺毋错）
    rows = []
    for kvals, g in valid.groupby(by, dropna=False, sort=False):
        if not isinstance(kvals, tuple):
            kvals = (kvals,)
        rows.append({
            **dict(zip(by, kvals)),
            "晋级率": g["promoted"].mean(),
            "n": len(g),
            "次日开盘均值": g["ret_open_1"].mean(),
            "中位": g["ret_open_1"].median(),
            "胜率": (g["ret_open_1"] > 0).mean(),
        })
    out = pd.DataFrame(rows)
    if not out.empty:
        out["样本足(≥50)"] = out["n"] >= 50
    return out


def main() -> None:
    conn = db.connect()
    try:
        logger.info("构建事件 [{} → {}]", START, END)
        ev = build_events(conn, START, END)
        ev = patterns.attach_pattern_features(ev, conn, START, END)

        mf = pd.read_sql_query(
            "SELECT trade_date, ts_code, sell_sm_amount, sell_md_amount, "
            "sell_lg_amount, sell_elg_amount FROM moneyflow "
            "WHERE trade_date>=? AND trade_date<=?",
            conn, params=(START, END),
        )
        mf = sell_structure(mf)
        fb = ev[ev["consecutive_boards"] == 1].merge(
            mf[["ts_code", "trade_date", "lg_sell_share", "sm_sell_share"]],
            on=["ts_code", "trade_date"], how="left",
        )
        fb["卖出结构"] = fb["lg_sell_share"].map(bucket_lg)

        # ── 1) 卖出结构 × 晋级率（全历史首板）──
        t1 = promo_table(fb, ["卖出结构"])
        # 交叉：卖出结构 × 封单强度档
        fb["封档"] = pd.cut(fb["seal_ratio"], [-1, 0.02, 0.05, 100], labels=["弱", "中", "强"])
        t2 = promo_table(fb, ["卖出结构", "封档"])

        # ── 2) 经验开板率（成交可能性锚）──
        up = db.read_limit_pool(conn, START, END, pool_kind="limit_up")
        broken_pool = db.read_limit_pool(conn, START, END, pool_kind="broken")
        up["封板档"] = np.select(
            [
                up["first_seal_time"].between("090000", "100000", inclusive="left"),
                up["first_seal_time"].between("100000", "140000", inclusive="left"),
                up["first_seal_time"] >= "140000",
            ],
            ["早盘(<10:00)", "午盘", "尾盘(>=14:00)"],
            default="异常时间",
        )
        rows = []
        for band, g in up.groupby("封板档"):
            n_all = len(g)
            n_broke = int((g["broken_count"] > 0).sum())
            rows.append({
                "封板档": band, "n": n_all,
                "盘中曾开板率": n_broke / n_all if n_all else np.nan,
                "开板未回封(收盘烂板)": np.nan,  # 占位，下行补
            })
        open_rates = pd.DataFrame(rows)
        # 收盘烂板率 = 炸板池 / (涨停池+炸板池)，按封板档近似（炸板池无首封时间档，给总体值）
        total_broken = len(broken_pool)
        overall_bad = total_broken / (total_broken + len(up))
        open_rates["开板未回封(收盘烂板)"] = overall_bad

        # ── 3) 近期首板候选清单（近 2 个月，按 first_board 预设口径）──
        recent = fb[fb["trade_date"] >= "20260601"].copy()
        regime_ok_dates = set()  # 简化：不做 regime 过滤，全列出并附封档供人工判断
        cols = ["trade_date", "ts_code", "name", "sector", "seal_ratio",
                "lg_sell_share", "sm_sell_share", "卖出结构", "封档",
                "first_seal_band", "promoted", "ret_open_1", "pattern_label"]
        recent_list = recent[cols].sort_values("trade_date", ascending=False).head(40)

        # ── 输出 ──
        def md(df: pd.DataFrame) -> str:
            return df.to_markdown(index=False, floatfmt=".4f") if not df.empty else "(空)"

        parts = [
            f"# 首板卖出结构调研 [{START} → {END}]\n",
            f"首板事件数: {len(fb)}（含资金流覆盖 {int(fb['lg_sell_share'].notna().sum())}）；"
            f"大单卖出占比 = (大单+特大单卖出额)/总卖出额；档位先验: ≥{LG_DOMINANT} 大单主导 / "
            f"{LG_BALANCED}–{LG_DOMINANT} 均衡 / <{LG_BALANCED} 小单主导\n",
            "## 1. 卖出结构 × 晋级率（全历史首板）\n" + md(t1),
            "## 2. 卖出结构 × 封单强度 × 晋级率\n" + md(t2),
            "## 3. 经验开板率（打板成交可能性的现实锚）\n" + md(open_rates)
            + f"\n\n全体收盘烂板率（炸板池/(涨停+炸板)）: {overall_bad:.4f}。"
            "解读：盘中曾开板的涨停股，挂单有实际成交窗口；从未开板的硬板，排队成交主要靠队首排单，"
            "日线数据无法观测，现行模型给 20%（早盘硬板）/35%（普通）为乐观上界。",
            "## 4. 近期首板标的清单（2026-06 起，近 40 条）\n" + md(recent_list),
        ]
        out_path = "davis_analyzer/limitup/reports/first_board_sell_structure.md"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(parts))
        print(f"报告已生成: {out_path}")
        print("\n=== 卖出结构 × 晋级率 ===")
        print(t1.to_string(index=False))
        print("\n=== 经验开板率 ===")
        print(open_rates.to_string(index=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
