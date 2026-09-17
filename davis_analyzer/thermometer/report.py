"""盘后 markdown 日报:温度排行/升降温和/高温预警/大盘温度/完整性备注."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from davis_analyzer.config import THERMOMETER_REPORTS_DIR
from davis_analyzer.limitup import db as limitup_db
from davis_analyzer.thermometer.scoring import rotation_signals

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
    lines = [
        f"# 板块温度计 · {dash}",
        "",
        "> **温度语义(反向,2026-09-13 部署拍板)**:温度=板块拥挤度。高温=预期打得过满,"
        "注意风险(人声鼎沸处);低温=关注度低,可跟踪左侧机会(无人问津时)。",
        "> 校准依据:反向 OOS IC +0.068 / ICIR 0.28(2022-2026 walk-forward)。",
        "",
    ]

    for level, label in (("L1", "一级行业"), ("L2", "二级行业")):
        sub = sec[sec["level"] == level].sort_values("temperature", ascending=False)
        top = sub.head(10)[["name", "temperature", "delta_temp5", "hot_streak"]].copy()
        top["hot_streak"] = top["hot_streak"].astype("Int64")
        top.columns = ["板块", "温度", "5日升温", "连热天数"]
        bottom = sub.tail(5)[["name", "temperature", "delta_temp5"]].copy()
        bottom.columns = ["板块", "温度", "5日升温"]
        lines += [f"## {level} 温度榜 · {label}", "",
                  "### 高温 · 过热预警 top10(拥挤度高,注意风险)", "",
                  _md_table(top.reset_index(drop=True)), "",
                  "### 低温 · 关注池 bottom5(无人问津,左侧跟踪)", "",
                  _md_table(bottom.reset_index(drop=True)), ""]

    hottest = sec.sort_values("delta_temp5", ascending=False).head(5)
    coldest = sec.sort_values("delta_temp5").head(5)
    for title, df in (("升温榜(预期快速打满,警惕过热)", hottest),
                      ("降温榜(关注度回落,观察出清)", coldest)):
        t = df[["level", "name", "temperature", "delta_temp5"]].copy()
        t.columns = ["层级", "板块", "温度", "5日升温"]
        lines += [f"## {title}", "", _md_table(t.reset_index(drop=True)), ""]

    # 温度轮动(盘后日频口径:日间排名迁移与档位跃迁)
    rot = rotation_signals(conn, day)
    lines += ["## 温度轮动(近五个交易日)", ""]
    if rot["ac1"] is not None:
        lines += [
            f"- 轮动强度: 排名自相关 1日 {rot['ac1']:.2f} / 5日 {rot['ac5']:.2f}"
            "(越低轮动越快);"
            f"档位迁移 {len(rot['moves'])} 个板块(升 {rot['n_up']} / 降 {rot['n_down']})",
        ]
    if rot["new_hot"] or rot["exit_hot"]:
        lines.append(f"- 主线 top5 切换: 新晋 {('、'.join(rot['new_hot']) or '无')}"
                     f" / 退出 {('、'.join(rot['exit_hot']) or '无')}")
    if rot["moves"]:
        mdf = pd.DataFrame(rot["moves"][:12])
        mdf["迁移"] = mdf["from_band"] + "→" + mdf["to_band"]
        show = mdf[["level", "name", "迁移", "d5"]].copy()
        show.columns = ["层级", "板块", "档位迁移", "5日温度变化"]
        lines += ["", "### 档位迁移 top12(升档=左侧补涨/降档=高位退潮)", "",
                  _md_table(show.reset_index(drop=True)), ""]
    else:
        lines += ["- 五日内无档位迁移(格局稳定)", ""]

    warn = sec[sec["hot_streak"] >= 3].sort_values("hot_streak", ascending=False)
    w = warn[["level", "name", "temperature", "hot_streak"]].copy()
    w["hot_streak"] = w["hot_streak"].astype("Int64")
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
