# davis_analyzer/tests/test_metrics.py
"""xhs_metrics 台账单元测试:幂等写入/快照覆盖/manual 优先/聚合报告/读数清洗。"""
from __future__ import annotations

import sqlite3

import pytest

from davis_analyzer.metrics import db as mdb
from davis_analyzer.metrics.collector import _to_int
from davis_analyzer.metrics.report import report


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(mdb, "DB_PATH", tmp_path / "xhs_metrics.db")
    return mdb.connect()


def test_init_and_upsert_note_idempotent(conn):
    mdb.init_db("acc1", name="测试号")
    n1 = mdb.upsert_note(conn, "acc1", "笔记A", grp="工具方法", published_at="2026-08-29")
    n2 = mdb.upsert_note(conn, "acc1", "笔记A", grp="工具方法", published_at="2026-08-29")
    assert n1 == n2  # 幂等
    n3 = mdb.upsert_note(conn, "acc1", "笔记B")
    assert n3 != n1


def test_upsert_note_fills_blank_fields_only(conn):
    mdb.init_db("acc1")
    nid = mdb.upsert_note(conn, "acc1", "笔记A", grp="")
    mdb.upsert_note(conn, "acc1", "笔记A", grp="产业链调研")  # 后补组
    row = conn.execute("SELECT grp FROM notes WHERE note_id=?", (nid,)).fetchone()
    assert row["grp"] == "产业链调研"
    mdb.upsert_note(conn, "acc1", "笔记A", grp="")  # 空值不覆盖
    row = conn.execute("SELECT grp FROM notes WHERE note_id=?", (nid,)).fetchone()
    assert row["grp"] == "产业链调研"


def test_snapshot_upsert_and_manual_override(conn):
    mdb.init_db("acc1")
    nid = mdb.upsert_note(conn, "acc1", "笔记A")
    mdb.record_note_metrics(conn, nid, "2026-08-30T21:00:00", views=100, likes=2, source="vision")
    mdb.record_note_metrics(conn, nid, "2026-08-30T21:00:00", views=105, likes=3, source="manual")
    rows = conn.execute("SELECT * FROM note_metrics WHERE note_id=?", (nid,)).fetchall()
    assert len(rows) == 1 and rows[0]["views"] == 105 and rows[0]["source"] == "manual"
    # 次日新快照共存
    mdb.record_note_metrics(conn, nid, "2026-08-31T21:00:00", views=200, source="vision")
    assert conn.execute("SELECT COUNT(*) FROM note_metrics WHERE note_id=?", (nid,)).fetchone()[0] == 2


def test_report_grouping(conn, capsys):
    mdb.init_db("acc1")
    for title, grp, views in [("卡1", "工具方法", 50), ("卡2", "工具方法", 150), ("卡3", "产业链调研", 300)]:
        nid = mdb.upsert_note(conn, "acc1", title, grp=grp)
        mdb.record_note_metrics(conn, nid, "2026-08-30T21:00:00", views=views, likes=1, collects=2)
    conn.commit()  # report 会开新连接,先提交本连接事务
    out = report("acc1")
    assert "工具方法" in out and "产业链调研" in out
    assert "| 2 | 100 | 200 |" in out  # 中位100 合计200


def test_to_int_cleans_vision_reads():
    assert _to_int("1.2万") == 12000
    assert _to_int("34") == 34
    assert _to_int(7) == 7
    assert _to_int(None) is None
    assert _to_int("") is None
    assert _to_int("环比-") is None
    assert _to_int("-5") is None  # 负数视为脏值
    assert _to_int("abc") is None
