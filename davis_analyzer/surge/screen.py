"""surge 主管线编排: 筛选→巨潮→cyq→九维→形态/标签→综合分→幂等入库(spec §7)."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta

import pandas as pd
from loguru import logger

from davis_analyzer.constants import PATTERN_PARAMS
from davis_analyzer.surge import chips, cninfo, db, factors, pattern

_NAN = float("nan")
_HIST_DAYS = 400  # 日线回看自然日(≥250交易日)


def _tushare_pro():
    from davis_analyzer.tushare_client import TushareClient

    return TushareClient().pro


def _read_hist(conn: sqlite3.Connection, ts_code: str, end_day: str) -> pd.DataFrame:
    start = (datetime.strptime(end_day, "%Y%m%d")
             - timedelta(days=_HIST_DAYS)).strftime("%Y%m%d")
    return pd.read_sql_query(
        "SELECT ts_code, trade_date, open, high, low, close, vol, adj_factor "
        "FROM daily_price WHERE ts_code=? AND trade_date>=? AND trade_date<=? "
        "ORDER BY trade_date",
        conn, params=(ts_code, start, end_day))


def _read_moneyflow_hist(conn, ts_code: str, end_day: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT trade_date, buy_lg_amount, sell_lg_amount, buy_elg_amount, "
        "sell_elg_amount, net_mf_amount FROM moneyflow "
        "WHERE ts_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 8",
        conn, params=(ts_code, end_day))


def _consecutive_loss(conn: sqlite3.Connection, ts_code: str) -> bool:
    rows = conn.execute(
        "SELECT end_date, payload FROM financial "
        "WHERE ts_code=? AND endpoint='income' "
        "ORDER BY end_date DESC LIMIT 8", (ts_code,)).fetchall()
    parsed: list[tuple[str, dict]] = []
    for end_date, payload in rows:
        if not payload:
            continue
        try:
            data = json.loads(payload)
            row = data[0] if isinstance(data, list) and data else data
            if isinstance(row, dict):
                parsed.append((end_date, row))
        except Exception:
            continue
    return factors.check_consecutive_loss(parsed)


def run_day(
    day: str | None = None, *, conn: sqlite3.Connection | None = None,
    pro=None, do_cninfo: bool = True,
) -> dict:
    conn = conn or db.connect()
    pro = pro or _tushare_pro()
    day = db.normalize_date(day or db.latest_trade_date(conn) or "")
    if not day:
        raise RuntimeError("daily_price 为空,无法确定交易日")
    pool = db.read_pool(conn, day)
    logger.info("surge {} 命中池 {} 只", day, len(pool))
    empty = {"day": day, "pool_n": len(pool), "snapshot_df": pool,
             "pattern_df": pd.DataFrame(), "tags_df": pd.DataFrame(),
             "cyq_day": "", "cninfo_stats": {}}
    if pool.empty:
        return empty
    codes = pool["ts_code"].tolist()

    # 巨潮(命中池;整批失败降级)
    cninfo_stats: dict = {}
    if do_cninfo:
        try:
            cninfo_stats = cninfo.sync_cninfo(conn, codes, day)
        except Exception as e:
            logger.warning("cninfo 整批失败(事件维度降级): {}", e)

    # 筹码(按日期一次)
    cyq_day = chips.ensure_cyq(conn, pro, day)
    cyq_hist = chips.read_cyq(conn, codes, cyq_day or day, lookback=8)
    cyq_today: pd.DataFrame | None = None
    if not cyq_hist.empty and cyq_day:
        cyq_today = cyq_hist[cyq_hist.trade_date == cyq_day].set_index("ts_code")

    # 行业截面
    sw_ind = db.read_sw_industry(conn, codes)
    ind_map: dict[str, str] = {}
    ind_name: dict[str, str] = {}
    if not sw_ind.empty:
        ind_map = dict(zip(sw_ind["ts_code"], sw_ind["index_code"]))
        ind_name = dict(zip(sw_ind["ts_code"], sw_ind["name"]))
    sw_all = db.read_sw_daily_all(conn, day)
    ind_mom = (factors.industry_momentum(sw_all).set_index("index_code")
               if not sw_all.empty else pd.DataFrame())

    # 事件帧(批量)
    d0 = datetime.strptime(day, "%Y%m%d")
    corp_w = pd.read_sql_query(
        "SELECT ts_code, ann_date, event_type, direction FROM corp_event "
        "WHERE ann_date>=? AND ann_date<=?",
        conn, params=((d0 - timedelta(days=int(PATTERN_PARAMS["event_window"]))
                       ).strftime("%Y%m%d"), day))
    major_w = pd.read_sql_query(
        "SELECT ts_code, ann_date, event_type, title FROM major_events "
        "WHERE ann_date>=?",
        conn, params=((d0 - timedelta(days=int(PATTERN_PARAMS["major_event_window"]))
                       ).strftime("%Y%m%d"),))
    pledge_map: dict[str, float] = {}
    for code in codes:
        row = conn.execute(
            "SELECT magnitude FROM corp_event WHERE ts_code=? AND "
            "event_type='pledge' ORDER BY ann_date DESC LIMIT 1", (code,)).fetchone()
        if row and row[0] is not None:
            pledge_map[code] = float(row[0])
    research_n = pd.read_sql_query(
        "SELECT ts_code, COUNT(DISTINCT org_name) AS n FROM research "
        "WHERE report_date>=? GROUP BY ts_code",
        conn, params=((d0 - timedelta(days=90)).strftime("%Y%m%d"),))
    research_map = (dict(zip(research_n["ts_code"], research_n["n"]))
                    if not research_n.empty else {})

    snap_rows: list[dict] = []
    pat_rows: list[dict] = []
    tag_rows: list[dict] = []
    for _, row in pool.iterrows():
        code = row["ts_code"]
        px = _read_hist(conn, code, day)
        if len(px) < 30:
            continue
        pos = factors.compute_position(px)
        mf = _read_moneyflow_hist(conn, code, day)
        money = factors.compute_moneyflow(
            mf.sort_values("trade_date"), float(row["amount"] or 0))

        cyq = (cyq_today.loc[code] if cyq_today is not None
               and code in cyq_today.index else None)
        chips_d = {k: (float(cyq[k]) if cyq is not None and pd.notna(cyq.get(k))
                       else _NAN)
                   for k in ("cost_5pct", "cost_50pct", "cost_95pct", "weight_avg")}
        wr = float(cyq["winner_rate"]) if cyq is not None and pd.notna(
            cyq.get("winner_rate")) else _NAN
        wr_prev5 = _NAN
        if not cyq_hist.empty:
            hist = cyq_hist[(cyq_hist.ts_code == code)
                            & (cyq_hist.trade_date < (cyq_day or day))]
            hist = hist.sort_values("trade_date")
            if len(hist) >= 5 and pd.notna(hist["winner_rate"].iloc[-5]):
                wr_prev5 = float(hist["winner_rate"].iloc[-5])
        wr_delta = wr - wr_prev5 if wr == wr and wr_prev5 == wr_prev5 else _NAN

        rs = factors.compute_resistance_support(px, cyq)
        ind_row = (ind_mom.loc[ind_map[code]]
                   if ind_map.get(code) and not ind_mom.empty
                   and ind_map[code] in ind_mom.index else None)
        vol_price = bool(
            len(px) >= 40
            and float(px["close"].iloc[-1]) / float(px["close"].iloc[-21]) - 1 > 0.15
            and float(px["vol"].tail(20).mean())
            > float(px["vol"].iloc[-40: -20].mean()) * 1.5)
        hype, risk = factors.classify_hype_risk(
            corp_events=(corp_w[corp_w.ts_code == code]
                         if not corp_w.empty else corp_w),
            major_events=(major_w[major_w.ts_code == code]
                          if not major_w.empty else major_w),
            pledge_ratio=pledge_map.get(code),
            fin_consecutive_loss=_consecutive_loss(conn, code),
            is_st=bool(row["is_st"]), industry_row=ind_row,
            vol_price_ok=vol_price,
            research_count=int(research_map.get(code, 0)), day=day)
        comp = factors.compute_composite(
            money=money, chips=chips_d,
            winner={"winner_rate": wr, "winner_delta_5d": wr_delta},
            position=pos, rs=rs, hype=hype, risk=risk)

        first_day = conn.execute(
            "SELECT MIN(trade_date) FROM daily_price WHERE ts_code=?",
            (code,)).fetchone()[0]
        is_new = int(first_day and (d0 - datetime.strptime(
            first_day, "%Y%m%d")).days < 90)
        snap_rows.append({
            "trade_date": day, "ts_code": code, "name": row.get("name"),
            "industry": ind_name.get(code), "pct_chg": float(row["pct_chg"]),
            "amount_k": float(row["amount"] or 0),
            **pos, "ladder_label": "非涨停", "is_st": int(row["is_st"]),
            "is_new": is_new,
            **money, **chips_d,
            "winner_rate": wr, "winner_delta_5d": wr_delta,
            "resistance_price": rs["resistance_price"],
            "resistance_dist": rs["resistance_dist"],
            "support_price": rs["support_price"],
            "support_dist": rs["support_dist"],
            "hype_tags": json.dumps(hype, ensure_ascii=False),
            "risk_flags": json.dumps(risk, ensure_ascii=False),
            "hype_count": len(hype), "risk_flag_count": len(risk),
            "composite": comp, "fetched_at": time.time(),
        })
        pat = pattern.detect_pattern(px)
        if pat:
            pat_rows.append({"trade_date": day, "ts_code": code, **pat,
                             "fetched_at": time.time()})
        tags = pattern.detect_tags(px, cyq, rs["resistance_dist"], pos)
        tag_rows += [{"trade_date": day, "ts_code": code, "tag": t,
                      "fetched_at": time.time()} for t in tags]

    snap = pd.DataFrame(snap_rows)
    if not snap.empty:
        snap["rank"] = snap["composite"].rank(ascending=False, method="min").astype(int)
    # 入库(幂等: 先删当日)
    for table, df in (("surge_snapshot", snap),
                      ("surge_pattern_hits", pd.DataFrame(pat_rows)),
                      ("surge_tags", pd.DataFrame(tag_rows))):
        conn.execute(f"DELETE FROM {table} WHERE trade_date=?", (day,))
        if not df.empty:
            df.to_sql(table, conn, if_exists="append", index=False)
    conn.commit()
    logger.info("surge {} 完成: snapshot={} pattern={} tags={}",
                day, len(snap), len(pat_rows), len(tag_rows))
    return {"day": day, "pool_n": len(pool), "snapshot_df": snap,
            "pattern_df": pd.DataFrame(pat_rows),
            "tags_df": pd.DataFrame(tag_rows), "cyq_day": cyq_day,
            "cninfo_stats": cninfo_stats}


def backfill_replay(conn: sqlite3.Connection, pro, dates: list[str]) -> list[dict]:
    """历史截面回放(spec §7): cyq 按日期回补 + 重放 snapshot(巨潮不回拉)."""
    chips.backfill_cyq(conn, pro, dates)
    return [run_day(d, conn=conn, pro=pro, do_cninfo=False) for d in dates]
