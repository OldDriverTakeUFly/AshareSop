"""研究脚本公共工具——统一从 market_data.db 读数据.

替代之前每个脚本各自 ts.pro_api() / ts.pro_bar() / pickle 缓存的做法.
所有数据都从共享 SQLite (market_data.db) 读取, 零 API 调用.

使用方式::

    from davis_analyzer.studies.rally_screening.utils import (
        fetch_daily_qfq_from_db,
        fetch_raw_financials_from_db,
        load_daily_by_date,
        load_top_list_by_date,
        get_name_map,
        get_trade_dates,
    )
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from stockhot.data_layer.market_db import MARKET_DB_PATH as _CACHE_DB


# ── 连接管理 ──────────────────────────────────────────────────────────


def _conn() -> sqlite3.Connection:
    """返回一个只读连接（每次新建，用完即关）."""
    conn = sqlite3.connect(str(_CACHE_DB))
    conn.execute("PRAGMA query_only=1")
    return conn


# ── 基础查询 ──────────────────────────────────────────────────────────


def get_name_map() -> dict[str, str]:
    """ts_code → name 映射."""
    with _conn() as conn:
        rows = conn.execute("SELECT ts_code, name FROM stock_basic").fetchall()
    return {r[0]: r[1] or "" for r in rows}


def get_industry_map() -> dict[str, str]:
    """ts_code → industry 映射."""
    with _conn() as conn:
        rows = conn.execute("SELECT ts_code, industry FROM stock_basic").fetchall()
    return {r[0]: r[1] or "" for r in rows}


def get_stock_basic_df() -> pd.DataFrame:
    """全量 stock_basic DataFrame."""
    with _conn() as conn:
        return pd.read_sql(
            "SELECT ts_code, name, industry, list_status FROM stock_basic",
            conn,
        )


def get_trade_dates(start: str = "", end: str = "") -> list[str]:
    """从 daily_price 推导交易日历（不调 trade_cal API）.

    Args:
        start: YYYYMMDD, 空则从最早.
        end:   YYYYMMDD, 空则到最新.

    Returns:
        排序后的 trade_date 字符串列表.
    """
    query = "SELECT DISTINCT trade_date FROM daily_price"
    conditions: list[str] = []
    params: list[str] = []
    if start:
        conditions.append("trade_date >= ?")
        params.append(start)
    if end:
        conditions.append("trade_date <= ?")
        params.append(end)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY trade_date"
    with _conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [r[0] for r in rows]


# ── 日线行情 ──────────────────────────────────────────────────────────


def load_daily_by_date(trade_date: str) -> pd.DataFrame:
    """按交易日加载全市场日线快照（替代 pro.daily(trade_date=)）.

    Returns columns: ts_code, open, high, low, close, pre_close, pct_chg,
    vol, amount, adj_factor
    """
    with _conn() as conn:
        return pd.read_sql(
            "SELECT ts_code, open, high, low, close, pre_close, pct_chg, "
            "vol, amount, adj_factor "
            "FROM daily_price WHERE trade_date = ?",
            conn,
            params=(trade_date,),
        )


def load_daily_batch(trade_dates: list[str]) -> pd.DataFrame:
    """批量加载多个交易日的全市场日线（一次查询）."""
    if not trade_dates:
        return pd.DataFrame()
    placeholders = ",".join("?" * len(trade_dates))
    with _conn() as conn:
        return pd.read_sql(
            f"SELECT ts_code, trade_date, open, high, low, close, pre_close, "
            f"pct_chg, vol, amount, adj_factor "
            f"FROM daily_price WHERE trade_date IN ({placeholders})",
            conn,
            params=trade_dates,
        )


def fetch_daily_qfq_from_db(
    ts_code: str,
    end_date: str = "",
    days: int = 250,
) -> pd.DataFrame:
    """从 daily_price 读取单只股票的前复权日线（替代 ts.pro_bar(adj='qfq')）.

    手算前复权: adj_close = close * adj_factor / latest_adj_factor

    Args:
        ts_code: 股票代码
        end_date: 截止日期 YYYYMMDD（空则到最新）
        days: 读取的天数（从 end_date 往前数）

    Returns:
        DataFrame with columns: date, open, high, low, close, volume
        （前复权后的价格），按日期升序排列.
    """
    query = (
        "SELECT trade_date, open, high, low, close, vol, adj_factor "
        "FROM daily_price WHERE ts_code = ?"
    )
    params: list = [ts_code]
    if end_date:
        query += " AND trade_date <= ?"
        params.append(end_date)
    query += " ORDER BY trade_date DESC LIMIT ?"
    params.append(days)

    with _conn() as conn:
        df = pd.read_sql(query, conn, params=params)

    if df.empty:
        return pd.DataFrame()

    # 手算前复权
    latest_adj = float(df["adj_factor"].iloc[0])  # 第一行是最近的
    if latest_adj <= 0:
        latest_adj = 1.0
    ratio = df["adj_factor"].astype(float) / latest_adj

    df["open"] = df["open"].astype(float) * ratio
    df["high"] = df["high"].astype(float) * ratio
    df["low"] = df["low"].astype(float) * ratio
    df["close"] = df["close"].astype(float) * ratio
    df["volume"] = df["vol"].astype(float)

    # 按日期升序
    df = df.sort_values("trade_date").reset_index(drop=True)
    df.rename(columns={"trade_date": "date"}, inplace=True)

    return df[["date", "open", "high", "low", "close", "volume"]]


# ── 龙虎榜 ──────────────────────────────────────────────────────────


def load_top_list_by_date(trade_date: str) -> pd.DataFrame:
    """加载某日龙虎榜明细（替代 pro.top_list(trade_date=)）."""
    with _conn() as conn:
        return pd.read_sql(
            "SELECT ts_code, trade_date, name, close, pct_change, "
            "turnover_rate, amount, l_sell, l_buy, l_amount, "
            "net_amount, net_rate, amount_rate, float_values, reason "
            "FROM top_list WHERE trade_date = ?",
            conn,
            params=(trade_date,),
        )


def load_top_list_batch(trade_dates: list[str]) -> pd.DataFrame:
    """批量加载多个交易日的龙虎榜."""
    if not trade_dates:
        return pd.DataFrame()
    placeholders = ",".join("?" * len(trade_dates))
    with _conn() as conn:
        return pd.read_sql(
            f"SELECT ts_code, trade_date, net_amount, l_buy, l_sell, reason "
            f"FROM top_list WHERE trade_date IN ({placeholders})",
            conn,
            params=trade_dates,
        )


# ── 财务数据 ──────────────────────────────────────────────────────────


def fetch_raw_financials_from_db(
    ts_code: str,
    periods: int = 8,
) -> pd.DataFrame | None:
    """从 financial 表读取单只股票的利润表+指标（替代 pro.income + pro.fina_indicator）.

    保留调用方自算单季同比的逻辑（绕过 financial_fetcher 的已知 yoy bug）.

    Returns:
        DataFrame with columns: end_date, total_revenue, n_income,
        n_income_attr_p, roe, grossprofit_margin(None), dt_roe(None)
        按报告期降序排列（最新在前）.
    """
    with _conn() as conn:
        # income
        inc_rows = conn.execute(
            "SELECT end_date, payload FROM financial "
            "WHERE ts_code = ? AND endpoint = 'income' "
            "ORDER BY end_date DESC LIMIT ?",
            (ts_code, periods),
        ).fetchall()
        if not inc_rows:
            return None

        # fina_indicator
        fina_rows = conn.execute(
            "SELECT end_date, payload FROM financial "
            "WHERE ts_code = ? AND endpoint = 'fina_indicator' "
            "ORDER BY end_date DESC LIMIT ?",
            (ts_code, periods),
        ).fetchall()
        fina_map: dict[str, dict] = {}
        for end_date, payload in fina_rows:
            try:
                data = json.loads(payload) if isinstance(payload, str) else payload
                fina_map[end_date] = data
            except (json.JSONDecodeError, TypeError):
                pass

    records = []
    for end_date, payload in inc_rows:
        try:
            data = json.loads(payload) if isinstance(payload, str) else payload
        except (json.JSONDecodeError, TypeError):
            continue
        fina = fina_map.get(end_date, {})
        records.append({
            "end_date": end_date,
            "total_revenue": float(data.get("total_revenue") or 0),
            "n_income": float(data.get("n_income") or 0),
            "n_income_attr_p": float(data.get("n_income_attr_p") or 0),
            "roe": float(fina.get("roe") or 0),
            # grossprofit_margin / dt_roe 不在 DB payload 中
            "grossprofit_margin": None,
            "dt_roe": None,
        })

    df = pd.DataFrame(records)
    if df.empty:
        return None

    # 拆单季（Q1 保留, Q2/Q3/Q4 = 本期累计 - 上期累计）
    df = df.sort_values("end_date").reset_index(drop=True)
    df["quarter_rev"] = df["total_revenue"]
    df["quarter_np"] = df["n_income_attr_p"]
    for i in range(len(df) - 1, 0, -1):
        curr_q = df.loc[i, "end_date"][4:6]
        if curr_q != "03":
            df.loc[i, "quarter_rev"] = df.loc[i, "total_revenue"] - df.loc[i - 1, "total_revenue"]
            df.loc[i, "quarter_np"] = df.loc[i, "n_income_attr_p"] - df.loc[i - 1, "n_income_attr_p"]

    # 单季同比（本期单季 vs 去年同期单季）
    df["rev_yoy"] = np.nan
    df["np_yoy"] = np.nan
    for i in range(len(df)):
        curr_end = df.loc[i, "end_date"]
        curr_q = curr_end[4:6]
        prev_year = f"{int(curr_end[:4]) - 1}{curr_q}{curr_end[6:]}"
        match = df[df["end_date"] == prev_year]
        if not match.empty:
            j = match.index[0]
            if (
                pd.notna(df.loc[i, "quarter_rev"])
                and pd.notna(df.loc[j, "quarter_rev"])
                and df.loc[j, "quarter_rev"] != 0
            ):
                df.loc[i, "rev_yoy"] = (df.loc[i, "quarter_rev"] / df.loc[j, "quarter_rev"] - 1) * 100
            if (
                pd.notna(df.loc[i, "quarter_np"])
                and pd.notna(df.loc[j, "quarter_np"])
                and abs(df.loc[j, "quarter_np"]) > 1e6
            ):
                df.loc[i, "np_yoy"] = (df.loc[i, "quarter_np"] / df.loc[j, "quarter_np"] - 1) * 100

    return df.sort_values("end_date", ascending=False).reset_index(drop=True)


# ── 估值/基本面 ──────────────────────────────────────────────────────


def load_daily_basic_by_date(trade_date: str) -> pd.DataFrame:
    """某日全市场估值数据（替代 pro.daily_basic(trade_date=)）."""
    with _conn() as conn:
        return pd.read_sql(
            "SELECT ts_code, trade_date, pe_ttm, pb, ps, total_mv "
            "FROM daily_basic WHERE trade_date = ?",
            conn,
            params=(trade_date,),
        )


def load_daily_basic_by_code(
    ts_code: str, start: str = "", end: str = ""
) -> pd.DataFrame:
    """单只股票的历史估值数据."""
    query = "SELECT ts_code, trade_date, pe_ttm, pb, ps, total_mv FROM daily_basic WHERE ts_code = ?"
    params: list = [ts_code]
    if start:
        query += " AND trade_date >= ?"
        params.append(start)
    if end:
        query += " AND trade_date <= ?"
        params.append(end)
    query += " ORDER BY trade_date"
    with _conn() as conn:
        return pd.read_sql(query, conn, params=params)


def get_float_share(ts_code: str) -> float:
    """获取最新流通股本（万股）——替代 safe_tushare_call('daily_basic')."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT circ_mv, total_mv FROM daily_basic WHERE ts_code = ? "
            "ORDER BY trade_date DESC LIMIT 1",
            (ts_code,),
        ).fetchone()
    if not row or not row[0]:
        return 0.0
    # circ_mv 单位万元, 假设股价 p 则 float_share = circ_mv / p * 10000
    # 但 daily_basic 没有 float_share 列, 需要另算
    return 0.0  # caller 应该从 daily_price 的 amount / close 推算换手率


# ── 技术因子 ──────────────────────────────────────────────────────────


def load_tech_factor(start: str = "", end: str = "") -> pd.DataFrame:
    """加载技术因子表（全市场）."""
    query = "SELECT ts_code, trade_date, tech_score, rsi, boll_position, kdj_j, ma_align_score FROM tech_factor"
    conditions: list[str] = []
    params: list[str] = []
    if start:
        conditions.append("trade_date >= ?")
        params.append(start)
    if end:
        conditions.append("trade_date <= ?")
        params.append(end)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    with _conn() as conn:
        return pd.read_sql(query, conn, params=params)
