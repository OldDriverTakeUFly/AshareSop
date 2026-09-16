# tests/test_publish_reconcile.py — 发布状态对账脚本测试(2026-09-17)
# 覆盖:emoji 差异匹配 / 日卡标题全等 / 包含式匹配 / 不匹配不动 / --dry 不写库 / 审计行写入
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "publish_reconcile", REPO_ROOT / "scripts" / "publish_reconcile.py")
pr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pr)


def _make_db(path: Path, table_sql: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(table_sql)
    conn.commit()
    conn.close()


@pytest.fixture()
def dbs(tmp_path: Path):
    metrics = tmp_path / "xhs_metrics.db"
    pool = tmp_path / "content_publisher.db"
    _make_db(metrics, "CREATE TABLE notes(note_id INTEGER, title TEXT, published_at TEXT)")
    _make_db(pool, """CREATE TABLE publish_queue(
        id INTEGER PRIMARY KEY, source TEXT, title TEXT, status TEXT, published_at TEXT)""")
    _make_db(pool, """CREATE TABLE publish_log(
        queue_id INTEGER, ts TEXT, event TEXT, detail TEXT)""")
    return metrics, pool


def _seed(metrics: Path, pool: Path, notes: list[tuple], pending: list[tuple]) -> None:
    with sqlite3.connect(metrics) as c:
        c.executemany("INSERT INTO notes VALUES(?,?,?)", notes)
    with sqlite3.connect(pool) as c:
        c.executemany("INSERT INTO publish_queue(id, source, title, status) VALUES(?,?,?,?)", pending)


def test_normalize_strips_emoji_and_punct() -> None:
    assert pr.normalize_title("10万张GPU训出的GPT-6，五个问题看懂🧠") == \
        pr.normalize_title("10万张GPU训出的GPT-6，五个问题看懂🤖")
    assert pr.normalize_title("09-16 连板天梯 | 每日数据复盘") == "0916连板天梯每日数据复盘"


def test_match_rules() -> None:
    assert pr.is_match(pr.normalize_title("油价的心电图:一年两次过山车"),
                       pr.normalize_title("油价的心电图:一年两次过山车📈"))
    assert not pr.is_match("周期考卷", "cpi温度计")  # 短且不相等
    assert not pr.is_match("", "anything")


def test_emoji_variant_marks_published(dbs) -> None:
    metrics, pool = dbs
    _seed(metrics, pool,
          notes=[(24, "10万张GPU训出的GPT-6，五个问题看懂🤖", "2026-09-05 12:35")],
          pending=[(76, "docs/小红书卡片/未发布/GPT6发布长文.md",
                    "10万张GPU训出的GPT-6，五个问题看懂🧠", "draft")])
    summary = pr.reconcile(metrics, pool)
    assert len(summary["marked"]) == 1
    with sqlite3.connect(pool) as c:
        status, ts = c.execute("SELECT status, published_at FROM publish_queue WHERE id=76").fetchone()
        logs = c.execute("SELECT event, detail FROM publish_log").fetchall()
    assert status == "published" and ts == "2026-09-05T12:35"
    assert logs and logs[0][0] == "reconcile_auto_published" and "note #24" in logs[0][1]


def test_daily_card_exact_match(dbs) -> None:
    metrics, pool = dbs
    _seed(metrics, pool,
          notes=[(48, "09-16 连板天梯 | 每日数据复盘", "2026-09-16 18:28")],
          pending=[(107, "docs/小红书卡片/未发布/连板天梯/2026-09-16",
                    "09-16 连板天梯 | 每日数据复盘", "scheduled")])
    assert len(pr.reconcile(metrics, pool)["marked"]) == 1


def test_no_match_leaves_untouched(dbs) -> None:
    metrics, pool = dbs
    _seed(metrics, pool,
          notes=[(42, "铟：两年涨了2.5倍的金属📈📉", "2026-09-13 14:53")],
          pending=[(90, "docs/小红书卡片/未发布/周期空间计算说明书长文.md",
                    "周期股上行空间的计算说明书📐", "draft")])
    assert pr.reconcile(metrics, pool)["marked"] == []
    with sqlite3.connect(pool) as c:
        assert c.execute("SELECT status FROM publish_queue WHERE id=90").fetchone()[0] == "draft"
        assert c.execute("SELECT COUNT(*) FROM publish_log").fetchone()[0] == 0


def test_dry_run_writes_nothing(dbs) -> None:
    metrics, pool = dbs
    _seed(metrics, pool,
          notes=[(24, "每个周期都有一张考卷:考题、及格线、交卷时间", "2026-09-12 13:18")],
          pending=[(89, "docs/小红书卡片/未发布/周期考卷长文.md",
                    "每个周期都有一张考卷：考题、及格线、交卷时间📝", "draft")])
    summary = pr.reconcile(metrics, pool, dry_run=True)
    assert len(summary["marked"]) == 1 and summary["dry_run"]
    with sqlite3.connect(pool) as c:
        assert c.execute("SELECT status FROM publish_queue WHERE id=89").fetchone()[0] == "draft"
        assert c.execute("SELECT COUNT(*) FROM publish_log").fetchone()[0] == 0


def test_duplicate_notes_take_earliest(dbs) -> None:
    metrics, pool = dbs
    _seed(metrics, pool,
          notes=[(29, "日元加息的两副面孔：一边利好A股，一边抽恒", "2026-09-05 15:36"),
                 (23, "日元加息的两副面孔：一边利好A股，一边抽", "2026-09-05 15:36")],
          pending=[(79, "docs/小红书卡片/未发布/日元加息传导链长文.md",
                    "日元加息的两副面孔：一边利好A股，一边抽", "draft")])
    pr.reconcile(metrics, pool)
    with sqlite3.connect(pool) as c:
        row = c.execute("SELECT status FROM publish_queue WHERE id=79").fetchone()
        assert row[0] == "published"
