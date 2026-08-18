"""paper_push 双臂日报测试（in-memory paper_* 表 + mock notifier）."""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

from davis_analyzer.limitup import paper_push


@pytest.fixture
def paper_db(monkeypatch, tmp_path):
    # 共享缓存内存库：每次 _connect() 开新连接（对齐生产语义），锚连接保活
    anchor = sqlite3.connect("file:paper_push_test?mode=memory&cache=shared", uri=True)
    anchor.row_factory = sqlite3.Row
    anchor.executescript("""
        CREATE TABLE paper_accounts (
            id INTEGER PRIMARY KEY, name TEXT, strategy_name TEXT,
            initial_capital REAL, cash REAL, status TEXT, config_json TEXT,
            created_at TEXT, updated_at TEXT);
        CREATE TABLE paper_trades (
            id INTEGER PRIMARY KEY, account_id INTEGER, trade_date TEXT,
            ts_code TEXT, name TEXT, action TEXT, shares INTEGER, price REAL,
            amount REAL, cost REAL, signal_reason TEXT, created_at TEXT);
        CREATE TABLE paper_nav_history (
            id INTEGER PRIMARY KEY, account_id INTEGER, trade_date TEXT,
            cash REAL, positions_value REAL, total_equity REAL, daily_return REAL);
        CREATE TABLE paper_positions (
            account_id INTEGER, ts_code TEXT, name TEXT, shares INTEGER,
            avg_cost REAL, entry_date TEXT, signal_reason TEXT);
    """)
    anchor.execute(
        "INSERT INTO paper_accounts VALUES (1,'fb_base','board_chasing',"
        "1000000.0,1000000.0,'active','{}','t','t')")
    anchor.execute(
        "INSERT INTO paper_accounts VALUES (2,'fb_enhanced','board_chasing_enhanced',"
        "1000000.0,1000000.0,'active','{}','t','t')")
    anchor.execute(
        "INSERT INTO paper_nav_history VALUES (1,1,'20260813',0,1000253,"
        "1000253.0,0.0003)")
    anchor.execute(
        "INSERT INTO paper_nav_history VALUES (2,2,'20260812',1000000,0,"
        "1000000.0,0.0)")
    anchor.execute(
        "INSERT INTO paper_trades VALUES (1,1,'20260813','600572.SH','康恩贝',"
        "'SELL',14000,4.775,66853,12,'T+1开盘卖','t')")
    anchor.commit()

    def _new_conn():
        c = sqlite3.connect("file:paper_push_test?mode=memory&cache=shared", uri=True)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(paper_push, "_connect", _new_conn)
    yield anchor
    anchor.close()


def test_build_arms_summary(paper_db) -> None:
    text = paper_push.build_arms_summary("20260813")
    assert "[打板双臂日报] 20260813" in text
    assert "fb_base(基准)" in text and "fb_enhanced(增强)" in text
    assert "NAV 1,000,253" in text
    assert "卖康恩贝 14000@4.775" in text
    assert "截至 20260813" in text  # fb_base 取 ≤day 的最新
    assert "截至 20260812" in text  # fb_enhanced 无 0813 记录取 0812


def test_push_idempotent_and_marker(paper_db, monkeypatch, tmp_path) -> None:
    notifier = MagicMock()
    notifier.send_text = AsyncMock(return_value={"code": 0, "msg": "ok"})
    monkeypatch.setattr(
        "stockhot.notification.feishu_bot.get_feishu_notifier", lambda: notifier)
    monkeypatch.setattr(paper_push, "_MARKER_DIR", tmp_path)
    assert paper_push.push_paper_summary("20260813") is True
    assert notifier.send_text.await_count == 1
    # 幂等：第二次跳过
    assert paper_push.push_paper_summary("20260813") is True
    assert notifier.send_text.await_count == 1
    # force 重推
    assert paper_push.push_paper_summary("20260813", force=True) is True
    assert notifier.send_text.await_count == 2
    assert (tmp_path / "paper_push_20260813.ok").exists()


def test_push_feishu_unconfigured(paper_db, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "stockhot.notification.feishu_bot.get_feishu_notifier", lambda: None)
    monkeypatch.setattr(paper_push, "_MARKER_DIR", tmp_path)
    assert paper_push.push_paper_summary("20260813") is False  # 不抛异常
