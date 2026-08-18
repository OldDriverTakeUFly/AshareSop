"""首板启动数据解剖：多维度分层 × 晋级/收益 + 连板高度分布 + 组合画像.

一次性研究脚本（探索性，假设生成口径——非采纳决策，采纳需另行 OOS 验证）。
输出 davis_analyzer/limitup/reports/first_board_anatomy.md
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
from loguru import logger

from davis_analyzer.limitup import db, patterns
from davis_analyzer.limitup.events import build_events

START, END = "20210104", "20260814"
MIN_N = 50  # 晋级率类结论样本门槛


def bucket_table(fb: pd.DataFrame, col: str, bins, labels: list[str],
                 title: str) -> tuple[str, pd.DataFrame]:
    valid = fb[fb["ret_open_1"].notna()].copy()
    valid["档"] = pd.cut(valid[col], bins=bins, labels=labels)
    rows = []
    for band, g in valid.groupby("档", observed=False):
        r = g["ret_open_1"]
        rows.append({
            "档位": str(band), "样本": len(g),
            "晋级率": g["promoted"].mean(),
            "T+1开盘均值": r.mean(), "中位": r.median(),
            "胜率": (r > 0).mean(),
        })
    out = pd.DataFrame(rows)
    out["样本足"] = out["样本"] >= MIN_N
    tbl = out.to_markdown(index=False, floatfmt=".4f")
    return f"## {title}\n\n{tbl}\n", out


def main() -> None:
    conn = db.connect()
    try:
        logger.info("构建事件 [{} → {}]", START, END)
        ev = build_events(conn, START, END)
        ev = patterns.attach_pattern_features(ev, conn, START, END)
        fb = ev[ev["consecutive_boards"] == 1].copy()
        fb = fb[fb["ret_open_1"].notna()]
        parts: list[str] = [
            f"# 首板启动数据解剖 [{START} → {END}]\n",
            f"首板样本 {len(fb)} 个（全部经股票池/涨停校验/除权防线；"
            "口径=打板成本(T涨停价)→T+1开盘卖；探索性研究，采纳需 OOS 复核）\n",
        ]

        # ── A. 分层维度 ──
        sections = [
            ("first_seal_band", None, None, "A1. 首封时间档"),
            ("vol_ratio_20", [0, 1, 2, 5, 1000], ["缩量(<1x)", "温和(1-2x)", "放量(2-5x)", "爆量(>5x)"], "A2. 放量倍数（当日量/前20日均量）"),
            ("float_mv", [0, 3e9, 1e10, 1e12], ["小盘(<30亿)", "中盘(30-100亿)", "大盘(>100亿)"], "A3. 流通市值分层"),
            ("sector_linkage", [0, 1.5, 3.5, 100], ["独苗(1家)", "小共振(2-3家)", "强共振(≥4家)"], "A4. 同板块涨停共振"),
            ("prev_limit_count_60", [-0.5, 0.5, 1.5, 1000], ["0次(全新)", "1次", "≥2次(活跃股)"], "A5. 近60日涨停史（股性）"),
            ("turnover_rate", [0, 5, 15, 100], ["低换手(<5%)", "中换手(5-15%)", "高换手(>15%)"], "A6. 换手率档"),
        ]
        for col, bins, labels, title in sections:
            if bins is None:
                valid = fb.copy()
                rows = []
                for band, g in valid.groupby(col, observed=False):
                    r = g["ret_open_1"]
                    rows.append({"档位": str(band), "样本": len(g),
                                 "晋级率": g["promoted"].mean(),
                                 "T+1开盘均值": r.mean(), "中位": r.median(),
                                 "胜率": (r > 0).mean()})
                out = pd.DataFrame(rows)
                out["样本足"] = out["样本"] >= MIN_N
                parts.append(f"## {title}\n\n" + out.to_markdown(index=False, floatfmt=".4f") + "\n")
            else:
                md, _ = bucket_table(fb, col, bins, labels, title)
                parts.append(md)

        # 炸板回封 vs 硬板
        fb["板型"] = np.where(fb["broken_count"] > 0, "炸板后回封", "硬板未炸")
        _, t_b = bucket_table(fb.assign(板型数值=fb["broken_count"]),
                              "板型数值", [-0.5, 0.5, 100], ["硬板未炸", "炸板后回封"], "A7. 板型（是否炸过板）")
        parts.append("## A7. 板型（是否炸过板）\n\n" + t_b.to_markdown(index=False, floatfmt=".4f") + "\n")

        # 星期效应
        fb["星期"] = pd.to_datetime(fb["trade_date"], format="%Y%m%d").dt.dayofweek.map(
            {0: "一", 1: "二", 2: "三", 3: "四", 4: "五"})
        rows = []
        for wd, g in fb.groupby("星期"):
            rows.append({"星期": wd, "样本": len(g), "晋级率": g["promoted"].mean(),
                         "T+1开盘均值": g["ret_open_1"].mean(),
                         "中位": g["ret_open_1"].median(),
                         "胜率": (g["ret_open_1"] > 0).mean()})
        wk = pd.DataFrame(rows)
        wk["样本足"] = wk["样本"] >= MIN_N
        parts.append("## A8. 星期效应\n\n" + wk.to_markdown(index=False, floatfmt=".4f") + "\n")

        # ── B. 形态 × 封档 晋级率矩阵 ──
        fb["封档"] = pd.cut(fb["seal_ratio"], [-1, 0.02, 0.05, 100], labels=["弱", "中", "强"])
        valid = fb[fb["ret_open_1"].notna()]
        mat_rows = []
        for (pat, band), g in valid.groupby(["pattern_label", "封档"], observed=False):
            if len(g) < MIN_N:
                continue
            mat_rows.append({"形态": pat, "封档": str(band), "样本": len(g),
                             "晋级率": g["promoted"].mean(),
                             "T+1开盘均值": g["ret_open_1"].mean(),
                             "胜率": (g["ret_open_1"] > 0).mean()})
        mat = pd.DataFrame(mat_rows).sort_values("晋级率", ascending=False)
        parts.append("## B. 形态 × 封档 晋级率矩阵（样本≥50）\n\n"
                     + mat.to_markdown(index=False, floatfmt=".4f") + "\n")

        # ── C. 首板后续连板高度分布（从 limit_pool 链条前推）──
        lp = db.read_limit_pool(conn, START, "20260930", pool_kind="limit_up")
        cal = db.trading_dates(conn, START, "20260930")
        rank = {d: i for i, d in enumerate(cal)}
        lp["rank"] = lp["trade_date"].map(rank)
        lp = lp.sort_values(["ts_code", "rank"])
        # 后向最高连板：对每行，沿次交易日 boards+1 链条走到头
        by_code = {c: g.reset_index(drop=True) for c, g in lp.groupby("ts_code")}
        heights = []
        for _, ev_row in fb.iterrows():
            g = by_code.get(ev_row["ts_code"])
            if g is None:
                continue
            idx_arr = g.index[(g["trade_date"] == ev_row["trade_date"])]
            if len(idx_arr) == 0:
                continue
            i = idx_arr[0]
            height = 1
            while (i + 1 < len(g) and g.at[i + 1, "rank"] == g.at[i, "rank"] + 1
                   and g.at[i + 1, "consecutive_boards"] == g.at[i, "consecutive_boards"] + 1):
                height += 1
                i += 1
            heights.append(height)
        h = pd.Series(heights)
        dist = pd.DataFrame({
            "最终高度": ["1板(止步)", "2板", "3板", "4板", "5板+"],
            "占比": [ (h == k).mean() for k in (1, 2, 3, 4) ] + [(h >= 5).mean()],
            "样本": [ int((h == k).sum()) for k in (1, 2, 3, 4) ] + [int((h >= 5).sum())],
        })
        parts.append("## C. 首板的最终连板高度分布（全历史链条前推）\n\n"
                     + dist.to_markdown(index=False, floatfmt=".4f")
                     + f"\n\n样本 {len(h)}；平均高度 {h.mean():.2f} 板，"
                     f"走出≥2板比例 {(h >= 2).mean():.1%}，≥3板 {(h >= 3).mean():.1%}。\n")

        out_path = "davis_analyzer/limitup/reports/first_board_anatomy.md"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(parts))
        print(f"报告已生成: {out_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
