"""Per-account run lock (flock) for paper-trading concurrency safety.

Problems this module solves:

1. ``DailyExecutor.run_day`` used a check-then-act guard (``has_run_on``)
   whose race window spans the whole run (minutes of factor computation),
   so two concurrent processes could execute the same trading day twice
   and double-book trades.
2. abx 脚本的 ``reset_account`` 按固定名 DELETE + 重建账户;重跑一个还在
   运行中的实验会静默清掉在跑数据(第一个进程继续写入已删除的
   account_id,产出孤儿行 + 残缺汇总)。

Solution: an exclusive OS-level file lock per account, held for the
duration of ``run_day`` / the whole ``run_backfill_auto`` range. flock is
crash-safe — the kernel releases it when the owning process dies, so a
killed run never leaves a stale lock behind. ``delete_account_if_idle``
refuses to wipe an account whose lock is currently held.
"""

from __future__ import annotations

import fcntl
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from loguru import logger

# Reentrancy: the same process nests run_day inside run_backfill_auto.
_HELD: set[int] = set()

_PAPER_DATA_TABLES = (
    "paper_positions",
    "paper_trades",
    "paper_nav_history",
    "paper_shadow_trades",
)


def _lock_dir() -> Path:
    # Resolved per call so tests that monkeypatch DB_PATH get isolated locks.
    from stockhot.storage.database import DB_PATH

    d = Path(DB_PATH).parent / "paper_locks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _lock_path(account_id: int) -> Path:
    return _lock_dir() / f"paper_run_{account_id}.lock"


@contextmanager
def _strict_run_lock(account_id: int) -> Iterator[bool]:
    """Try to take the run lock on a fresh fd (ignores this process's _HELD).

    flock is per open-file-description, so a second open+flock conflicts
    even within the same process — used by the reset guard to detect
    holders including ourselves.
    """
    fd = os.open(_lock_path(account_id), os.O_CREAT | os.O_RDWR, 0o644)
    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def account_run_lock(account_id: int) -> Iterator[bool]:
    """Hold the account's run lock for the context.

    Yields True when acquired (or already held by this process — reentrant),
    False when another process holds it.
    """
    if account_id in _HELD:
        yield True
        return
    with _strict_run_lock(account_id) as acquired:
        if not acquired:
            yield False
            return
        _HELD.add(account_id)
        try:
            yield True
        finally:
            _HELD.discard(account_id)


def account_run_in_progress(account_id: int) -> bool:
    """True if any process (including this one) holds this account's run lock."""
    with _strict_run_lock(account_id) as acquired:
        return not acquired


def get_account_id_by_name(name: str) -> int | None:
    """Look up a paper account id by name (None if missing)."""
    from stockhot.storage.database import DB_PATH

    with sqlite3.connect(str(DB_PATH)) as c:
        row = c.execute("SELECT id FROM paper_accounts WHERE name=?", (name,)).fetchone()
    return row[0] if row else None


def delete_account_if_idle(name: str) -> None:
    """Delete an account and its data, refusing while a run is in flight.

    Acquires the run lock for the duration of the delete, so a backtest
    that is still writing cannot be wiped by a script re-launch, and a run
    starting right after us sees the lock and skips.

    Raises RuntimeError if another process holds the account's run lock.
    """
    account_id = get_account_id_by_name(name)
    if account_id is None:
        return
    # Strict acquire (no reentrancy shortcut): refuses even if THIS process
    # holds the run lock — deleting data under our own in-flight run is
    # exactly the silent-corruption scenario we guard against.
    with _strict_run_lock(account_id) as acquired:
        if not acquired:
            raise RuntimeError(
                f"Paper account '{name}' 有运行中的回测/交易进程(运行锁被占用),"
                "拒绝 reset——先停掉在跑的进程再重跑"
            )
        from stockhot.storage.database import DB_PATH

        with sqlite3.connect(str(DB_PATH)) as c:
            for tbl in _PAPER_DATA_TABLES:
                c.execute(f"DELETE FROM {tbl} WHERE account_id=?", (account_id,))
            c.execute("DELETE FROM paper_accounts WHERE id=?", (account_id,))
            c.commit()
    logger.info(f"[runlock] reset account '{name}' (id={account_id}) — no run in progress")
