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
