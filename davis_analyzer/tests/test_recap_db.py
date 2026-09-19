# davis_analyzer/tests/test_recap_db.py
"""recap 台账:建表/保存/状态机/读取。"""
from __future__ import annotations

import sqlite3

from davis_analyzer.systems.recap import db


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    db.ensure_tables(conn)
    return conn


def test_ensure_tables_idempotent():
    conn = sqlite3.connect(":memory:")
    db.ensure_tables(conn)
    db.ensure_tables(conn)  # 不抛异常
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='recap_episodes'"
    ).fetchone()
    assert row is not None


def test_save_and_get_roundtrip():
    conn = _conn()
    meta = {"trade_date": "2026-09-18", "status": "selected",
            "candidates_json": '[{"ts_code": "605577.SH"}]',
            "episode_json": None, "facts_json": None}
    db.save_episode(conn, meta)
    got = db.get_episode(conn, "2026-09-18")
    assert got["status"] == "selected"
    # 简报原文断言 got["candidates_json"],但 get_episode 返回解析后的
    # candidates/episode/facts 键(Task 2 的 _do_select 与 CLI status 均按此消费)——
    # 修正为解析形态断言(偏离说明见 task-1-report.md)
    assert got["candidates"][0]["ts_code"] == "605577.SH"


def test_update_status():
    conn = _conn()
    db.save_episode(conn, {"trade_date": "2026-09-18", "status": "selected"})
    db.update_status(conn, "2026-09-18", "scripted")
    assert db.get_episode(conn, "2026-09-18")["status"] == "scripted"


def test_update_status_rejects_unknown():
    conn = _conn()
    db.save_episode(conn, {"trade_date": "2026-09-18", "status": "selected"})
    try:
        db.update_status(conn, "2026-09-18", "nope")
        raise AssertionError("应拒绝未知状态")
    except ValueError:
        pass
