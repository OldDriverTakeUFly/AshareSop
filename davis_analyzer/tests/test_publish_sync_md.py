# tests/test_publish_sync_md.py — sync 平铺长文 .md 归位测试(2026-09-17)
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from davis_analyzer.systems.cardgen import publish_sync


@pytest.fixture()
def env(tmp_path: Path):
    root = tmp_path / "卡片"
    (root / "未发布").mkdir(parents=True)
    (root / "已发布").mkdir()
    db = tmp_path / "pool.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE publish_queue(id INTEGER PRIMARY KEY, source TEXT, status TEXT)")
    conn.commit()
    conn.close()
    return root, db


def _seed(db: Path, rows: list[tuple[str, str]]) -> None:
    with sqlite3.connect(db) as c:
        c.executemany("INSERT INTO publish_queue(source, status) VALUES(?,?)", rows)


def test_published_md_moves_to_published_dir(env) -> None:
    root, db = env
    (root / "未发布" / "GPT6发布长文.md").write_text("正文", encoding="utf-8")
    _seed(db, [("docs/小红书卡片/未发布/GPT6发布长文.md", "published")])
    actions = publish_sync.sync(root, db=db)
    assert (root / "已发布" / "GPT6发布长文.md").exists()
    assert not (root / "未发布" / "GPT6发布长文.md").exists()
    assert any(a[0] == "已发布→已发布" for a in actions)


def test_draft_md_stays_in_pending(env) -> None:
    root, db = env
    (root / "未发布" / "油价心电图长文.md").write_text("正文", encoding="utf-8")
    _seed(db, [("docs/小红书卡片/未发布/油价心电图长文.md", "draft")])
    publish_sync.sync(root, db=db)
    assert (root / "未发布" / "油价心电图长文.md").exists()


def test_root_level_md_relocated_to_pending(env) -> None:
    root, db = env
    (root / "存量长文.md").write_text("正文", encoding="utf-8")
    _seed(db, [("docs/小红书卡片/存量长文.md", "draft")])
    publish_sync.sync(root, db=db)
    assert (root / "未发布" / "存量长文.md").exists()


def test_md_and_project_move_together(env) -> None:
    root, db = env
    proj = root / "未发布" / "雷达专题20260912"
    proj.mkdir()
    (proj / "cards.spec.json").write_text("{}", encoding="utf-8")
    (root / "未发布" / "周期考卷长文.md").write_text("正文", encoding="utf-8")
    _seed(db, [("docs/小红书卡片/未发布/雷达专题20260912", "published"),
               ("docs/小红书卡片/未发布/周期考卷长文.md", "published")])
    publish_sync.sync(root, db=db)
    assert (root / "已发布" / "雷达专题20260912" / "cards.spec.json").exists()
    assert (root / "已发布" / "周期考卷长文.md").exists()
