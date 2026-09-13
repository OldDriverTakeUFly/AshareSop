"""盘后 markdown 日报:温度排行/升降温和/高温预警/大盘温度/完整性备注."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from davis_analyzer.config import THERMOMETER_REPORTS_DIR
from davis_analyzer.limitup import db as limitup_db

REPORTS_DIR = THERMOMETER_REPORTS_DIR  # 测试可 monkeypatch


def _md_table(df: pd.DataFrame, floatfmt: str = "{:.1f}") -> str:
    if df.empty:
        return "(无数据)"
    fmt = df.copy()
    for c in fmt.select_dtypes("number").columns:
        fmt[c] = fmt[c].map(lambda v: floatfmt.format(v) if pd.notna(v) else "-")
    head = "| " + " | ".join(str(c) for c in fmt.columns) + " |"
    sep = "|" + "|".join("---" for _ in fmt.columns) + "|"
    rows = ["| " + " | ".join(str(v) for v in r) + " |"
            for r in fmt.itertuples(index=False, name=None)]
    return "\n".join([head, sep, *rows])


def write_daily_report(conn: sqlite3.Connection, day: str) -> Path:
    """day 为 compact 日期;输出 {dash}_板块温度计.md 到 REPORTS_DIR."""
    dash = limitup_db.to_dash_date(day)
    sec = pd.read_sql_query(
        "SELECT * FROM thermometer_sector WHERE trade_date=?", conn, params=(day,))
    mkt = pd.read_sql_query(
        "SELECT * FROM thermometer_market WHERE trade_date=?", conn, params=(day,))
    lines = [f"# 板块温度计 · {dash}", ""]

    for level, label in (("L1", "一级行业"), ("L2", "二级行业")):
        sub = sec[sec["level"] == level].sort_values("temperature", ascending=False)
        top = sub.head(10)[["name", "temperature", "delta_temp5", "hot_streak"]].copy()
        top.columns = ["板块", "温度", "5日升温", "连热天数"]
        bottom = sub.tail(5)[["name", "temperature", "delta_temp5"]].copy()
        bottom.columns = ["板块", "温度", "5日升温"]
        lines += [f"## {level} 温度榜 · {label}", "",
                  "### 最热 top10", "", _md_table(top.reset_index(drop=True)), "",
                  "### 最冷 bottom5", "", _md_table(bottom.reset_index(drop=True)), ""]

    hottest = sec.sort_values("delta_temp5", ascending=False).head(5)
    coldest = sec.sort_values("delta_temp5").head(5)
    for title, df in (("升温榜", hottest), ("降温榜", coldest)):
        t = df[["level", "name", "temperature", "delta_temp5"]].copy()
        t.columns = ["层级", "板块", "温度", "5日升温"]
        lines += [f"## {title}", "", _md_table(t.reset_index(drop=True)), ""]

    warn = sec[sec["hot_streak"] >= 3].sort_values("hot_streak", ascending=False)
    w = warn[["level", "name", "temperature", "hot_streak"]].copy()
    w.columns = ["层级", "板块", "温度", "连热天数"]
    lines += ["## 高温预警(温度>80 连续≥3日)", "",
              _md_table(w.reset_index(drop=True)), ""]

    if not mkt.empty:
        row = mkt.iloc[0]
        lines += ["## 大盘温度", "",
                  f"- 温度 **{row['temperature']:.1f}** · 档位 **{row['regime_label']}**",
                  f"- 五维分位: 趋势 {row['trend_dim']:.2f} / 宽度 {row['width_dim']:.2f} "
                  f"/ 量能 {row['volume_dim']:.2f} / 资金 {row['flow_dim']:.2f} "
                  f"/ 情绪 {row['sentiment_dim']:.2f}"]
        if row["detail"]:
            lines += [f"- 背离提示: {row['detail']}"]
        lines.append("")
    else:
        lines += ["## 大盘温度", "", "(当日无大盘温度,历史不足或未计算)", ""]

    n_all = pd.read_sql_query(
        "SELECT level, COUNT(*) AS n FROM sw_index GROUP BY level", conn)
    have = sec.groupby("level")["index_code"].nunique().to_dict()
    notes = [f"{r.level}: 覆盖 {have.get(r.level, 0)}/{r.n}"
             for r in n_all.itertuples()] or ["sw_index 为空"]
    lines += ["## 数据完整性", "", " · ".join(notes), ""]

    out = REPORTS_DIR / f"{dash}_板块温度计.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
