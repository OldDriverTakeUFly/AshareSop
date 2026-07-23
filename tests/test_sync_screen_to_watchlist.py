"""选股 top20 → watchlist 候选池同步的单元测试.

用临时数据库（:memory: 或 tmp 文件）隔离，不污染生产数据。
重点测：code 格式转换、UPSERT 语义、跌出归档、幂等性。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

TEST_DATE = "2026-07-14"


# ── 辅助：构建最小 top20 JSON 和临时数据库 ──


def _make_top20_json(tmp_path: Path, stocks: list[dict], as_of: str = TEST_DATE) -> Path:
    """写一份 top20 JSON 到 tmp_path，返回路径."""
    # 模拟 studies/output/ 结构
    output_dir = tmp_path / "studies" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"top20_screen_{as_of}.json"
    data = {
        "as_of": as_of,
        "generated_at": datetime.now().isoformat(),
        "universe_size": 100,
        "prefilter_survivors": 50,
        "scored": 50,
        "top20": stocks,
        "config": {},
    }
    json_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return output_dir  # 返回目录，sync 脚本通过 OUTPUT_DIR 读


def _make_test_db(tmp_path: Path) -> Path:
    """创建含 invest_watchlist 表（含 composite_score 列）的临时 DB 文件，返回路径.

    返回文件路径而非连接——因为 sync() 内部会 close 连接，测试需每次重开验证。
    """
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE invest_watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT, sector TEXT,
            added_date TEXT NOT NULL,
            trigger_reason TEXT,
            target_entry_low REAL, target_entry_high REAL,
            stop_loss_pct REAL, priority INTEGER DEFAULT 1,
            status TEXT DEFAULT 'watching',
            source TEXT DEFAULT 'manual',
            notes TEXT, updated_at TEXT,
            composite_score REAL
        );
    """)
    conn.commit()
    conn.close()
    return db_path


def _patch_db(monkeypatch, db_path: Path) -> None:
    """monkeypatch get_connection 返回每次新开的文件连接（sync close 后可重开）."""
    def _get_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    monkeypatch.setattr("stockhot.storage.database.get_connection", _get_conn)


def _query(db_path: Path, sql: str, params: tuple = ()) -> list:
    """重开连接查询，返回 list[dict]（验证用）."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _make_stock(code: str, name: str, score: float, industry: str = "元器件") -> dict:
    """构造一只选股结果 dict."""
    return {
        "ts_code": f"{code}.SH" if code.startswith("6") else f"{code}.SZ",
        "name": name,
        "industry": industry,
        "domain": "super_cycle",
        "composite": score,
        "momentum": 70, "valuation": 80, "prosperity": 65,
        "distress": 30, "delta_g": 5.0, "persistence_bonus": 0.0,
    }


# ===================================================================
# _ts_code_to_code — 格式转换
# ===================================================================


class TestTsCodeConversion:
    def test_sh_suffix(self):
        from studies.sync_screen_to_watchlist import _ts_code_to_code
        assert _ts_code_to_code("603629.SH") == "603629"

    def test_sz_suffix(self):
        from studies.sync_screen_to_watchlist import _ts_code_to_code
        assert _ts_code_to_code("300373.SZ") == "300373"

    def test_no_suffix(self):
        from studies.sync_screen_to_watchlist import _ts_code_to_code
        assert _ts_code_to_code("688127") == "688127"


# ===================================================================
# sync — 核心逻辑（用 monkeypatch 隔离 DB 和 OUTPUT_DIR）
# ===================================================================


class TestSync:
    """测试同步逻辑：INSERT / UPDATE / ARCHIVE / 幂等."""

    def test_insert_new_candidates(self, tmp_path, monkeypatch):
        """新股票全部 INSERT."""
        import studies.sync_screen_to_watchlist as mod

        output_dir = _make_top20_json(tmp_path, [
            _make_stock("603629", "利通电子", 74.9),
            _make_stock("688127", "蓝特光学", 74.2),
        ])
        monkeypatch.setattr(mod, "OUTPUT_DIR", output_dir)

        db_path = _make_test_db(tmp_path)
        _patch_db(monkeypatch, db_path)

        stats = mod.sync(TEST_DATE)
        assert stats["inserted"] == 2
        assert stats["updated"] == 0
        assert stats["archived"] == 0

        rows = _query(
            db_path,
            "SELECT code, name, composite_score, status, source "
            "FROM invest_watchlist WHERE source = 'screen_top20'",
        )
        assert len(rows) == 2
        assert rows[0]["code"] == "603629"
        assert rows[0]["composite_score"] == 74.9

    def test_idempotent_rerun_all_updates(self, tmp_path, monkeypatch):
        """重复跑：第二次全部 UPDATE，无重复行."""
        import studies.sync_screen_to_watchlist as mod

        stocks = [_make_stock("603629", "利通电子", 74.9)]
        output_dir = _make_top20_json(tmp_path, stocks)
        monkeypatch.setattr(mod, "OUTPUT_DIR", output_dir)

        db_path = _make_test_db(tmp_path)
        _patch_db(monkeypatch, db_path)

        mod.sync(TEST_DATE)  # 第一次：insert
        stats = mod.sync(TEST_DATE)  # 第二次：应全 update
        assert stats["inserted"] == 0
        assert stats["updated"] == 1
        assert stats["archived"] == 0

        total = _query(
            db_path,
            "SELECT COUNT(*) AS n FROM invest_watchlist WHERE source='screen_top20'",
        )[0]["n"]
        assert total == 1  # 无重复

    def test_dropped_candidates_archived(self, tmp_path, monkeypatch):
        """跌出本次 top20 的旧候选被归档."""
        import studies.sync_screen_to_watchlist as mod

        # 第一批：2 只
        output_dir = _make_top20_json(tmp_path, [
            _make_stock("603629", "利通电子", 74.9),
            _make_stock("688127", "蓝特光学", 74.2),
        ])
        monkeypatch.setattr(mod, "OUTPUT_DIR", output_dir)
        db_path = _make_test_db(tmp_path)
        _patch_db(monkeypatch, db_path)
        mod.sync(TEST_DATE)

        # 第二批：只保留 603629，688127 跌出
        output_dir2 = _make_top20_json(
            tmp_path, [_make_stock("603629", "利通电子", 75.5)], as_of="2026-07-15"
        )
        monkeypatch.setattr(mod, "OUTPUT_DIR", output_dir2)
        stats = mod.sync("2026-07-15")

        assert stats["inserted"] == 0
        assert stats["updated"] == 1  # 603629 刷新
        assert stats["archived"] == 1  # 688127 归档

        # 688127 应为 archived
        rows = _query(
            db_path, "SELECT status FROM invest_watchlist WHERE code='688127'"
        )
        assert rows[0]["status"] == "archived"

    def test_archived_reactivated_when_reenter(self, tmp_path, monkeypatch):
        """归档的股票重新进榜 → 重新激活为 watching."""
        import studies.sync_screen_to_watchlist as mod

        stocks = [_make_stock("603629", "利通电子", 74.9)]
        output_dir = _make_top20_json(tmp_path, stocks)
        monkeypatch.setattr(mod, "OUTPUT_DIR", output_dir)
        db_path = _make_test_db(tmp_path)
        _patch_db(monkeypatch, db_path)

        mod.sync(TEST_DATE)  # 插入
        # 手动归档
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE invest_watchlist SET status='archived' WHERE code='603629'"
        )
        conn.commit()
        conn.close()
        # 重新进榜
        stats = mod.sync(TEST_DATE)
        assert stats["updated"] == 1
        rows = _query(
            db_path, "SELECT status FROM invest_watchlist WHERE code='603629'"
        )
        assert rows[0]["status"] == "watching"

    def test_manual_watchlist_not_touched(self, tmp_path, monkeypatch):
        """source='manual' 的人工 watchlist 不被归档."""
        import studies.sync_screen_to_watchlist as mod

        output_dir = _make_top20_json(
            tmp_path, [_make_stock("603629", "利通电子", 74.9)]
        )
        monkeypatch.setattr(mod, "OUTPUT_DIR", output_dir)
        db_path = _make_test_db(tmp_path)
        _patch_db(monkeypatch, db_path)
        # 插一条人工 watchlist
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO invest_watchlist (code, name, added_date, status, source) "
            "VALUES ('999999', '人工股', '2026-07-01', 'watching', 'manual')"
        )
        conn.commit()
        conn.close()

        mod.sync(TEST_DATE)  # top20 只有 603629，999999 不在

        rows = _query(
            db_path, "SELECT status FROM invest_watchlist WHERE code='999999'"
        )
        assert rows[0]["status"] == "watching"  # 人工的没被归档


# ===================================================================
# load_top20 — JSON 读取
# ===================================================================


class TestLoadTop20:
    def test_missing_file_raises(self, tmp_path, monkeypatch):
        from studies.sync_screen_to_watchlist import load_top20

        monkeypatch.setattr(
            "studies.sync_screen_to_watchlist.OUTPUT_DIR", tmp_path
        )
        with pytest.raises(FileNotFoundError):
            load_top20("2099-01-01")
