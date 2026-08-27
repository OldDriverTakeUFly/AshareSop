"""连板晋级缺口因子补全 + 7%+ 未封板群体平行研究（实验0007，2026-08-27）.

Part A 涨停封板事件（复用 limitup.events.build_events，2021-01 → 最新）：
  A1 开板次数桶（0 硬板/1/2/3+）
  A2 回封耗时桶（一封到底/≤30min/30-120/>120，last-first 封板时间差）
  A3 市值桶 × 晋级率；1板事件 × 市值桶的最终连板高度分布
  A4 龙虎榜净买方向（未榜/榜净买/榜净卖）+ 上榜股净买额四分位
  A5 消息面代理：板块共振 × 封档矩阵；30 日内解禁/减持 × 晋级
Part B 涨幅 ≥7% 未封板群体（daily_price 全市场，同口径过滤/除权防线）：
  B0 = 主板 10cm 7%+ 未封板；B20 = 创业板/科创板 20cm 7%+ 未封板（含义不同，单列）
  对照 = 封板事件（Part A 同表）：T+1 开盘/收盘/胜率/追赶封板率/3 日
  B0 因子切片：量比桶 / 成交额桶（盘面大小代理，无全市场历史流通市值）/
              上影线桶 / 收盘位置桶（intraday_feature）
纪律：桶为先验固定，不做连续寻优；晋级率类 n≥50、收益类 n≥30，不足标记。
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from davis_analyzer.limitup import db as ldb  # noqa: E402
from davis_analyzer.limitup import events as lev  # noqa: E402

START, END = "20210104", "20260826"
OUT_DIR = "studies/output/promotion"
MIN_N_RATE, MIN_N_RET = 50, 30


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def fmt(x, pct=False, nd=3):
    if pd.isna(x):
        return "—"
    return f"{x*100:.1f}%" if pct else f"{x:.{nd}f}"


def dist_table(df: pd.DataFrame, by: str | list[str], val: str = "ret_open_1") -> pd.DataFrame:
    keys = [by] if isinstance(by, str) else by
    rows = []
    for k, g in df.groupby(keys, dropna=False, sort=False):
        if not isinstance(k, tuple):
            k = (k,)
        r = g[val].dropna()
        pos, neg = r[r > 0], r[r <= 0]
        promo = g["promoted"].mean() if "promoted" in g else np.nan
        rows.append({
            **dict(zip(keys, k)), "n": len(r),
            "晋级率": promo,
            "T+1开均值": r.mean(), "T+1开中位": r.median(),
            "胜率": (r > 0).mean() if len(r) else np.nan,
            "盈亏比": pos.mean() / abs(neg.mean()) if len(pos) and len(neg) else np.nan,
            "样本足": len(r) >= MIN_N_RET,
        })
    out = pd.DataFrame(rows)
    return out.sort_values(keys)


def md(df: pd.DataFrame, pctcols=()) -> str:
    d = df.copy()
    for c in pctcols:
        if c in d:
            d[c] = d[c].map(lambda x: fmt(x, pct=True) if not isinstance(x, str) else x)
    for c in d.columns:
        if d[c].dtype == float:
            d[c] = d[c].map(lambda x: "—" if pd.isna(x) else f"{x:.3f}")
    return d.to_markdown(index=False)


def seal_minutes(t: object) -> float:
    if not isinstance(t, str) or not t.isdigit() or len(t) != 6:
        return np.nan
    return int(t[:2]) * 60 + int(t[2:4]) + int(t[4:6]) / 60


def seal_band(sr: pd.Series) -> pd.Series:
    return pd.cut(sr, [-1, 0.02, 0.05, 100], labels=["弱", "中", "强"])


# ══════════════ Part A：封板事件缺口切片 ══════════════

def final_heights(ev: pd.DataFrame, conn) -> pd.DataFrame:
    """每事件的最终连板高度：同股按交易日连续且连板数+1 视为同链，链末连板数即全链高度."""
    tdates = ldb.trading_dates(conn, "20201201", "20261001")
    rank_map = {d: i for i, d in enumerate(tdates)}
    ev = ev.copy()
    ev["rank"] = ev["trade_date"].map(rank_map)
    ev = ev.sort_values(["ts_code", "rank"])
    ev["final_height"] = np.nan
    for code, g in ev.groupby("ts_code", sort=False):
        idx = g.index.to_numpy()
        rk = g["rank"].to_numpy()
        cb = g["consecutive_boards"].to_numpy()
        fh = np.full(len(g), np.nan)
        chain_start = 0
        for i in range(1, len(g) + 1):
            chain_break = i == len(g) or not (
                rk[i] is not None and rk[i] - rk[i - 1] == 1 and cb[i] == cb[i - 1] + 1
            )
            if chain_break:
                fh[chain_start:i] = cb[i - 1]
                chain_start = i
        ev.loc[idx, "final_height"] = fh
    return ev


def part_a(conn) -> pd.DataFrame:
    log("Part A: build_events 全历史 ...")
    ev = lev.build_events(conn, START, END)
    log(f"封板事件 {len(ev)} 条")
    out = ["# 连板晋级缺口因子 + 7%+ 未封板平行研究（实验0007）\n",
           f"窗口 {START} → {END}；晋级率类样本门槛 n≥{MIN_N_RATE}、收益类 n≥{MIN_N_RET}（探索性研究，采纳需 OOS 复核）\n",
           "\n## A1 开板次数桶（全体封板事件）\n"]
    ev["开板次数桶"] = ev["broken_count"].fillna(-1).map(
        lambda b: "0 硬板" if b == 0 else "1 次" if b == 1 else "2 次" if b == 2
        else "3 次+" if b >= 3 else "未知").astype(str)
    out.append(md(dist_table(ev, "开板次数桶"), pctcols=("晋级率", "T+1开均值", "T+1开中位", "胜率")))

    out.append("\n## A2 回封耗时桶（首次→末次封板时间差，分钟）\n")
    fmin = ev["first_seal_time"].map(seal_minutes)
    lmin = ev["last_seal_time"].map(seal_minutes)
    dur = lmin - fmin
    ev["回封耗时桶"] = np.select(
        [ev["broken_count"] == 0, dur.between(0, 30), dur.between(30, 120), dur > 120],
        ["一封到底", "≤30min", "30-120min", ">120min"], default="未知/异常")
    out.append(md(dist_table(ev, "回封耗时桶"), pctcols=("晋级率", "T+1开均值", "T+1开中位", "胜率")))

    out.append("\n## A3 流通市值桶 × 晋级率；1板 × 市值桶的最终连板高度\n")
    ev["市值桶"] = np.select(
        [ev["float_mv"] < 3e9, ev["float_mv"].between(3e9, 1e10), ev["float_mv"] > 1e10],
        ["小盘<30亿", "中盘30-100亿", "大盘>100亿"], default="缺失")
    out.append(md(dist_table(ev, "市值桶"), pctcols=("晋级率", "T+1开均值", "T+1开中位", "胜率")))
    ev = final_heights(ev, conn)
    first = ev[ev["consecutive_boards"] == 1]
    rows = []
    for b, g in first.groupby("市值桶"):
        rows.append({
            "市值桶": b, "n": len(g),
            "≥2板率": (g["final_height"] >= 2).mean(),
            "≥3板率": (g["final_height"] >= 3).mean(),
            "≥5板率": (g["final_height"] >= 5).mean(),
            "平均最终高度": g["final_height"].mean(),
        })
    out.append("\n1板事件的最终连板高度（按市值桶）：\n\n" +
               md(pd.DataFrame(rows).sort_values("市值桶"), pctcols=("≥2板率", "≥3板率", "≥5板率")))

    out.append("\n## A4 龙虎榜：净买方向 + 上榜股净买额四分位\n")
    onl = ev["on_lhb"].map(bool).to_numpy()
    net = ev["lhb_net_amount"].to_numpy()
    ev["龙虎榜方向"] = np.select(
        [~onl, onl & (net > 0), onl & (net <= 0)],
        ["未榜", "榜·净买", "榜·净卖"], default="未知")
    out.append(md(dist_table(ev, "龙虎榜方向"), pctcols=("晋级率", "T+1开均值", "T+1开中位", "胜率")))
    listed = ev[ev["on_lhb"] & ev["lhb_net_amount"].notna()].copy()
    listed["净买额四分位"] = pd.qcut(listed["lhb_net_amount"], 4,
                                   labels=["Q1最卖", "Q2", "Q3", "Q4最买"],
                                   duplicates="drop")
    out.append("\n上榜股内部（净买额四分位，万元）：\n\n" +
               md(dist_table(listed, "净买额四分位"), pctcols=("晋级率", "T+1开均值", "T+1开中位", "胜率")))

    out.append("\n## A5 消息面代理：板块共振 × 封档；30 日内解禁/减持\n")
    ev["封档"] = seal_band(ev["seal_ratio"])
    ev["共振桶"] = np.select(
        [ev["sector_linkage"] == 1, ev["sector_linkage"].between(2, 3), ev["sector_linkage"] >= 4],
        ["独苗", "小共振2-3", "强共振≥4"], default="未知")
    out.append(md(dist_table(ev, ["共振桶", "封档"]), pctcols=("晋级率", "T+1开均值", "T+1开中位", "胜率")))
    ev["近30日利空"] = ev["negative_event_30d"].map({True: "有解禁/减持", False: "无"})
    out.append("\n30 日内解禁/减持公告 × 次日表现：\n\n" +
               md(dist_table(ev, "近30日利空"), pctcols=("晋级率", "T+1开均值", "T+1开中位", "胜率")))
    return ev, out


# ══════════════ Part B：≥7% 未封板群体 ══════════════

def part_b(conn, ev_board: pd.DataFrame) -> list[str]:
    log("Part B: 全市场 ≥7% 未封板事件构建 ...")
    p_all = pd.read_sql_query(
        "SELECT ts_code, trade_date, open, high, low, close, pre_close, vol, amount "
        "FROM daily_price WHERE trade_date>=? AND trade_date<=? ORDER BY ts_code, trade_date",
        conn, params=("2020-12-01", END.replace("/", "-")))
    p_all["trade_date"] = p_all["trade_date"].map(ldb.normalize_date)
    basic = ldb.read_stock_basic(conn)[["ts_code", "name", "list_date"]]
    p_all = p_all.merge(basic, on="ts_code", how="left")
    p_all = p_all[~p_all["name"].str.contains("ST", na=False)]
    p_all = p_all[~p_all["ts_code"].str.endswith(".BJ")]
    list_dt = pd.to_datetime(p_all["list_date"], format="%Y%m%d", errors="coerce")
    trade_dt = pd.to_datetime(p_all["trade_date"], format="%Y%m%d")
    p_all = p_all[(trade_dt - list_dt).dt.days >= 60]

    prices = lev._drop_ex_dividend(p_all)
    chg = prices["close"] / prices["pre_close"] - 1
    ratio = prices["ts_code"].map(lambda c: 0.20 if c.startswith(("30", "68")) else 0.10)
    is_board = [lev.is_limit_up_close(c, pc, r) for c, pc, r
                in zip(prices["close"], prices["pre_close"], ratio)]
    sel = prices[(chg >= 0.07).to_numpy() & (~np.array(is_board))
                 & (prices["trade_date"] >= START)].copy()
    sel["is_20cm"] = sel["ts_code"].str.startswith(("30", "68"))
    log(f"≥7% 未封板事件 {len(sel)} 条（20cm {int(sel['is_20cm'].sum())}）")

    sel["limit_price"] = sel["close"]  # 入场基准=T 收盘（未封板可收盘买入，口径与封板一致）
    sel = lev.attach_return_labels(sel, p_all)
    sel = lev.attach_volume_features(sel, prices)

    # 盘面大小代理：成交额桶（无全市场历史流通市值，诚实边界）
    sel["成交额桶"] = np.select(
        [sel["amount"] < 5e5, sel["amount"].between(5e5, 2e6),
         sel["amount"].between(2e6, 5e6), sel["amount"] > 5e6],
        ["<5亿", "5-20亿", "20-50亿", ">50亿"], default="缺失")  # amount 单位千元

    sel["量比桶"] = np.select(
        [sel["vol_ratio_20"] < 1, sel["vol_ratio_20"].between(1, 2),
         sel["vol_ratio_20"].between(2, 5), sel["vol_ratio_20"] > 5],
        ["缩量<1x", "温和1-2x", "放量2-5x", "爆量>5x"], default="缺失")

    codes = sorted(sel["ts_code"].unique())
    kl = ldb.read_intraday_features(conn, codes, START, END)
    sel = sel.merge(kl[["ts_code", "trade_date", "upper_shadow", "close_position"]],
                    on=["ts_code", "trade_date"], how="left")
    sel["上影线桶"] = np.select(
        [sel["upper_shadow"] <= 0.1, sel["upper_shadow"].between(0.1, 0.3),
         sel["upper_shadow"] > 0.3],
        ["短≤0.1", "中0.1-0.3", "长>0.3"], default="缺失")
    sel["收盘位置桶"] = np.select(
        [sel["close_position"] <= 0.5, sel["close_position"] > 0.5],
        ["弱≤0.5", "强>0.5"], default="缺失")

    b0 = sel[~sel["is_20cm"]]
    b20 = sel[sel["is_20cm"]]

    out = ["\n## B. 涨幅 ≥7% 未封板群体 vs 封板（同口径对照）\n"]
    cmp_rows = []
    for name, d in (("封板（对照）", ev_board), ("10cm 7%+未板", b0), ("20cm 7%+未板", b20)):
        r = d["ret_open_1"].dropna()
        cmp_rows.append({
            "群体": name, "n": len(r),
            "T+1开均值": r.mean(), "T+1开中位": r.median(), "胜率": (r > 0).mean(),
            "T+1封板率(追赶/晋级)": d["promoted"].mean(),
            "T+1收盘均值": d["ret_close_1"].mean(), "3日均值": d["ret_3d"].mean(),
        })
    out.append(md(pd.DataFrame(cmp_rows), pctcols=("T+1开均值", "T+1开中位", "胜率",
                                                   "T+1封板率(追赶/晋级)", "T+1收盘均值", "3日均值")))

    out.append("\n### B0 因子切片（10cm 7%+ 未板）\n")
    for col in ("量比桶", "成交额桶", "上影线桶", "收盘位置桶"):
        out.append(f"\n{col}：\n\n" + md(dist_table(b0, col),
                   pctcols=("晋级率", "T+1开均值", "T+1开中位", "胜率")))

    out.append("\n### 分年稳健性（T+1 开均值：10cm 7%+未板 vs 封板）\n")
    b0y = b0.copy(); b0y["年"] = b0y["trade_date"].str[:4]
    evy = ev_board.copy(); evy["年"] = evy["trade_date"].str[:4]
    yr = pd.DataFrame({
        "7%+未板": b0y.groupby("年")["ret_open_1"].mean(),
        "封板": evy.groupby("年")["ret_open_1"].mean(),
        "n未板": b0y.groupby("年")["ret_open_1"].size(),
        "n封板": evy.groupby("年")["ret_open_1"].size(),
    }).reset_index()
    out.append(md(yr, pctcols=("7%+未板", "封板")))
    return out


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = ldb.connect()
    try:
        ev, out_a = part_a(conn)
        out_b = part_b(conn, ev)
        path = f"{OUT_DIR}/promotion_gaps_20260827.md"
        with open(path, "w") as f:
            f.write("\n".join(out_a + out_b) + "\n")
        log(f"报告已写 {path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
