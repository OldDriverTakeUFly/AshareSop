"""limitup 包骨架与配置测试。"""

from __future__ import annotations

import sqlite3

from davis_analyzer import config


def test_package_importable() -> None:
    import davis_analyzer.limitup  # noqa: F401


def test_reports_dir_created() -> None:
    assert config.LIMITUP_REPORTS_DIR.exists()
    assert config.LIMITUP_REPORTS_DIR.is_dir()


def test_limitup_db_fixture_has_tables(limitup_db: sqlite3.Connection) -> None:
    rows = limitup_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"limit_pool", "daily_price", "index_daily", "top_list",
            "intraday_feature", "stock_basic", "corp_event"} <= names
