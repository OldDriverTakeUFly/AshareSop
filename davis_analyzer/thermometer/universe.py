"""Universe management: 申万 L1/L2 池与成分、同花顺概念层(仅建数据)."""

from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING

import pandas as pd
from loguru import logger

if TYPE_CHECKING:
    from stockhot.data_layer.tushare_gateway import TushareGateway


# ── 刷新(调用方传 conn,测试可注入临时库) ────────────────────────────────

def refresh_sw_index(conn: sqlite3.Connection, gw: TushareGateway) -> int:
    """index_classify L1+L2(SW2021)全量覆写 sw_index,返回行数."""
    frames = [gw.call("index_classify", level=lv, src="SW2021") for lv in ("L1", "L2")]
    df = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    if df.empty:
        logger.warning("index_classify L1+L2 均为空,sw_index 未更新")
        return 0
    conn.execute("DELETE FROM sw_index")
    conn.executemany(
        "INSERT INTO sw_index (index_code,name,level,parent_code,src,is_pub,fetched_at) "
        "VALUES (?,?,?,?,?,?,?)",
        [(r["index_code"], r["industry_name"], r["level"], r.get("parent_code"),
          "SW2021", r.get("is_pub"), time.time()) for _, r in df.iterrows()],
    )
    conn.commit()
    logger.info("sw_index 刷新: {} 行", len(df))
    return len(df)


def refresh_sw_member(conn: sqlite3.Connection, gw: TushareGateway,
                      snapshot_date: str) -> int:
    """逐指数拉 index_member 写 sw_member 快照;同快照日重跑先清后写(幂等)."""
    codes = [r[0] for r in conn.execute("SELECT index_code FROM sw_index").fetchall()]
    conn.execute("DELETE FROM sw_member WHERE snapshot_date=?", (snapshot_date,))
    total = 0
    for code in codes:
        df = gw.call("index_member", index_code=code)
        conn.executemany(
            "INSERT OR REPLACE INTO sw_member "
            "(index_code,con_code,in_date,out_date,is_new,snapshot_date) "
            "VALUES (?,?,?,?,?,?)",
            [(code, r["con_code"], r.get("in_date"), r.get("out_date"),
              r.get("is_new"), snapshot_date) for _, r in df.iterrows()],
        )
        total += len(df)
    conn.commit()
    logger.info("sw_member 快照 {}: {} 指数 {} 行", snapshot_date, len(codes), total)
    return total


def refresh_ths_index(conn: sqlite3.Connection, gw: TushareGateway) -> int:
    """ths_index 概念列表全量覆写(概念层只建数据)."""
    df = gw.call("ths_index", exchange="A", type="N")
    conn.execute("DELETE FROM ths_index")
    conn.executemany(
        "INSERT INTO ths_index (ts_code,name,count,exchange,list_date,type,fetched_at) "
        "VALUES (?,?,?,?,?,?,?)",
        [(r["ts_code"], r["name"], r.get("count"), r.get("exchange"),
          r.get("list_date"), r.get("type"), time.time()) for _, r in df.iterrows()],
    )
    conn.commit()
    logger.info("ths_index 刷新: {} 行", len(df))
    return len(df)


def refresh_ths_member(conn: sqlite3.Connection, gw: TushareGateway,
                       snapshot_date: str) -> int:
    """逐概念拉 ths_member 写快照(约 395 次调用,周频足够)."""
    codes = [r[0] for r in conn.execute("SELECT ts_code FROM ths_index").fetchall()]
    conn.execute("DELETE FROM ths_member WHERE snapshot_date=?", (snapshot_date,))
    total = 0
    for code in codes:
        df = gw.call("ths_member", ts_code=code)
        conn.executemany(
            "INSERT OR REPLACE INTO ths_member (ts_code,con_code,con_name,snapshot_date) "
            "VALUES (?,?,?,?)",
            [(code, r["con_code"], r.get("con_name"), snapshot_date)
             for _, r in df.iterrows()],
        )
        total += len(df)
    conn.commit()
    logger.info("ths_member 快照 {}: {} 概念 {} 行", snapshot_date, len(codes), total)
    return total


# ── 读取 ───────────────────────────────────────────────────────────────

def load_universe(conn: sqlite3.Connection, level: str) -> pd.DataFrame:
    """[index_code, name, level] — 某层级指数清单."""
    return pd.read_sql_query(
        "SELECT index_code, name, level FROM sw_index WHERE level=?", conn, params=(level,))


def load_members(conn: sqlite3.Connection, levels: tuple[str, ...] = ("L1", "L2")) -> pd.DataFrame:
    """最新快照的 (level, index_code, con_code, snapshot_date) 长表.

    最新快照 = 每个 index_code 的 MAX(snapshot_date);同一 con_code 会出现在
    L1 与其所属 L2 两行,聚合时天然双层级各自成立。
    """
    ph = ",".join("?" * len(levels))
    return pd.read_sql_query(
        "SELECT i.level, m.index_code, m.con_code, m.snapshot_date "
        "FROM sw_member m JOIN sw_index i ON i.index_code = m.index_code "
        "JOIN (SELECT index_code, MAX(snapshot_date) AS ms FROM sw_member "
        "      GROUP BY index_code) t "
        "  ON t.index_code = m.index_code AND t.ms = m.snapshot_date "
        f"WHERE i.level IN ({ph})",
        conn, params=levels,
    )
