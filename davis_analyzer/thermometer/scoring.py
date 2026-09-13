"""温度合成:panel 组装(行情+资金+涨停密度)→ 五族 → composite_z → 温度落库."""

from __future__ import annotations

import sqlite3
import time

import pandas as pd
from loguru import logger

from davis_analyzer.constants import THERMOMETER_WEIGHTS
from davis_analyzer.limitup import db as limitup_db
from davis_analyzer.thermometer import factors
from davis_analyzer.thermometer.universe import load_universe

# ── 涨停密度(代码归属 join,非板块名匹配) ───────────────────────────────

def limit_density_daily(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """(level, index_code, trade_date, limit_ratio) 全日历网格.

    涨停家数来自 limit_pool(dash 日期/无后缀代码)按股票代码 join 申万成分;
    无涨停日补 0,保证 20 日滚动窗口连续。member_count 来自最新成分快照。
    """
    members = pd.read_sql_query(
        "SELECT i.level, m.index_code, m.con_code FROM sw_member m "
        "JOIN sw_index i ON i.index_code=m.index_code AND i.is_pub='1' "
        "JOIN (SELECT index_code, MAX(snapshot_date) ms FROM sw_member GROUP BY index_code) t "
        "ON t.index_code=m.index_code AND t.ms=m.snapshot_date", conn)
    if members.empty:
        return pd.DataFrame(columns=["level", "index_code", "trade_date", "limit_ratio"])
    members["bare"] = members["con_code"].str.split(".").str[0]

    zt = pd.read_sql_query(
        "SELECT trade_date, ts_code FROM limit_pool WHERE pool_kind='limit_up' "
        "AND trade_date>=? AND trade_date<=?",
        conn, params=(limitup_db.to_dash_date(start), limitup_db.to_dash_date(end)))
    zt["bare"] = zt["ts_code"].str.split(".").str[0]
    zt["trade_date"] = zt["trade_date"].str.replace("-", "", regex=False)
    zt = zt.drop_duplicates(["trade_date", "bare"])

    merged = zt.merge(members, on="bare", how="inner")
    zt_n = (merged.groupby(["level", "index_code", "trade_date"]).size()
            .rename("limit_count").reset_index())

    # 全日历网格:每 (level,index_code) × 每交易日,无涨停补 0
    cal = limitup_db.trading_dates(conn, start, end)
    grid = members[["level", "index_code"]].drop_duplicates().merge(
        pd.DataFrame({"trade_date": cal}), how="cross")
    member_n = members.groupby("index_code").size().rename("member_count").reset_index()
    grid = grid.merge(member_n, on="index_code", how="left")
    grid = grid.merge(zt_n, on=["level", "index_code", "trade_date"], how="left")
    grid["limit_count"] = grid["limit_count"].fillna(0)
    grid["limit_ratio"] = grid["limit_count"] / grid["member_count"]
    return grid[["level", "index_code", "trade_date", "limit_ratio"]]


# ── panel 组装 ─────────────────────────────────────────────────────────

def build_panel(conn: sqlite3.Connection, level: str, start: str, end: str) -> pd.DataFrame:
    """拼装某层级 panel:sw_daily + main_net_pct + limit_ratio(列契约见 factors)."""
    uni = load_universe(conn, level)
    codes = uni["index_code"].tolist()
    if not codes:
        return pd.DataFrame()
    ph = ",".join("?" * len(codes))
    px = pd.read_sql_query(
        f"SELECT ts_code AS index_code, trade_date, close, amount, vol, pct_change "
        f"FROM sw_daily WHERE ts_code IN ({ph}) AND trade_date>=? AND trade_date<=? ",
        conn, params=(*codes, start, end))
    flow = pd.read_sql_query(
        f"SELECT index_code, trade_date, main_net_pct FROM sector_moneyflow_daily "
        f"WHERE level=? AND index_code IN ({ph}) AND trade_date>=? AND trade_date<=?",
        conn, params=(level, *codes, start, end))
    dens = limit_density_daily(conn, start, end)
    dens = dens[dens["level"] == level]
    panel = px.merge(flow, on=["index_code", "trade_date"], how="left")
    panel = panel.merge(dens[["index_code", "trade_date", "limit_ratio"]],
                        on=["index_code", "trade_date"], how="left")
    # 无资金流/无涨停的日 → 0(资金缺失视作中性,避免 NaN 截断窗口)
    panel["main_net_pct"] = panel["main_net_pct"].fillna(0.0)
    panel["limit_ratio"] = panel["limit_ratio"].fillna(0.0)
    return panel.sort_values(["index_code", "trade_date"]).reset_index(drop=True)


# ── 温度合成 ───────────────────────────────────────────────────────────

def _score_level(panel: pd.DataFrame) -> pd.DataFrame:
    """单层级全历史:factors → composite_z → temperature(每日截面 rank pct)."""
    df = factors.family_scores(factors.add_factor_columns(panel))
    z = (
        THERMOMETER_WEIGHTS["momentum"] * df["mom_score"]
        + THERMOMETER_WEIGHTS["flow"] * df["flow_score"]
        + THERMOMETER_WEIGHTS["volume"] * df["vol_score"]
        + THERMOMETER_WEIGHTS["trend"] * df["trend_score"]
        + THERMOMETER_WEIGHTS["limit"] * df["limit_score"]
    )
    df["composite_z"] = z
    df["temperature"] = df.groupby("trade_date")["composite_z"].rank(
        pct=True, method="average") * 100
    return df


def score_history(conn: sqlite3.Connection, start: str, end: str) -> dict:
    """L1+L2 全历史评分并落库(INSERT OR REPLACE 全量覆写,幂等);衍生读数后算."""
    rows_total = 0
    now = time.time()
    for level in ("L1", "L2"):
        panel = build_panel(conn, level, start, end)
        if panel.empty:
            logger.warning("build_panel {} 为空,跳过", level)
            continue
        uni = load_universe(conn, level)
        name_by_code = dict(zip(uni["index_code"], uni["name"]))
        df = _score_level(panel)
        # 窗口不足的日子(任一水平项 NaN)没有温度意义,整行丢弃
        df = df.dropna(subset=["mom_level", "flow_level", "vol_level",
                               "trend_level", "limit_level"], how="any")
        if df.empty:
            logger.warning("{} 无完整窗口日", level)
            continue
        # 衍生读数:按 index_code 时序
        df = df.sort_values(["index_code", "trade_date"])
        df["delta_temp5"] = df.groupby("index_code")["temperature"].transform(
            lambda s: s - s.shift(5))
        hot = (df["temperature"] > 80).astype(int)
        grp_reset = (hot != hot.groupby(df["index_code"]).shift(1)).cumsum()
        df["hot_streak"] = hot.groupby([df["index_code"], grp_reset]).cumsum() * hot
        conn.executemany(
            "INSERT OR REPLACE INTO thermometer_sector "
            "(trade_date,level,index_code,name,mom_score,flow_score,vol_score,"
            "trend_score,limit_score,composite_z,temperature,delta_temp5,hot_streak,fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (r.trade_date, level, r.index_code, name_by_code.get(r.index_code),
                 r.mom_score, r.flow_score, r.vol_score, r.trend_score, r.limit_score,
                 r.composite_z, r.temperature, r.delta_temp5, int(r.hot_streak), now)
                for r in df.itertuples()
            ],
        )
        rows_total += len(df)
        logger.info("thermometer {}: {} 行落库", level, len(df))
    conn.commit()
    return {"rows": rows_total}


def latest_snapshot(conn: sqlite3.Connection, day: str) -> pd.DataFrame:
    """某日全层级温度快照(按温度降序),日报/卡片用."""
    return pd.read_sql_query(
        "SELECT * FROM thermometer_sector WHERE trade_date=? ORDER BY temperature DESC",
        conn, params=(day,))
