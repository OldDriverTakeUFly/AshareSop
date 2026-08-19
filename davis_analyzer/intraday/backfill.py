"""baostock 分钟线回补管道（研究沙盒数据源）.

- 频率默认 5min（做T信号研究粒度足够；baostock 历史自 2011 年起）
- adjustflag='3' 不复权——与生产库 daily_price 未复权口径一致，便于对账
- 按 (ts_code, 自然月) 分块拉取，整月写完才记 backfill_chunk → 断点续跑
- baostock 无硬性频次限制，仍按 --sleep 间隔礼貌限速
"""

from __future__ import annotations

import time
from datetime import datetime

import pandas as pd
from loguru import logger

from davis_analyzer.intraday import db

_DEFAULT_FREQ_LABEL = "5min"
_DEFAULT_FREQ_PARAM = "5"
_FIELDS = "date,time,code,open,high,low,close,volume,amount"

# ── 代码与日历 ──

def to_bs_code(ts_code: str) -> str:
    """'600050.SH' -> 'sh.600050'（上证指数 '000001.SH' -> 'sh.000001' 同规则）."""
    code, _, suffix = ts_code.partition(".")
    return f"{suffix.lower()}.{code}"


def paper_universe() -> list[str]:
    """全部模拟盘持仓去重 + 上证指数（日内市场状态基准）。"""
    from stockhot.core.config import DB_PATH
    import sqlite3

    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT DISTINCT ts_code FROM paper_positions WHERE shares>0 "
            "ORDER BY ts_code"
        ).fetchall()
    finally:
        con.close()
    codes = [r[0] for r in rows]
    if "000001.SH" not in codes:
        codes.append("000001.SH")
    return codes


def month_chunks(start_date: str, end_date: str) -> list[tuple[str, str, str]]:
    """切分日期窗为 (month, chunk_start, chunk_end)，YYYYMMDD 口径。"""
    s = datetime.strptime(start_date, "%Y%m%d")
    e = datetime.strptime(end_date, "%Y%m%d")
    chunks: list[tuple[str, str, str]] = []
    cur = datetime(s.year, s.month, 1)
    while cur <= e:
        month = cur.strftime("%Y%m")
        if cur.month == 12:
            nxt = datetime(cur.year + 1, 1, 1)
        else:
            nxt = datetime(cur.year, cur.month + 1, 1)
        c_start = max(s, cur).strftime("%Y%m%d")
        c_end = min(e, datetime(nxt.year, nxt.month, 1) - pd.Timedelta(days=1))
        chunks.append((month, c_start, c_end.strftime("%Y%m%d")))
        cur = nxt
    return chunks


# ── baostock 拉取与解析 ──

def parse_baostock_frame(df: pd.DataFrame, ts_code: str, freq_label: str) -> pd.DataFrame:
    """纯函数：baostock 原始行 -> minute_bar 口径 DataFrame（可离线单测）。

    time 形如 '20260819093500000'（毫秒补零）；停牌/无额分钟 volume 为空串。
    """
    if df.empty:
        return pd.DataFrame(columns=[
            "ts_code", "freq", "trade_date", "trade_time",
            "open", "high", "low", "close", "volume", "amount", "source",
        ])
    out = pd.DataFrame({
        "ts_code": ts_code,
        "freq": freq_label,
        "trade_date": df["date"].str.replace("-", "", regex=False),
        "trade_time": df["time"].str[8:10] + ":" + df["time"].str[10:12],
    })
    for col in ("open", "high", "low", "close", "volume", "amount"):
        out[col] = pd.to_numeric(df[col], errors="coerce")
    out["source"] = "baostock"
    out = out.dropna(subset=["trade_date", "trade_time"])
    return out


def fetch_range(bs_mod, bs_code: str, start: str, end: str,
                freq_param: str = _DEFAULT_FREQ_PARAM) -> pd.DataFrame:
    """拉取一只票一个日期窗的分钟线（start/end 为 YYYY-MM-DD）。"""
    rs = bs_mod.query_history_k_data_plus(
        bs_code, _FIELDS,
        start_date=f"{start[:4]}-{start[4:6]}-{start[6:8]}",
        end_date=f"{end[:4]}-{end[4:6]}-{end[6:8]}",
        frequency=freq_param, adjustflag="3",
    )
    rows: list[list[str]] = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if rs.error_code != "0":
        raise RuntimeError(f"baostock {bs_code} {start}-{end}: {rs.error_msg}")
    return pd.DataFrame(rows, columns=rs.fields)


# ── 主流程 ──

def backfill(
    codes: list[str],
    start_date: str,
    end_date: str,
    db_path: str | None = None,
    freq_label: str = _DEFAULT_FREQ_LABEL,
    freq_param: str = _DEFAULT_FREQ_PARAM,
    sleep_sec: float = 0.3,
) -> dict:
    """按票×月回补分钟线（断点续跑）。返回统计摘要。"""
    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login 失败: {lg.error_msg}")

    conn = db.connect(db_path)
    done = db.finished_chunks(conn, freq_label)
    chunks = month_chunks(start_date, end_date)
    stats = {"codes": len(codes), "chunks_total": len(codes) * len(chunks),
             "chunks_done": 0, "chunks_skipped": 0, "rows_written": 0,
             "failures": []}
    try:
        for i, ts_code in enumerate(codes, 1):
            bs_code = to_bs_code(ts_code)
            for month, c_start, c_end in chunks:
                if (ts_code, month) in done:
                    stats["chunks_skipped"] += 1
                    continue
                try:
                    raw = fetch_range(bs, bs_code, c_start, c_end, freq_param)
                    bars = parse_baostock_frame(raw, ts_code, freq_label)
                    db.upsert_bars(conn, bars)
                    db.mark_chunk_done(
                        conn, ts_code, freq_label, month, c_start, c_end, len(bars)
                    )
                    stats["chunks_done"] += 1
                    stats["rows_written"] += len(bars)
                except Exception as exc:
                    logger.warning("回补失败 {} {} ({})", ts_code, month, exc)
                    stats["failures"].append(f"{ts_code}:{month}")
                time.sleep(sleep_sec)
            logger.info(
                "[{}/{}] {} 完成：累计 {} 行, 跳过 {} 块",
                i, len(codes), ts_code, stats["rows_written"],
                stats["chunks_skipped"],
            )
    finally:
        bs.logout()
        conn.close()
    return stats


def default_start(months: int, end_date: str) -> str:
    """end_date 往前推 N 个自然月的月初（YYYYMMDD）。"""
    e = datetime.strptime(end_date, "%Y%m%d")
    m = e.month - months
    y = e.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}{m:02d}01"
