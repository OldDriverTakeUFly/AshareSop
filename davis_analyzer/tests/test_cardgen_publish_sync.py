"""嵌套日期 topic(连板天梯/2026-09-01)的 sync/归档/降级与 builder PNG 前缀。"""
import sqlite3

from davis_analyzer.cardgen import publish_sync
from davis_analyzer.cardgen.builder import _png_prefix


def _mk_proj(base, topic: str) -> None:
    d = base / topic
    d.mkdir(parents=True)
    (d / "cards.spec.json").write_text("{}", encoding="utf-8")


def _mk_pub_db(tmp_path, sources: list[str]):
    db = tmp_path / "pub.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE publish_queue(id INTEGER PRIMARY KEY, source TEXT, status TEXT)")
    for s in sources:
        con.execute("INSERT INTO publish_queue(source, status) VALUES(?, 'published')", (s,))
    con.commit()
    con.close()
    return db


def test_nested_topic_moves_only_that_date(tmp_path):
    root = tmp_path / "卡片"
    _mk_proj(root / "未发布", "连板天梯/2026-09-01")
    _mk_proj(root / "未发布", "连板天梯/2026-09-02")
    db = _mk_pub_db(tmp_path, ["docs/小红书卡片/未发布/连板天梯/2026-09-01"])
    actions = publish_sync.sync(root, db=db)
    assert (root / "已发布" / "连板天梯" / "2026-09-01").is_dir()
    assert (root / "未发布" / "连板天梯" / "2026-09-02").is_dir()
    assert len(actions) == 1


def test_flat_topic_backward_compat(tmp_path):
    root = tmp_path / "卡片"
    _mk_proj(root, "GPU四小龙")  # 存量根目录平铺工程
    db = _mk_pub_db(tmp_path, ["docs/小红书卡片/GPU四小龙"])
    publish_sync.sync(root, db=db)
    assert (root / "已发布" / "GPU四小龙").is_dir()


def test_category_dir_without_spec_not_moved_as_project(tmp_path):
    root = tmp_path / "卡片"
    _mk_proj(root / "未发布", "龙虎榜/2026-09-01")
    db = _mk_pub_db(tmp_path, [])  # 无已发布记录
    actions = publish_sync.sync(root, db=db)
    assert actions == []
    assert (root / "未发布" / "龙虎榜" / "2026-09-01").is_dir()


def test_demote_nested_to_pending(tmp_path):
    root = tmp_path / "卡片"
    _mk_proj(root / "已发布", "连板天梯/2026-09-01")
    new = publish_sync.demote_to_pending(root, root / "已发布" / "连板天梯" / "2026-09-01")
    assert new == root / "未发布" / "连板天梯" / "2026-09-01"
    assert new.is_dir()


def test_png_prefix_replaces_slash():
    assert _png_prefix("连板天梯/2026-09-01") == "连板天梯_2026-09-01"
    assert _png_prefix("GPU四小龙") == "GPU四小龙"
