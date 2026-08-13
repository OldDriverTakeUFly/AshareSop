"""data_layer.repository 板块资金流读取 API 测试.

背景：结构化表列名为 sector_name，而消费方（after-hours-review skill /
JSON fallback 格式 / panic_detector）分别期望 name / sector_name 两套 key。
读取 API 必须同时返回两个 key（双 key 别名），保证表达力与 JSON 等价。
"""

from __future__ import annotations

import sqlite3

import stockhot.data_layer.repository as repository_module
from stockhot.data_layer.repository import MarketDataRepository

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fund_flow_sector (
    trade_date TEXT NOT NULL,
    sector_name TEXT NOT NULL,
    change_pct REAL,
    main_net REAL,
    main_pct REAL,
    huge_net REAL,
    large_net REAL,
    medium_net REAL,
    small_net REAL,
    fetched_at REAL,
    PRIMARY KEY (trade_date, sector_name)
)
"""

_ROWS = [
    ("2026-08-13", "化学制药", 1.13, 12.31, 0.0, 8.0, 4.31, -1.0, -2.0),
    ("2026-08-13", "小金属", -3.63, -75.2, 0.0, -40.0, -35.2, 5.0, 6.0),
    ("2026-08-12", "化学制药", 0.5, 5.0, 0.0, 3.0, 2.0, 0.0, 0.0),
]


def _setup_db(db_path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    for r in _ROWS:
        conn.execute(
            "INSERT INTO fund_flow_sector (trade_date, sector_name, change_pct, "
            "main_net, main_pct, huge_net, large_net, medium_net, small_net, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            r,
        )
    conn.commit()
    conn.close()


def test_get_fund_flow_sector_returns_name_and_sector_name_alias(tmp_path, monkeypatch):
    """读取结果必须同时含 name 与 sector_name 两个 key（与 JSON 格式对齐）."""
    db = tmp_path / "test.db"
    _setup_db(db)
    monkeypatch.setattr(
        repository_module, "get_connection", lambda: sqlite3.connect(db)
    )

    repo = MarketDataRepository()
    rows = repo.get_fund_flow_sector("2026-08-13")

    assert len(rows) == 2
    # 按 change_pct 降序：化学制药在前
    assert rows[0]["name"] == "化学制药"
    assert rows[0]["sector_name"] == "化学制药"
    assert rows[1]["name"] == "小金属"
    assert rows[1]["sector_name"] == "小金属"
    # 其余字段不受影响
    assert rows[0]["main_net"] == 12.31


def test_get_fund_flow_sector_only_target_date(tmp_path, monkeypatch):
    """只读目标日期，不串日."""
    db = tmp_path / "test.db"
    _setup_db(db)
    monkeypatch.setattr(
        repository_module, "get_connection", lambda: sqlite3.connect(db)
    )

    repo = MarketDataRepository()
    rows = repo.get_fund_flow_sector("2026-08-12")

    assert len(rows) == 1
    assert rows[0]["name"] == "化学制药"
