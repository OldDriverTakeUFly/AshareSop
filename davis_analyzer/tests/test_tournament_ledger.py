"""ledger OOS 台账测试。"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from davis_analyzer.tournament.ledger import (
    LedgerRecord,
    append_record,
    count_campaigns,
    detect_continual_tweaking,
    ensure_tables,
)


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    ensure_tables(conn)
    yield conn
    conn.close()


def _rec(run_date: date, params_version: str = "v1", op: str = "evolve") -> LedgerRecord:
    return LedgerRecord(
        op_type=op, run_date=run_date,
        participants=[("davis_balanced", "vabc")],
        params_version=params_version, oos_windows_used=3, detail={},
    )


def test_ensure_tables_idempotent(db) -> None:
    ensure_tables(db)  # second call no raise


def test_append_and_count_campaigns(db) -> None:
    append_record(db, _rec(date(2025, 1, 10)))
    append_record(db, _rec(date(2025, 6, 1)))
    append_record(db, _rec(date(2024, 3, 1)))
    assert count_campaigns(db, 2025) == 2


def test_continual_tweaking_detection(db) -> None:
    d0 = date(2025, 3, 1)
    for i in range(3):
        append_record(db, _rec(d0 + timedelta(days=i)))
    assert detect_continual_tweaking(db) is True
    db2 = db
    db2.execute("DELETE FROM tournament_ledger")
    append_record(db2, _rec(date(2025, 3, 1)))
    append_record(db2, _rec(date(2025, 3, 2)))
    assert detect_continual_tweaking(db2) is False  # 2 次 ≤ max_runs
