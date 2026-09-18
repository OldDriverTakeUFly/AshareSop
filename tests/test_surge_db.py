"""surge.db 表管理与命中池读取测试（内存库，不触真库）."""

from __future__ import annotations

import sqlite3

import pytest

from davis_analyzer.surge import db


@pytest.fixture()
def mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE daily_price (ts_code TEXT, trade_date TEXT, open REAL, high REAL,"
        " low REAL, close REAL, pre_close REAL, pct_chg REAL, vol REAL, amount REAL,"
        " adj_factor REAL, PRIMARY KEY(ts_code, trade_date))"
    )
    conn.execute(
        "CREATE TABLE stock_basic (ts_code TEXT PRIMARY KEY, name TEXT, industry TEXT,"
        " market TEXT, list_date TEXT)"
    )
    db.ensure_tables(conn)
    yield conn
    conn.close()


def test_ensure_tables_idempotent(mem_conn):
    db.ensure_tables(mem_conn)  # 二次执行不抛错
    names = {r[0] for r in mem_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"cyq_perf_cache", "surge_snapshot", "major_events",
            "cninfo_announcement", "cninfo_org_map",
            "surge_pattern_hits", "surge_tags"} <= names


def test_read_pool_filters_pct(mem_conn):
    mem_conn.execute(
        "INSERT INTO stock_basic VALUES ('000001.SZ','平安X','银行','主板','19910403')")
    for code, pct in [("000001.SZ", 7.5), ("000002.SZ", 6.9), ("000003.SZ", 20.1)]:
        mem_conn.execute(
            "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (code, "20260918", 10, 11, 9, 10.7, 10, pct, 1000, 10700, 1.0))
    pool = db.read_pool(mem_conn, "20260918")
    assert set(pool["ts_code"]) == {"000001.SZ", "000003.SZ"}
    assert pool.loc[pool.ts_code == "000001.SZ", "name"].iloc[0] == "平安X"
    # 7.0 整不入选（严格大于）
    mem_conn.execute(
        "INSERT OR REPLACE INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("000004.SZ", "20260918", 10, 11, 9, 10.7, 10, 7.0, 1000, 10700, 1.0))
    assert "000004.SZ" not in set(db.read_pool(mem_conn, "20260918")["ts_code"])


def test_read_pool_marks_st(mem_conn):
    mem_conn.execute(
        "INSERT INTO stock_basic VALUES ('000005.SZ','ST测试','银行','主板','20200101')")
    mem_conn.execute(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("000005.SZ", "20260918", 10, 11, 9, 10.7, 10, 7.5, 1000, 10700, 1.0))
    pool = db.read_pool(mem_conn, "20260918")
    assert int(pool["is_st"].iloc[0]) == 1


def test_date_code_normalizers():
    assert db.normalize_date("2026-09-18") == "20260918"
    assert db.to_dash_date("20260918") == "2026-09-18"
    assert db.to_suffixed_code("000001") == "000001.SZ"
    assert db.to_suffixed_code("600519") == "600519.SH"
    assert db.to_suffixed_code("920298") == "920298.BJ"
    assert db.to_suffixed_code("000001.SZ") == "000001.SZ"
    assert db.strip_code_suffix("000001.SZ") == "000001"
