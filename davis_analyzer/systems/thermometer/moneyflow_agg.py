"""moneyflow 个股明细 → 板块主力资金流日频(Decimal 求和).

主力口径 = 超大单净额 + 大单净额(buy_elg−sell_elg + buy_lg−sell_lg),单位万元。
分母 = 成分股当日 circ_mv 之和(daily_basic,万元);分子分母同源同时点。
"""

from __future__ import annotations

import sqlite3
import time
from decimal import Decimal

import pandas as pd
from loguru import logger

from davis_analyzer.systems.thermometer.universe import load_members


def _dec(v: object) -> Decimal:
    """None/NaN 安全转 Decimal(str 中转避免二进制尾差)."""
    if v is None:
        return Decimal(0)
    try:
        if pd.isna(v):
            return Decimal(0)
    except TypeError:
        pass
    return Decimal(str(v))


def aggregate_sector_moneyflow(conn: sqlite3.Connection, start: str, end: str) -> dict:
    """聚合 [start,end] 全部交易日;已有行覆写,幂等.

    一次拉窗口内全部 moneyflow/daily_basic 明细,Python 侧 Decimal 累加
    (金额铁律:不用 float 累加)。全窗口回补约 540 万行,一次约 1-2 分钟。
    """
    members = load_members(conn)
    if members.empty:
        raise RuntimeError("sw_member 为空,先 refresh universe")
    code_to_idx: dict[str, list[tuple[str, str]]] = {}
    for _, m in members.iterrows():
        code_to_idx.setdefault(m["con_code"], []).append((m["level"], m["index_code"]))

    mf = pd.read_sql_query(
        "SELECT trade_date, ts_code, buy_elg_amount, sell_elg_amount, "
        "buy_lg_amount, sell_lg_amount FROM moneyflow "
        "WHERE trade_date>=? AND trade_date<=?",
        conn, params=(start, end),
    )
    cap = pd.read_sql_query(
        "SELECT trade_date, ts_code, circ_mv FROM daily_basic "
        "WHERE trade_date>=? AND trade_date<=? AND circ_mv IS NOT NULL",
        conn, params=(start, end),
    )
    cap_map = {(r.ts_code, r.trade_date): _dec(r.circ_mv) for r in cap.itertuples()}

    # (level,index_code,trade_date) → Decimal 累加器
    main_acc: dict[tuple[str, str, str], Decimal] = {}
    huge_acc: dict[tuple[str, str, str], Decimal] = {}
    big_acc: dict[tuple[str, str, str], Decimal] = {}
    cap_acc: dict[tuple[str, str, str], Decimal] = {}

    for r in mf.itertuples():
        targets = code_to_idx.get(r.ts_code)
        if not targets:
            continue
        huge = _dec(r.buy_elg_amount) - _dec(r.sell_elg_amount)
        big = _dec(r.buy_lg_amount) - _dec(r.sell_lg_amount)
        net = huge + big
        cv = cap_map.get((r.ts_code, r.trade_date))
        for lv, idx in targets:
            key = (lv, idx, r.trade_date)
            main_acc[key] = main_acc.get(key, Decimal(0)) + net
            huge_acc[key] = huge_acc.get(key, Decimal(0)) + huge
            big_acc[key] = big_acc.get(key, Decimal(0)) + big
            if cv is not None:
                cap_acc[key] = cap_acc.get(key, Decimal(0)) + cv

    now = time.time()
    conn.executemany(
        "INSERT OR REPLACE INTO sector_moneyflow_daily "
        "(level,index_code,trade_date,main_net,huge_net,big_net,mkt_cap,main_net_pct,fetched_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (lv, idx, d, float(main_acc[k]), float(huge_acc[k]), float(big_acc[k]),
             float(cap_acc[k]) if k in cap_acc else None,
             float(main_acc[k] / cap_acc[k]) if k in cap_acc and cap_acc[k] else None,
             now)
            for k, (lv, idx, d) in ((k, k) for k in main_acc)
        ],
    )
    conn.commit()
    days = len({k[2] for k in main_acc})
    logger.info("sector_moneyflow 聚合: {} 行 / {} 日 [{},{}]",
                len(main_acc), days, start, end)
    return {"rows": len(main_acc), "days": days}


def market_flow_series(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """全市场(不分板块)主力净额与流通市值日和,大盘资金维用(Decimal 累加).

    注意:直接扫 moneyflow+daily_basic——daily_basic 是 30 天滚动缓存
    (cleanup_expired_cache 会删历史行),此函数仅适用于近期窗口或测试;
    历史口径请用 market_flow_from_sectors(读已沉淀的板块聚合)。
    """
    mf = pd.read_sql_query(
        "SELECT trade_date, ts_code, buy_elg_amount, sell_elg_amount, "
        "buy_lg_amount, sell_lg_amount FROM moneyflow "
        "WHERE trade_date>=? AND trade_date<=?",
        conn, params=(start, end),
    )
    cap = pd.read_sql_query(
        "SELECT trade_date, SUM(circ_mv) AS cap_sum FROM daily_basic "
        "WHERE trade_date>=? AND trade_date<=? AND circ_mv IS NOT NULL GROUP BY trade_date",
        conn, params=(start, end),
    )
    cap_map = dict(zip(cap["trade_date"], cap["cap_sum"]))
    acc: dict[str, Decimal] = {}
    for r in mf.itertuples():
        net = (_dec(r.buy_elg_amount) - _dec(r.sell_elg_amount)
               + _dec(r.buy_lg_amount) - _dec(r.sell_lg_amount))
        acc[r.trade_date] = acc.get(r.trade_date, Decimal(0)) + net
    return pd.DataFrame({
        "trade_date": sorted(acc),
        "main_net_sum": [float(acc[d]) for d in sorted(acc)],
        "circ_mv_sum": [float(cap_map.get(d) or 0.0) for d in sorted(acc)],
    })


def market_flow_from_sectors(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """大盘资金序列的历史口径:L1 板块聚合求和(成分互斥,无重复计).

    sector_moneyflow_daily.mkt_cap 是聚合时点沉淀的分母,不受 daily_basic
    30 天滚动清理影响——大盘资金维的历史计算必须走这里。
    """
    return pd.read_sql_query(
        "SELECT trade_date, SUM(main_net) AS main_net_sum, SUM(mkt_cap) AS circ_mv_sum "
        "FROM sector_moneyflow_daily WHERE level='L1' "
        "AND trade_date>=? AND trade_date<=? AND mkt_cap IS NOT NULL "
        "GROUP BY trade_date ORDER BY trade_date",
        conn, params=(start, end),
    )
