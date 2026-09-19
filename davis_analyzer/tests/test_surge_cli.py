"""surge CLI 冒烟: help/status(内存库)."""

from __future__ import annotations

import sqlite3

from davis_analyzer.systems.surge import cli, db


def test_cli_help(capsys):
    for args in (["--help"], ["run", "--help"], ["backfill", "--help"]):
        try:
            cli.main(args)
        except SystemExit as e:
            assert e.code == 0


def test_cli_status(monkeypatch, capsys):
    conn = sqlite3.connect(":memory:")
    db.ensure_tables(conn)
    monkeypatch.setattr(cli.db, "connect", lambda: conn)
    monkeypatch.setattr(cli.db, "latest_trade_date", lambda c: None)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "cyq_days=0" in out and "announcements=0" in out
