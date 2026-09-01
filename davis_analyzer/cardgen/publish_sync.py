# davis_analyzer/cardgen/publish_sync.py
"""双文件夹归档:未发布/已发布。

约定(2026-09-01 用户需求):
  - 工程根(<projects_root>)下维护两个归档文件夹:``未发布/`` 与 ``已发布/``。
  - init 新建工程落 ``未发布/<topic>/``;根目录下的存量工程视为兼容布局,仍可解析。
  - 发布成功的判定不读 content_publisher 代码(对接只读),只读其 SQLite 台账
    ``publish_queue`` 表:同一 source 出现过 status='published' 即视为已发布。
  - sync 时把已发布工程挪入 ``已发布/``,其余挪入 ``未发布/``;幂等,可重复执行。
  - build --bump 产生新版本时,已发布工程会被挪回 ``未发布/``(待重新发布)。
  - ``废稿/``(2026-09-02):过期未发工程的终态归档,手工挪入;sync 与归位逻辑跳过,
    工程留在台账里仅作历史记录(resolve 不再解析,如需复活手工挪回 未发布/)。

不修改 scripts/content_publisher(AGENTS.md 只读纪律);本模块只消费其 DB。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLISHER_DB = Path(os.environ.get(
    "PUBLISHER_DB", REPO_ROOT / "storage" / "database" / "content_publisher.db"))
PENDING_DIR = "未发布"
PUBLISHED_DIR = "已发布"
RECYCLED_DIR = "废稿"  # 过期未发工程的终态归档,手工挪入;sync 不触碰
_MARKER = "小红书卡片"  # publish_queue.source 形如 docs/小红书卡片/<topic>


def _published_topics(db: Path = PUBLISHER_DB) -> set[str]:
    """读 publish_queue,返回已发布过的 topic 集合;库/表不存在返回空集。"""
    if not db.exists():
        return set()
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT DISTINCT source FROM publish_queue WHERE status='published'").fetchall()
    except sqlite3.OperationalError:  # 表不存在(旧库)
        return set()
    finally:
        conn.close()
    topics = set()
    for (source,) in rows:
        parts = Path(str(source)).parts
        if _MARKER in parts:
            tail = parts[parts.index(_MARKER) + 1:]
            # 剥掉归档层级(兼容 source 记录了 已发布/<topic> 的情况)
            tail = tuple(p for p in tail if p not in (PENDING_DIR, PUBLISHED_DIR))
            if tail:
                # 2026-09-01 嵌套 topic:品类/日期 取全路径,防整品类误判已发布
                topics.add("/".join(tail))
    return topics


def _safe_move(src: Path, dst: Path) -> bool:
    if not src.exists() or src == dst or dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return True


def _update_ledger_spec_path(projects_root: Path, topic: str, new_spec: Path) -> None:
    """挪动后同步 content_cards.db 的 spec_path,保持 build/status 可用;失败仅忽略。"""
    env = os.environ.get("CARDGEN_LEDGER_DB")
    db = Path(env) if env else REPO_ROOT / "storage" / "database" / "content_cards.db"
    if not db.exists():
        return
    try:
        conn = sqlite3.connect(db)
        conn.execute("UPDATE cards SET spec_path=? WHERE topic=?", (str(new_spec), topic))
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass


def sync(projects_root: Path, db: Path = PUBLISHER_DB,
         dry_run: bool = False) -> list[tuple[str, str]]:
    """按 publish_queue 状态归档工程;返回 [(动作, 路径)] 列表(实际或拟执行)。"""
    published = _published_topics(db)
    actions: list[tuple[str, str]] = []
    if not dry_run:
        for d in (PENDING_DIR, PUBLISHED_DIR):
            (projects_root / d).mkdir(parents=True, exist_ok=True)

    def _iter_projects(base: Path):
        """两级发现:含 cards.spec.json 的目录=工程;否则=品类容器,其下含 spec 的子目录=日期工程。

        兼容:目录无 spec 且其下也无 spec 子目录时,视为存量平铺工程(旧布局不落 spec 也归位)。
        """
        for p in sorted(x for x in base.iterdir() if x.is_dir()):
            if p.name in (PENDING_DIR, PUBLISHED_DIR, RECYCLED_DIR):
                continue
            if (p / "cards.spec.json").exists():
                yield p.name, p
                continue
            subs = [sub for sub in p.iterdir()
                    if sub.is_dir() and (sub / "cards.spec.json").exists()]
            if subs:
                for sub in sorted(subs):
                    yield f"{p.name}/{sub.name}", sub
            else:
                yield p.name, p

    def _move(topic: str, proj: Path, dest_dir: str, label: str) -> None:
        dst = projects_root / dest_dir / topic
        entry = (f"{label}→{dest_dir}", str(dst))
        actions.append(entry)
        if not dry_run:
            if _safe_move(proj, dst):
                _update_ledger_spec_path(projects_root, topic, dst / "cards.spec.json")

    # 根目录与 未发布/ 下的工程:已发布→已发布/,其余(仅根目录存量)→未发布/
    for base, strict in ((projects_root, False), (projects_root / PENDING_DIR, True)):
        if not base.exists():
            continue
        for topic, proj in _iter_projects(base):
            if topic in published:
                _move(topic, proj, PUBLISHED_DIR, "已发布")
            elif not strict:
                _move(topic, proj, PENDING_DIR, "归位")
    return actions


def demote_to_pending(projects_root: Path, proj: Path) -> Path | None:
    """build --bump 产生新版本时,把已发布工程挪回未发布;返回新路径(无需挪则 None)。"""
    pub_root = projects_root / PUBLISHED_DIR
    if proj == pub_root or not proj.is_relative_to(pub_root):
        return None
    topic = str(proj.relative_to(pub_root))
    dst = projects_root / PENDING_DIR / topic
    if _safe_move(proj, dst):
        _update_ledger_spec_path(projects_root, topic, dst / "cards.spec.json")
        return dst
    return proj if proj.exists() else None


def resolve_project(projects_root: Path, topic: str) -> Path:
    """按 未发布/ → 根目录(存量) → 已发布/ 顺序解析工程路径。"""
    for cand in (projects_root / PENDING_DIR / topic, projects_root / topic,
                 projects_root / PUBLISHED_DIR / topic):
        if cand.exists():
            return cand
    return projects_root / PENDING_DIR / topic  # 不存在时按新约定返回(供 init mkdir)
