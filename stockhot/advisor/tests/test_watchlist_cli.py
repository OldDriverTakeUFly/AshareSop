"""TDD tests for watchlist_cli CRUD operations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stockhot.advisor import watchlist_cli
from stockhot.storage import database as db_module


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Point DB_PATH to a temp file and initialize the schema."""
    temp_path = tmp_path / "test_watchlist_cli.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_path)
    db_module.init_database()
    yield temp_path


def _count_rows(db_path: Path, code: str | None = None) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        if code:
            cur = conn.execute("SELECT COUNT(*) FROM invest_watchlist WHERE code = ?", (code,))
        else:
            cur = conn.execute("SELECT COUNT(*) FROM invest_watchlist")
        return cur.fetchone()[0]
    finally:
        conn.close()


def _get_row(db_path: Path, code: str) -> dict | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("SELECT * FROM invest_watchlist WHERE code = ?", (code,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


class TestAdd:
    def test_add_and_list(self, temp_db, capsys):
        args = watchlist_cli.build_parser().parse_args(
            ["add", "600519", "--name", "贵州茅台", "--reason", "davis_score_85"]
        )
        watchlist_cli.cmd_add(args)
        captured = capsys.readouterr()
        assert "[OK]" in captured.out

        assert _count_rows(temp_db, "600519") == 1
        row = _get_row(temp_db, "600519")
        assert row is not None
        assert row["name"] == "贵州茅台"
        assert row["trigger_reason"] == "davis_score_85"
        assert row["status"] == "watching"

    def test_add_duplicate_fails(self, temp_db, capsys):
        args1 = watchlist_cli.build_parser().parse_args(["add", "600519"])
        watchlist_cli.cmd_add(args1)

        args2 = watchlist_cli.build_parser().parse_args(["add", "600519"])
        with pytest.raises(SystemExit) as exc_info:
            watchlist_cli.cmd_add(args2)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "已在关注列表中" in captured.out
        assert _count_rows(temp_db, "600519") == 1


class TestList:
    def test_list_empty(self, temp_db, capsys):
        args = watchlist_cli.build_parser().parse_args(["list"])
        watchlist_cli.cmd_list(args)
        captured = capsys.readouterr()
        assert "No watchlist entries found" in captured.out

    def test_list_shows_entries(self, temp_db, capsys):
        add_args = watchlist_cli.build_parser().parse_args(
            ["add", "600519", "--name", "贵州茅台"]
        )
        watchlist_cli.cmd_add(add_args)
        capsys.readouterr()

        list_args = watchlist_cli.build_parser().parse_args(["list"])
        watchlist_cli.cmd_list(list_args)
        captured = capsys.readouterr()
        assert "600519" in captured.out
        assert "贵州茅台" in captured.out

    def test_list_status_filter(self, temp_db, capsys):
        for code, status in [("000001", None), ("000002", None), ("000003", "bought")]:
            add_args = watchlist_cli.build_parser().parse_args(["add", code])
            watchlist_cli.cmd_add(add_args)
            capsys.readouterr()
            if status:
                up_args = watchlist_cli.build_parser().parse_args(
                    ["update", code, "--status", status]
                )
                watchlist_cli.cmd_update(up_args)
                capsys.readouterr()

        list_args = watchlist_cli.build_parser().parse_args(
            ["list", "--status", "watching"]
        )
        watchlist_cli.cmd_list(list_args)
        captured = capsys.readouterr()
        assert "000001" in captured.out
        assert "000002" in captured.out
        assert "000003" not in captured.out

    def test_list_sector_filter(self, temp_db, capsys):
        for code, sector in [("000001", "AI"), ("000002", "AI"), ("000003", "医药")]:
            add_args = watchlist_cli.build_parser().parse_args(
                ["add", code, "--sector", sector]
            )
            watchlist_cli.cmd_add(add_args)
            capsys.readouterr()

        list_args = watchlist_cli.build_parser().parse_args(
            ["list", "--status", "all", "--sector", "AI"]
        )
        watchlist_cli.cmd_list(list_args)
        captured = capsys.readouterr()
        assert "000001" in captured.out
        assert "000002" in captured.out
        assert "000003" not in captured.out


class TestRemove:
    def test_remove_existing(self, temp_db, capsys):
        add_args = watchlist_cli.build_parser().parse_args(["add", "600519"])
        watchlist_cli.cmd_add(add_args)
        capsys.readouterr()
        assert _count_rows(temp_db, "600519") == 1

        rm_args = watchlist_cli.build_parser().parse_args(["remove", "600519"])
        watchlist_cli.cmd_remove(rm_args)
        captured = capsys.readouterr()
        assert "[OK]" in captured.out
        assert _count_rows(temp_db, "600519") == 0

    def test_remove_nonexistent_exit_zero(self, temp_db, capsys):
        rm_args = watchlist_cli.build_parser().parse_args(["remove", "999999"])
        watchlist_cli.cmd_remove(rm_args)
        captured = capsys.readouterr()
        assert "[WARN]" in captured.out
        assert "not found" in captured.out


class TestUpdate:
    def test_update_priority(self, temp_db, capsys):
        add_args = watchlist_cli.build_parser().parse_args(["add", "600519"])
        watchlist_cli.cmd_add(add_args)
        capsys.readouterr()

        up_args = watchlist_cli.build_parser().parse_args(
            ["update", "600519", "--priority", "3"]
        )
        watchlist_cli.cmd_update(up_args)
        captured = capsys.readouterr()
        assert "[OK]" in captured.out

        row = _get_row(temp_db, "600519")
        assert row is not None
        assert row["priority"] == 3

    def test_update_status(self, temp_db, capsys):
        add_args = watchlist_cli.build_parser().parse_args(["add", "600519"])
        watchlist_cli.cmd_add(add_args)
        capsys.readouterr()

        up_args = watchlist_cli.build_parser().parse_args(
            ["update", "600519", "--status", "bought"]
        )
        watchlist_cli.cmd_update(up_args)

        row = _get_row(temp_db, "600519")
        assert row is not None
        assert row["status"] == "bought"

    def test_update_nonexistent_exit_one(self, temp_db, capsys):
        up_args = watchlist_cli.build_parser().parse_args(
            ["update", "999999", "--priority", "3"]
        )
        with pytest.raises(SystemExit) as exc_info:
            watchlist_cli.cmd_update(up_args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "[ERROR]" in captured.out

    def test_update_no_fields_specified(self, temp_db, capsys):
        add_args = watchlist_cli.build_parser().parse_args(["add", "600519"])
        watchlist_cli.cmd_add(add_args)
        capsys.readouterr()

        up_args = watchlist_cli.build_parser().parse_args(["update", "600519"])
        watchlist_cli.cmd_update(up_args)
        captured = capsys.readouterr()
        assert "[WARN]" in captured.out
