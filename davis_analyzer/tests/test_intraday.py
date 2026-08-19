"""intraday 研究沙盒的离线单元测试（无网络、无 baostock login）。"""

from __future__ import annotations

import sqlite3

import pandas as pd

from davis_analyzer.intraday import backfill, db


# ── 代码转换 ──

def test_to_bs_code():
    assert backfill.to_bs_code("600050.SH") == "sh.600050"
    assert backfill.to_bs_code("002342.SZ") == "sz.002342"
    assert backfill.to_bs_code("000001.SH") == "sh.000001"  # 上证指数


# ── 月块切分 ──

def test_month_chunks_boundaries():
    # 起止均落在月中：首尾月部分覆盖，中间整月
    chunks = backfill.month_chunks("20250819", "20260705")
    assert chunks[0] == ("202508", "20250819", "20250831")
    assert chunks[1] == ("202509", "20250901", "20250930")
    assert chunks[-1] == ("202607", "20260701", "20260705")
    assert len(chunks) == 12

    # 同月内窗口
    assert backfill.month_chunks("20260701", "20260731") == [("202607", "20260701", "20260731")]
    # 跨年
    chunks = backfill.month_chunks("20251201", "20260131")
    assert [c[0] for c in chunks] == ["202512", "202601"]


def test_default_start():
    assert backfill.default_start(12, "20260819") == "20250801"
    assert backfill.default_start(1, "20260115") == "20251201"


# ── baostock 原始行解析 ──

def _raw_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["2026-07-01", "20260701093500000", "sh.600050", "5.10", "5.12", "5.08", "5.11", "1200000", "6132000"],
            ["2026-07-01", "20260701094000000", "sh.600050", "5.11", "5.15", "5.11", "5.14", "800000", ""],
        ],
        columns=["date", "time", "code", "open", "high", "low", "close", "volume", "amount"],
    )


def test_parse_baostock_frame():
    out = backfill.parse_baostock_frame(_raw_frame(), "600050.SH", "5min")
    assert len(out) == 2
    assert out.iloc[0]["trade_date"] == "20260701"
    assert out.iloc[0]["trade_time"] == "09:35"
    assert out.iloc[1]["trade_time"] == "09:40"
    assert (out["source"] == "baostock").all()
    # 空 amount 解析为 NaN 而非报错
    assert pd.isna(out.iloc[1]["amount"])
    assert out.iloc[0]["amount"] == 6132000


def test_parse_empty_frame():
    out = backfill.parse_baostock_frame(pd.DataFrame(), "600050.SH", "5min")
    assert out.empty


# ── 数据层：建表 / 幂等写入 / 进度台账 ──

def test_db_roundtrip_and_chunk_ledger(tmp_path):
    conn = db.connect(tmp_path / "research.db")
    try:
        bars = backfill.parse_baostock_frame(_raw_frame(), "600050.SH", "5min")
        assert db.upsert_bars(conn, bars) == 2
        # 幂等：重复写入不膨胀
        assert db.upsert_bars(conn, bars) == 2
        n = conn.execute("SELECT COUNT(*) FROM minute_bar").fetchone()[0]
        assert n == 2

        db.mark_chunk_done(conn, "600050.SH", "5min", "202607", "20260701", "20260731", 2)
        assert ("600050.SH", "202607") in db.finished_chunks(conn, "5min")
        assert ("600050.SH", "202608") not in db.finished_chunks(conn, "5min")

        got = db.read_bars(conn, ["600050.SH"], "20260701", "20260731")
        assert len(got) == 2
        assert list(got["trade_time"]) == ["09:35", "09:40"]

        summary = db.coverage_summary(conn)
        assert len(summary) == 1
        assert summary.iloc[0]["months_done"] == 1
        assert summary.iloc[0]["minute_rows"] == 2
    finally:
        conn.close()


def test_connect_ensures_schema_on_existing_db(tmp_path):
    path = tmp_path / "r.db"
    db.connect(path).close()
    # 裸 sqlite 直连应能看到两张表
    raw = sqlite3.connect(path)
    tables = {r[0] for r in raw.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    raw.close()
    assert {"minute_bar", "backfill_chunk"} <= tables
