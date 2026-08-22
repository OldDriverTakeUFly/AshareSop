"""data_layer.repository 板块资金流读取 API 测试.

背景：结构化表列名为 sector_name，而消费方（after-hours-review skill /
JSON fallback 格式 / panic_detector）分别期望 name / sector_name 两套 key。
读取 API 必须同时返回两个 key（双 key 别名），保证表达力与 JSON 等价。
"""

from __future__ import annotations

import sqlite3

import pandas as pd

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


# ═══════════════════════════════════════════════════════════════════
# persist_daily_snapshot（盘后写穿落库，2026-08 断供事故修复）
# ═══════════════════════════════════════════════════════════════════

_DAILY_PRICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_price (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL NOT NULL,
    pre_close REAL, pct_chg REAL, vol REAL, amount REAL,
    adj_factor REAL, fetched_at REAL,
    PRIMARY KEY (ts_code, trade_date)
)
"""


def _setup_daily_db(db_path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(_DAILY_PRICE_SCHEMA)
    conn.commit()
    conn.close()


def _daily_df() -> pd.DataFrame:
    """3 行日线，其中 600000.SH 的 close 为 NaN（Tushare 部分发布防线）."""
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ", "600000.SH"],
            "trade_date": ["20260817"] * 3,
            "open": [10.0, 20.0, None],
            "high": [11.0, 21.0, None],
            "low": [9.0, 19.0, None],
            "close": [10.5, 20.5, None],
            "pre_close": [10.0, 20.0, None],
            "pct_chg": [5.0, 2.5, None],
            "vol": [1000.0, 2000.0, None],
            "amount": [1.0, 2.0, None],
        }
    )


def _adj_df() -> pd.DataFrame:
    # 与真实 adj_factor 接口返回形态一致(必含 ts_code); 旧 fixture 缺该列,
    # 恰是 2026-07~08 单值广播事故的镜像——按 (ts_code, trade_date) 复合键
    # 合并后该形态不再合法
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ", "600000.SH"],
            "trade_date": ["20260817"] * 3,
            "adj_factor": [1.25] * 3,
        }
    )


def test_persist_daily_snapshot_drops_null_close(tmp_path, monkeypatch):
    """close 为空的行必须丢弃（历史 NaN 事故防线），不丢整批."""
    db = tmp_path / "test.db"
    _setup_daily_db(db)
    monkeypatch.setattr(
        repository_module, "get_connection", lambda: sqlite3.connect(db)
    )

    repo = MarketDataRepository()
    n = repo.persist_daily_snapshot(_daily_df(), _adj_df())

    assert n == 2
    with sqlite3.connect(db) as conn:
        codes = [r[0] for r in conn.execute(
            "SELECT ts_code FROM daily_price ORDER BY ts_code")]
        adj = [r[0] for r in conn.execute(
            "SELECT DISTINCT adj_factor FROM daily_price")]
    assert codes == ["000001.SZ", "000002.SZ"]
    assert adj == [1.25]


def test_persist_daily_snapshot_idempotent(tmp_path, monkeypatch):
    """重复写穿幂等（INSERT OR REPLACE，不产生重复行）."""
    db = tmp_path / "test.db"
    _setup_daily_db(db)
    monkeypatch.setattr(
        repository_module, "get_connection", lambda: sqlite3.connect(db)
    )

    repo = MarketDataRepository()
    repo.persist_daily_snapshot(_daily_df(), _adj_df())
    repo.persist_daily_snapshot(_daily_df(), _adj_df())

    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM daily_price").fetchone()[0]
    assert count == 2


def test_persist_daily_snapshot_empty_input(tmp_path, monkeypatch):
    """空输入返回 0，不触碰数据库."""
    db = tmp_path / "test.db"
    _setup_daily_db(db)
    monkeypatch.setattr(
        repository_module, "get_connection", lambda: sqlite3.connect(db)
    )

    repo = MarketDataRepository()
    assert repo.persist_daily_snapshot(pd.DataFrame(), None) == 0
    assert repo.persist_daily_snapshot(None, _adj_df()) == 0


# ── sync_index_to_daily（指数日线同步：锚与基准参赛者依赖）──

_INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS index_daily (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL NOT NULL,
    vol REAL, amount REAL, pct_chg REAL,
    fetched_at REAL,
    PRIMARY KEY (ts_code, trade_date)
)
"""


def test_sync_index_to_daily(tmp_path, monkeypatch):
    """从 index_daily 同步 000001.SH 到 daily_price，幂等且不碰个股行."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.execute(_DAILY_PRICE_SCHEMA)
    conn.execute(_INDEX_SCHEMA)
    conn.execute(
        "INSERT INTO index_daily VALUES ('000001.SH','20260817',"
        "3500.0,3550.0,3490.0,3520.5,123456.0,99999.0,1.41,0)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        repository_module, "get_connection", lambda: sqlite3.connect(db)
    )

    repo = MarketDataRepository()
    assert repo.sync_index_to_daily("20260817") == 1
    assert repo.sync_index_to_daily("20260817") == 0  # 幂等
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT ts_code, close, pct_chg, adj_factor FROM daily_price "
            "WHERE ts_code='000001.SH'"
        ).fetchone()
        cnt = conn.execute("SELECT COUNT(*) FROM daily_price").fetchone()[0]
    assert row == ("000001.SH", 3520.5, 1.41, None)
    assert cnt == 1
