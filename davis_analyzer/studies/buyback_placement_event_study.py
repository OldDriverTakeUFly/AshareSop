"""定增/回购公告效应事件研究(2026-09-14).

问题:定增与回购公告对股价的利空/利好,用全市场实证校验:
  1. 预案公告后 T+1 / T+5 / T+20 / T+60(约一季度)收益(原始+相对上证超额);
  2. 实施开始→结束期间的价格变化(回购=首末进度公告的 end_date;定增=申购日→新增股份上市日)。

数据:Tushare repurchase(回购)/ stk_seasoned(股票增发,仅取非公开=定增);
行情=本地 market_data.db daily_price(后复权 close×adj_factor),基准=index_daily 000001.SH。
缓存:storage/files/smb_event/*.csv;结果:davis_analyzer/studies/buyback_placement_event_study_20260914.json。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

REPO = Path("/home/leo/Projects/CodeAgentDashboard")
CACHE = REPO / "storage/files/smb_event"
DB_PATH = REPO / "storage/database/market_data.db"
OUT_JSON = Path(__file__).with_name("buyback_placement_event_study_20260914.json")

PULL_START, PULL_END = "20200101", "20260914"   # 原始拉取窗(比研究窗早1年,锚定跨年方案)
T0_START, T0_END = "20210101", "20260630"       # 预案公告窗(T+60 至 2026-09 前可收口)
WINDOWS = (1, 5, 20, 60)                        # 交易日窗口
BENCH_CODE = "000001.SH"                        # 上证指数(先例:解禁事件研究同基准)


# ── 数据拉取(带 CSV 缓存) ──────────────────────────────────────────────

def _ranges(start: str, end: str, months: int) -> list[tuple[str, str]]:
    out, cur, stop = [], pd.Timestamp(start), pd.Timestamp(end)
    while cur <= stop:
        seg_end = min(cur + pd.DateOffset(months=months) - pd.Timedelta(days=1), stop)
        out.append((cur.strftime("%Y%m%d"), seg_end.strftime("%Y%m%d")))
        cur = cur + pd.DateOffset(months=months)
    return out


def pull(api: str, fname: str, months: int) -> pd.DataFrame:
    """按时间窗分页拉取并缓存;缓存存在则直接读。"""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / fname
    if path.exists():
        logger.info(f"cache hit {fname}: {path.stat().st_size} bytes")
        return pd.read_csv(path, dtype=str)

    from stockhot.tushare_config import get_pro_api

    pro = get_pro_api()
    frames: list[pd.DataFrame] = []
    for lo, hi in _ranges(PULL_START, PULL_END, months):
        got = False
        for attempt in range(3):
            try:
                df = pro.query(api, start_date=lo, end_date=hi)
                frames.append(df)
                logger.info(f"{api} {lo}-{hi}: {len(df)} rows")
                got = True
                break
            except Exception as e:  # noqa: BLE001
                logger.warning(f"{api} {lo}-{hi} attempt{attempt} error: {e}")
                time.sleep(3)
        if not got:
            logger.error(f"{api} {lo}-{hi} 全部重试失败,按空窗处理")
        time.sleep(0.25)
    full = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    full.to_csv(path, index=False)
    logger.info(f"saved {fname}: {len(full)} rows")
    return full


def ensure_adj_factors(trade_days: list[str]) -> dict[tuple[str, str], float]:
    """按交易日全市场拉 adj_factor 覆盖层(修 market_data.db 2025 年 76% 缺口)。

    返回 (ts_code, trade_date)→adj_factor 映射。
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "adj_factor_2020_2026.csv.gz"
    if path.exists():
        df = pd.read_csv(path, dtype={"ts_code": str, "trade_date": str})
        logger.info(f"cache hit {path.name}: {len(df)} rows")
    else:
        from stockhot.tushare_config import get_pro_api

        pro = get_pro_api()
        frames = []
        for i, d in enumerate(trade_days):
            got = False
            for attempt in range(3):
                try:
                    df = pro.query("adj_factor", trade_date=d)
                    frames.append(df)
                    got = True
                    if i % 100 == 0:
                        logger.info(f"adj_factor {d}: {len(df)} rows ({i}/{len(trade_days)})")
                    break
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"adj_factor {d} attempt{attempt} error: {e}")
                    time.sleep(3)
            if not got:
                logger.error(f"adj_factor {d} 拉取失败")
            time.sleep(0.13)
        df = pd.concat(frames, ignore_index=True)
        df.to_csv(path, index=False, compression="gzip")
        logger.info(f"saved {path.name}: {len(df)} rows")
    return {(r.ts_code, r.trade_date): float(r.adj_factor) for r in df.itertuples()}


# ── 行情读取 ──────────────────────────────────────────────────────────

class PriceBook:
    """后复权价格:个股逐码懒加载(close×adj_factor,adj 优先用覆盖层);基准指数一次载入。"""

    def __init__(self, db: sqlite3.Connection, adj_override: dict[tuple[str, str], float] | None = None) -> None:
        self.db = db
        self.adj_override = adj_override or {}
        self._cache: dict[str, tuple[np.ndarray, np.ndarray] | None] = {}
        bd = db.execute(
            "SELECT trade_date, close FROM index_daily WHERE ts_code=? ORDER BY trade_date",
            (BENCH_CODE,),
        ).fetchall()
        self.bench_dates = np.array([r[0] for r in bd])
        self.bench_close = np.array([float(r[1]) for r in bd])

    def stock(self, ts_code: str) -> tuple[np.ndarray, np.ndarray] | None:
        if ts_code in self._cache:
            return self._cache[ts_code]
        rows = self.db.execute(
            "SELECT trade_date, close, adj_factor FROM daily_price "
            "WHERE ts_code=? AND close IS NOT NULL "
            "GROUP BY trade_date ORDER BY trade_date",
            (ts_code,),
        ).fetchall()
        dates, px = [], []
        for d, close, adj in rows:
            a = self.adj_override.get((ts_code, d), adj)
            if a is None:
                continue  # 真缺复权因子(极少),跳过该日
            dates.append(d)
            px.append(float(close) * float(a))
        val = (np.array(dates), np.array(px)) if dates else None
        self._cache[ts_code] = val
        return val

    @staticmethod
    def _asof(dates: np.ndarray, day: str) -> int:
        i = int(np.searchsorted(dates, day, side="right")) - 1
        return i if i >= 0 else -1

    def bench_ret(self, d0: str, d1: str) -> float | None:
        i, j = self._asof(self.bench_dates, d0), self._asof(self.bench_dates, d1)
        if i < 0 or j <= i:
            return None
        return float(self.bench_close[j] / self.bench_close[i] - 1)

    def event_windows(self, ts_code: str, t0: str) -> dict | None:
        """以个股自身交易日轴计算 d0(公告日当天)与 dk(k∈WINDOWS),及相对上证超额。

        base=公告日前(含)最后一个有价交易日收盘——公告若在盘后披露,dk 全部落在公告后;
        若盘中披露,d0 会捕捉当日反应,dk 从当日收盘起算。
        """
        s = self.stock(ts_code)
        if s is None:
            return None
        dates, px = s
        b = self._asof(dates, t0)
        if b < 0:
            return None
        out: dict = {"base_date": str(dates[b])}
        gap = (datetime.strptime(t0, "%Y%m%d") - datetime.strptime(str(dates[b]), "%Y%m%d")).days
        out["gap_cal_days"] = gap
        if gap > 10:
            out["suspended"] = True  # 公告日处于停牌中(缺交易日>10自然日),基准=停牌前收盘
        if b >= 1:
            out["d0"] = float(px[b] / px[b - 1] - 1)
        for k in WINDOWS:
            if b + k < len(px):
                end_day = str(dates[b + k])
                r = float(px[b + k] / px[b] - 1)
                out[f"d{k}"] = r
                br = self.bench_ret(str(dates[b]), end_day)
                if br is not None:
                    out[f"ex{k}"] = (1 + r) / (1 + br) - 1
        return out

    def span_return(self, ts_code: str, d_start: str, d_end: str) -> dict | None:
        """区间收益(d_start 当日收盘→d_end 当日收盘,均为个股自身 asof 口径)。"""
        s = self.stock(ts_code)
        if s is None:
            return None
        dates, px = s
        i, j = self._asof(dates, d_start), self._asof(dates, d_end)
        if i < 0 or j < i:
            return None
        r = float(px[j] / px[i] - 1)
        br = self.bench_ret(str(dates[i]), str(dates[j]))
        return {"days": int(j - i), "ret": r, "ex": ((1 + r) / (1 + br) - 1) if br is not None else None}


# ── 事件组装 ──────────────────────────────────────────────────────────

def build_buyback_programs(raw: pd.DataFrame) -> pd.DataFrame:
    """回购:每个「预案」行=一个回购方案;其后(下一预案前)的进度行归入该方案。

    实施期=方案内全部进度行 end_date 的 min→max(月度粒度);
    终态=停止(有停止行) / 完成(有进度行) / 未实施(无进度行)。
    """
    df = raw.copy()
    df["ann_date"] = df["ann_date"].fillna("")
    df = df[df["ann_date"] != ""].sort_values(["ts_code", "ann_date"])
    prog_rows: list[dict] = []
    for ts_code, g in df.groupby("ts_code", sort=False):
        cur: dict | None = None
        for _, r in g.iterrows():
            proc = str(r.get("proc", ""))
            if proc == "预案":
                cur = {
                    "ts_code": ts_code,
                    "t0": str(r["ann_date"]),
                    "plan_amount_yi": round(float(r["amount"]) / 1e8, 2) if pd.notna(r.get("amount")) else np.nan,
                    "exp_date": r.get("exp_date") or "",
                    "impl_first": "", "impl_last": "",
                    "total_vol_wan": 0.0, "total_amt_yi": 0.0,
                    "n_progress": 0, "stopped": False,
                }
                prog_rows.append(cur)
            elif cur is None:
                continue  # 预案在拉取窗之前的方案,无锚不纳入
            else:
                ed = r.get("end_date")
                if proc == "停止":
                    cur["stopped"] = True
                if pd.notna(ed) and str(ed) not in ("", "nan"):
                    eds = str(int(float(ed))) if "." in str(ed) else str(ed)
                    if not cur["impl_first"]:
                        cur["impl_first"] = eds
                    cur["impl_last"] = eds
                    cur["n_progress"] += 1
                    if pd.notna(r.get("vol")):
                        cur["total_vol_wan"] += float(r["vol"]) / 1e4
                    if pd.notna(r.get("amount")):
                        cur["total_amt_yi"] += float(r["amount"]) / 1e8
    out = pd.DataFrame(prog_rows)
    out["outcome"] = np.where(out["stopped"], "停止", np.where(out["n_progress"] > 0, "完成", "未实施"))
    return out


def build_spo_projects(raw: pd.DataFrame) -> pd.DataFrame:
    """定增:stk_seasoned 按项目归组(ts_code+first_ann_date),快照字段取组内非空。"""
    df = raw.copy()
    df = df[df["fo_type"] == "非公开"]  # 公开增发极少,剔除
    df["first_ann_date"] = df["first_ann_date"].fillna(df["ann_date"])
    df = df[df["first_ann_date"].notna()]

    def first_notna(g: pd.DataFrame, col: str) -> str:
        s = g[col].dropna()
        return str(s.iloc[0]) if len(s) else ""

    def last_notna(g: pd.DataFrame, col: str) -> float:
        s = g[col].dropna()
        return float(s.iloc[-1]) if len(s) else np.nan

    rows: list[dict] = []
    for (ts_code, fad), g in df.groupby(["ts_code", "first_ann_date"], sort=False):
        g = g.sort_values("ann_date")
        last = g.iloc[-1]
        chg = str(last.get("plan_chg_type", "") or "")
        rows.append({
            "ts_code": ts_code,
            "t0": str(fad),
            "final_stage": str(last.get("cur_stage", "")),
            "terminate_date": str(last.get("plan_chg_ann_dt", "") or "") if ("终止" in chg or str(last.get("cur_stage")) == "终止") else "",
            "apply_date": first_notna(g, "apply_date"),
            "list_date": first_notna(g, "new_share_list_dt"),
            "raise_plan_yi": round(last_notna(g, "fo_raise_total") / 1e8, 2) if not np.isnan(last_notna(g, "fo_raise_total")) else np.nan,
            "raise_act_yi": round(last_notna(g, "fo_raise_total_act") / 1e8, 2) if not np.isnan(last_notna(g, "fo_raise_total_act")) else np.nan,
            "price_ratio": last_notna(g, "fo_price_ratio"),
            "purpose": str(last.get("fo_purpose", "") or "")[:60],
        })
    out = pd.DataFrame(rows)
    out["final_stage"] = out["final_stage"].replace({"实施": "实施完成"})
    return out


# ── 统计 ──────────────────────────────────────────────────────────────

def agg(vals: list) -> dict:
    a = np.array([float(v) for v in vals if v is not None and not (isinstance(v, float) and np.isnan(v))], dtype=float)
    if len(a) == 0:
        return {"n": 0}
    return {
        "n": int(len(a)),
        "mean": round(float(a.mean()) * 100, 2),
        "median": round(float(np.median(a)) * 100, 2),
        "win": round(float((a > 0).mean()) * 100, 1),
        "q25": round(float(np.quantile(a, 0.25)) * 100, 2),
        "q75": round(float(np.quantile(a, 0.75)) * 100, 2),
    }


def bucketize(vals: list) -> dict:
    a = np.array([float(v) for v in vals if v is not None and not (isinstance(v, float) and np.isnan(v))], dtype=float)
    if len(a) == 0:
        return {}
    edges = [(-np.inf, -0.20), (-0.20, -0.05), (-0.05, 0.05), (0.05, 0.20), (0.20, np.inf)]
    names = ["≤-20%", "-20~-5%", "-5~+5%", "+5~+20%", "≥+20%"]
    return {n: round(float(((a > lo) & (a <= hi)).mean()) * 100, 1) for (lo, hi), n in zip(edges, names, strict=True)}


def stats_from_per(per: pd.DataFrame, label: str, n_events: int) -> dict:
    stats = {"label": label, "N_events": int(n_events), "has_price": int(len(per))}
    for key in ["d0", *(f"d{k}" for k in WINDOWS), *(f"ex{k}" for k in WINDOWS)]:
        stats[key] = agg(per[key].tolist()) if key in per else {"n": 0}
    if "d60" in per:
        stats["d60_buckets"] = bucketize(per["d60"].tolist())
        stats["ex60_buckets"] = bucketize(per["ex60"].tolist())
    if "suspended" in per:
        stats["n_suspended"] = int(per["suspended"].eq(True).sum())
    return stats


def window_table(events: pd.DataFrame, book: PriceBook, label: str) -> tuple[dict, pd.DataFrame]:
    rows = []
    for _, e in events.iterrows():
        w = book.event_windows(e["ts_code"], e["t0"])
        if w:
            rows.append({**e.to_dict(), **w})
    per = pd.DataFrame(rows)
    return stats_from_per(per, label, len(events)), per


def impl_table(events: pd.DataFrame, book: PriceBook, label: str, col_s: str, col_e: str,
               max_days: int = 500) -> tuple[dict, pd.DataFrame]:
    stats = {"label": label, "N_events": int(len(events))}
    rows = []
    for _, e in events.iterrows():
        ds, de = str(e.get(col_s, "")), str(e.get(col_e, ""))
        if len(ds) == 8 and len(de) == 8 and ds <= de:
            w = book.span_return(e["ts_code"], ds, de)
            if w:
                rows.append({**e.to_dict(), **w})
    per = pd.DataFrame(rows)
    over = int((per["days"] > max_days).sum()) if len(per) else 0
    per = per[per["days"] <= max_days] if len(per) else per
    stats["has_price"] = int(len(per))
    stats["dropped_over_maxdays"] = over
    stats["max_days"] = max_days
    if len(per):
        d = per["days"]
        stats["days"] = {"n": int(len(d)), "median": float(d.median()), "mean": round(float(d.mean()), 1),
                         "p90": float(d.quantile(0.9))}
    else:
        stats["days"] = {"n": 0}
    stats["ret"] = agg(per["ret"].tolist()) if len(per) else {"n": 0}
    stats["ex"] = agg(per["ex"].tolist()) if len(per) else {"n": 0}
    return stats, per


# ── 主流程 ────────────────────────────────────────────────────────────

def main(skip_pull: bool = False) -> None:
    if skip_pull:
        rp = pd.read_csv(CACHE / "repurchase_2020_2026.csv", dtype=str)
        sp = pd.read_csv(CACHE / "stk_seasoned_2020_2026.csv", dtype=str)
    else:
        rp = pull("repurchase", "repurchase_2020_2026.csv", months=1)
        sp = pull("stk_seasoned", "stk_seasoned_2020_2026.csv", months=3)

    bb = build_buyback_programs(rp)
    spo = build_spo_projects(sp)
    logger.info(f"回购方案 {len(bb)} / 定增项目 {len(spo)}(非公开)")

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    trade_days = [r[0] for r in con.execute(
        "SELECT trade_date FROM index_daily WHERE ts_code=? AND trade_date>='20201201' ORDER BY trade_date",
        (BENCH_CODE,),
    )]
    adj_ov = ensure_adj_factors(trade_days)
    logger.info(f"adj_factor 覆盖层 {len(adj_ov)} 条")
    book = PriceBook(con, adj_override=adj_ov)

    results: dict = {
        "meta": {
            "generated": datetime.now().isoformat(timespec="seconds"),
            "pull_window": [PULL_START, PULL_END],
            "t0_window": [T0_START, T0_END],
            "windows_trading_days": list(WINDOWS),
            "benchmark": BENCH_CODE,
            "price": "daily_price 后复权(close×adj_factor)",
            "sources": {
                "回购": "Tushare repurchase(预案=proc:预案 行;实施期=进度行 end_date min→max)",
                "定增": "Tushare stk_seasoned(非公开;项目=ts_code+first_ann_date;实施期=apply_date→new_share_list_dt)",
            },
        },
        "counts": {
            "回购_全部方案_2020_2026": int(len(bb)),
            "回购_预案t0窗内": 0,
            "定增_全部项目_非公开_2020_2026": int(len(spo)),
            "定增_预案t0窗内": 0,
        },
    }
    bb_t0 = bb[(bb["t0"] >= T0_START) & (bb["t0"] <= T0_END)].copy()
    spo_t0 = spo[(spo["t0"] >= T0_START) & (spo["t0"] <= T0_END)].copy()
    results["counts"]["回购_预案t0窗内"] = int(len(bb_t0))
    results["counts"]["定增_预案t0窗内"] = int(len(spo_t0))

    def dump(st: dict, per: pd.DataFrame, tag: str) -> None:
        results[tag] = st
        per.to_csv(CACHE / f"per_{tag}.csv", index=False)

    # ── 回购:预案公告后窗口 ──
    bb_wins: dict[str, pd.DataFrame] = {}
    for label, ev in [
        ("回购_全部", bb_t0),
        ("回购_完成", bb_t0[bb_t0["outcome"] == "完成"]),
        ("回购_停止", bb_t0[bb_t0["outcome"] == "停止"]),
        ("回购_未实施", bb_t0[bb_t0["outcome"] == "未实施"]),
    ]:
        st, per = window_table(ev, book, label)
        bb_wins[label] = per
        dump(st, per, label)
    per_bb_all = bb_wins["回购_全部"]
    if "suspended" in per_bb_all and len(per_bb_all):
        sus_mask = per_bb_all["suspended"].eq(True)
        for label, sub in [
            ("回购_正常交易", per_bb_all[~sus_mask]),
            ("回购_停牌复牌", per_bb_all[sus_mask]),
        ]:
            results[label] = stats_from_per(sub, label, len(sub))
            sub.to_csv(CACHE / f"per_{label}.csv", index=False)

    # 回购实施期(不限预案窗,数据截至 2026-09-14;要求≥2个进度月,窗口才可测)
    bb_impl = bb[bb["n_progress"] >= 2]
    for label, ev in [("回购_实施期_全部", bb_impl), ("回购_实施期_完成", bb_impl[bb_impl["outcome"] == "完成"])]:
        st, per = impl_table(ev, book, label, "impl_first", "impl_last")
        dump(st, per, label)

    # ── 定增:预案公告后窗口(按终态分组) ──
    spo_wins: dict[str, pd.DataFrame] = {}
    for label, ev in [
        ("定增_全部", spo_t0),
        ("定增_实施完成", spo_t0[spo_t0["final_stage"] == "实施完成"]),
        ("定增_终止", spo_t0[spo_t0["final_stage"] == "终止"]),
        ("定增_在途", spo_t0[~spo_t0["final_stage"].isin(["实施完成", "终止"])]),
    ]:
        st, per = window_table(ev, book, label)
        spo_wins[label] = per
        dump(st, per, label)

    # 停牌拆分:公告日停牌中(基准=停牌前收盘,d1≈复牌首日)vs 正常交易
    per_all = spo_wins["定增_全部"]
    if "suspended" in per_all and len(per_all):
        sus_mask = per_all["suspended"].eq(True)
        for label, sub in [
            ("定增_正常交易", per_all[~sus_mask]),
            ("定增_停牌复牌", per_all[sus_mask]),
        ]:
            results[label] = stats_from_per(sub, label, len(sub))
            sub.to_csv(CACHE / f"per_{label}.csv", index=False)

    # 定增发行实施期:申购日→新增股份上市日(不限预案窗)
    spo_impl = spo[(spo["apply_date"].str.len() == 8) & (spo["list_date"].str.len() == 8)
                   & (spo["apply_date"] <= spo["list_date"])]
    st, per = impl_table(spo_impl, book, "定增_发行实施期", "apply_date", "list_date")
    dump(st, per, "定增_发行实施期")

    # 定增终止公告(独立事件,t0=方案变动终止公告日)
    term = spo[(spo["terminate_date"].str.len() == 8)
               & (spo["terminate_date"] >= T0_START) & (spo["terminate_date"] <= "20260914")]
    st, per = window_table(term.assign(t0=term["terminate_date"]), book, "定增_终止公告")
    dump(st, per, "定增_终止公告")

    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info(f"results → {OUT_JSON}")

    for k, v in results.items():
        if isinstance(v, dict) and "N_events" in v:
            d1, d5, d20, d60 = (v.get(f"d{i}", {}) for i in (1, 5, 20, 60))
            print(f"{k:16s} N={v['N_events']:5d} | d1 {d1.get('median', '-'):>7}/win{d1.get('win', '-'):>5}"
                  f" | d5 {d5.get('median', '-'):>7}/win{d5.get('win', '-'):>5}"
                  f" | d20 {d20.get('median', '-'):>7}/win{d20.get('win', '-'):>5}"
                  f" | d60 {d60.get('median', '-'):>7}/win{d60.get('win', '-'):>5}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-pull", action="store_true", help="直接用缓存 CSV")
    args = ap.parse_args()
    main(skip_pull=args.skip_pull)
