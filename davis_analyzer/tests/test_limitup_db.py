"""db.py 归一化助手与读取函数测试。"""

from __future__ import annotations

import sqlite3

from davis_analyzer.limitup import db


def test_normalize_date_roundtrip() -> None:
    assert db.normalize_date("2026-05-12") == "20260512"
    assert db.normalize_date("20260512") == "20260512"
    assert db.to_dash_date("20260512") == "2026-05-12"


def test_to_suffixed_code() -> None:
    assert db.to_suffixed_code("603311") == "603311.SH"
    assert db.to_suffixed_code("000631") == "000631.SZ"
    assert db.to_suffixed_code("300750") == "300750.SZ"
    assert db.to_suffixed_code("688981") == "688981.SH"
    assert db.to_suffixed_code("603311.SH") == "603311.SH"
    assert db.strip_code_suffix("603311.SH") == "603311"


def test_read_limit_pool_normalizes(limitup_db: sqlite3.Connection) -> None:
    limitup_db.execute(
        "INSERT INTO limit_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-05-12", "603311", "limit_up", "金海高科", "家电", 10.0,
         5e7, 2, 0, "093000", "145500", 12.5, None),
    )
    limitup_db.commit()
    df = db.read_limit_pool(limitup_db, "20260501", "20260531")
    assert len(df) == 1
    assert df.iloc[0]["trade_date"] == "20260512"
    assert df.iloc[0]["ts_code"] == "603311.SH"
    assert df.iloc[0]["consecutive_boards"] == 2


def test_trading_dates_sorted_unique(limitup_db: sqlite3.Connection) -> None:
    for d in ("20260512", "20260513", "20260511"):
        limitup_db.execute(
            "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("600519.SH", d, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, None),
        )
    limitup_db.commit()
    assert db.trading_dates(limitup_db, "20260501", "20260531") == [
        "20260511", "20260512", "20260513",
    ]


def test_read_limit_pool_pads_seal_time(limitup_db: sqlite3.Connection) -> None:
    """Tushare 存 '92500'（09:25:00 去前导零），读取必须归一为 6 位防误判档位."""
    limitup_db.execute(
        "INSERT INTO limit_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2024-01-02", "600001", "limit_up", "甲", "X", 10.0, 1e8, 1, 0,
         "92500", "112945", 5.0, None),
    )
    limitup_db.commit()
    df = db.read_limit_pool(limitup_db, "20240101", "20240110")
    assert df.iloc[0]["first_seal_time"] == "092500"
    assert df.iloc[0]["last_seal_time"] == "112945"


def test_read_limit_pool_dedupes_suffix_variants(limitup_db: sqlite3.Connection) -> None:
    """stockhot 日扫写带后缀、本模块回补写无后缀 → 同股双记，读取层必须去重."""
    limitup_db.execute(
        "INSERT INTO limit_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-08-18", "000007", "limit_up", "甲", "X", 10.0, 1e8, 1, 0,
         "130045", "130045", 3.29, None),
    )
    limitup_db.execute(
        "INSERT INTO limit_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-08-18", "000007.SZ", "limit_up", "甲", "X", 10.0, 1e8, 1, 0,
         "130045", "130045", 3.29, None),
    )
    limitup_db.commit()
    df = db.read_limit_pool(limitup_db, "20260801", "20260831")
    assert len(df) == 1
    assert df.iloc[0]["ts_code"] == "000007.SZ"
