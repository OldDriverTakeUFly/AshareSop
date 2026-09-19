"""surge 因子回测分析(spec §10 轻量校准): 前向收益分组 + RankIC.

口径:
- 前向收益 = T+1 开盘买入 → T+N 收盘卖出(N=5/10/20),未满窗口剔除
- 基线 = 当日命中池等权(所有截面合并)
- 巨潮事件维度未随回放补拉(仅 corp_event 覆盖),hype/risk 标签在历史截面偏低,
  报告须标注
用法: python -m davis_analyzer.surge.validate [--start 20260101] [--out PATH]
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from davis_analyzer.surge import db

_HORIZONS = (5, 10, 20)


def _load_snapshots(conn: sqlite3.Connection, start: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT trade_date, ts_code, name, industry, pct_chg, pos_250d, "
        "dist_ma60, dist_ma120, dd_high_250, elg_net_d0, lg_net_5d, "
        "net_ratio_d0, consec_net_days, winner_rate, winner_delta_5d, "
        "cost_5pct, cost_50pct, cost_95pct, weight_avg, resistance_dist, "
        "support_dist, hype_count, risk_flag_count, composite "
        "FROM surge_snapshot WHERE trade_date>=? ORDER BY trade_date",
        conn, params=(start,))


def _forward_returns(conn: sqlite3.Connection, snap: pd.DataFrame) -> pd.DataFrame:
    """为每个 (day, ts_code) 算 T+1 开盘买入的 N 日前向收益."""
    codes = list(snap["ts_code"].unique())
    if not codes:
        return snap
    days = sorted(snap["trade_date"].unique())
    last_need = None  # 需要 T+21 日线
    frames = []
    for chunk_start in range(0, len(codes), 500):
        chunk = codes[chunk_start:chunk_start + 500]
        ph = ",".join("?" * len(chunk))
        frames.append(pd.read_sql_query(
            f"SELECT ts_code, trade_date, open, close FROM daily_price "
            f"WHERE ts_code IN ({ph}) AND trade_date>=? AND trade_date<=?",
            conn, params=(*chunk, days[0], "20991231")))
    px = pd.concat(frames, ignore_index=True)
    px["trade_date"] = px["trade_date"].map(db.normalize_date)
    px = px.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    # 交易日序列(全市场)
    tdays = sorted(px["trade_date"].unique())
    tidx = {d: i for i, d in enumerate(tdays)}
    px_g = {c: g.set_index("trade_date") for c, g in px.groupby("ts_code")}

    def _fwd(row_ts: str, row_day: str) -> tuple:
        g = px_g.get(row_ts)
        i = tidx.get(row_day)
        if g is None or i is None or i + 1 >= len(tdays):
            return (np.nan,) * len(_HORIZONS)
        buy_day = tdays[i + 1]
        if buy_day not in g.index:
            return (np.nan,) * len(_HORIZONS)
        buy = float(g.loc[buy_day, "open"])
        out = []
        for n in _HORIZONS:
            j = i + 1 + n
            if j >= len(tdays):
                out.append(np.nan)
                continue
            sell_day = tdays[j]
            out.append(float(g.loc[sell_day, "close"]) / buy - 1
                       if sell_day in g.index else np.nan)
        return tuple(out)

    keys = list(zip(snap["ts_code"], snap["trade_date"]))
    vals = [_fwd(t, d) for t, d in keys]
    for k, n in enumerate(_HORIZONS):
        snap[f"fwd{n}"] = [v[k] for v in vals]
    return snap


def _group_stats(df: pd.DataFrame, col: str, horizon: int = 10) -> pd.DataFrame:
    """分组等权前向收益与样本数;基线=全池."""
    base = df[f"fwd{horizon}"].mean()
    rows = [{"组": "基线(全池等权)", "样本": int(df[f"fwd{horizon}"].notna().sum()),
             f"{horizon}日均收益%": base * 100, "超额%": 0.0}]
    if col == "composite_quintile":
        for q in sorted(df[col].dropna().unique()):
            sub = df[df[col] == q]
            m = sub[f"fwd{horizon}"].mean()
            rows.append({"组": f"Q{int(q)}", "样本": int(sub[f"fwd{horizon}"].notna().sum()),
                         f"{horizon}日均收益%": m * 100, "超额%": (m - base) * 100})
    elif col == "pattern_hit":
        for val, label in ((1, "形态命中"), (0, "未命中")):
            sub = df[df[col] == val]
            if sub.empty:
                continue
            m = sub[f"fwd{horizon}"].mean()
            rows.append({"组": label, "样本": int(sub[f"fwd{horizon}"].notna().sum()),
                         f"{horizon}日均收益%": m * 100, "超额%": (m - base) * 100})
    return pd.DataFrame(rows)


def _rank_ic(df: pd.DataFrame, factor: str, horizon: int = 10) -> tuple[float, float, int]:
    """按日截面 Spearman IC;返回 (均值, ICIR, 有效日数)."""
    ics = []
    for _, g in df.groupby("trade_date"):
        g = g[[factor, f"fwd{horizon}"]].dropna()
        if len(g) < 20:
            continue
        ic = g[factor].rank().corr(g[f"fwd{horizon}"].rank())
        if pd.notna(ic):
            ics.append(ic)
    if not ics:
        return (np.nan, np.nan, 0)
    arr = np.array(ics)
    return (float(arr.mean()),
            float(arr.mean() / arr.std()) if arr.std() > 0 else np.nan,
            len(ics))


_FACTORS = ["composite", "pos_250d", "dist_ma60", "dist_ma120", "dd_high_250",
            "elg_net_d0", "lg_net_5d", "net_ratio_d0", "consec_net_days",
            "winner_rate", "winner_delta_5d", "resistance_dist", "support_dist",
            "hype_count", "risk_flag_count"]


def run(start: str = "20260101", out_path: Path | None = None) -> Path:
    conn = db.connect()
    snap = _load_snapshots(conn, start)
    if snap.empty:
        raise SystemExit("无 snapshot 数据")
    snap = _forward_returns(conn, snap)
    # 形态命中标记
    pat = pd.read_sql_query(
        "SELECT DISTINCT trade_date, ts_code FROM surge_pattern_hits "
        "WHERE trade_date>=?", conn, params=(start,))
    snap["pattern_hit"] = [
        int(((pat.trade_date == d) & (pat.ts_code == c)).any())
        for c, d in zip(snap.ts_code, snap.trade_date)]
    tags = pd.read_sql_query(
        "SELECT trade_date, ts_code, tag FROM surge_tags WHERE trade_date>=?",
        conn, params=(start,))
    # composite 五分位(按日内)
    snap["composite_quintile"] = snap.groupby("trade_date")["composite"] \
        .transform(lambda s: pd.qcut(s, 5, labels=False, duplicates="drop") + 1)

    lines = [f"# Surge 因子回测报告({snap['trade_date'].min()}~{snap['trade_date'].max()})",
             "",
             f"- 截面数: {snap['trade_date'].nunique()},样本 {len(snap)} 行",
             f"- 前向收益口径: T+1 开盘买入 → N 日收盘;未满窗口剔除",
             "- **限制**: 巨潮事件维度未随回放(仅 corp_event 覆盖),"
             "历史截面 hype/risk 标签与综合分中事件权重偏低——综合分结论以此口径理解",
             ""]
    for h in _HORIZONS:
        lines.append(f"## 前向收益分组(N={h})")
        lines.append("")
        lines.append("### 综合分五分位")
        lines.append(_group_stats(snap, "composite_quintile", h)
                     .to_markdown(index=False))
        lines.append("")
        lines.append("### 形态副本命中 vs 未命中")
        lines.append(_group_stats(snap, "pattern_hit", h).to_markdown(index=False))
        lines.append("")
    # 标签分组(N=10)
    lines.append("## 形态标签分组(N=10,命中 vs 基线)")
    lines.append("")
    base10 = snap["fwd10"].mean()
    rows = []
    for tag in sorted(tags["tag"].unique()):
        sub_keys = set(map(tuple, tags[tags.tag == tag][["trade_date", "ts_code"]].values))
        mask = [tuple(x) in sub_keys for x in snap[["trade_date", "ts_code"]].values]
        sub = snap[mask]
        if len(sub) < 10:
            continue
        m = sub["fwd10"].mean()
        rows.append({"标签": tag, "样本": int(sub["fwd10"].notna().sum()),
                     "10日均收益%": m * 100, "超额%": (m - base10) * 100})
    if rows:
        lines.append(pd.DataFrame(rows).sort_values("超额%", ascending=False)
                     .to_markdown(index=False))
    lines.append("")
    # 因子 RankIC(N=10)
    lines.append("## 因子 RankIC(N=10 截面 Spearman)")
    lines.append("")
    ic_rows = []
    for f in _FACTORS:
        ic, icir, n = _rank_ic(snap, f, 10)
        ic_rows.append({"因子": f, "IC均值": round(ic, 4) if ic == ic else None,
                        "ICIR": round(icir, 2) if icir == icir else None,
                        "有效日": n})
    lines.append(pd.DataFrame(ic_rows).to_markdown(index=False))
    lines += ["", "> 口径:docs/superpowers/specs/2026-09-18-surge-screener-design.md §10;"
               "先验权重未校准,本报告即校准依据。", ""]
    out = out_path or (Path(__file__).resolve().parents[2] / "docs" / "回测记录"
                       / f"surge因子回测_{start[:6]}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告 → {out}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20260101")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    run(a.start, Path(a.out) if a.out else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
