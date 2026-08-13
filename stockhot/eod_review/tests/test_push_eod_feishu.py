"""Tests for push_eod_feishu 幂等保护 —— 全 mock，不触真实飞书/SQLite/git.

核心断言：飞书推送按交易日幂等，当日已推过则跳过，与 git 提交的幂等行为对齐，
防止 agent 在一个 session 内反复调用导致飞书群被刷屏。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from stockhot.eod_review import push_eod_feishu as mod


# ── 公共 fixture：把 marker 目录指向临时目录，隔离真实文件系统 ──


@pytest.fixture
def isolated_lock_dir(tmp_path, monkeypatch):
    lock_dir = tmp_path / ".eod_feishu_push"
    monkeypatch.setattr(mod, "_PUSH_LOCK_DIR", lock_dir)
    return lock_dir


@pytest.fixture
def mock_notifier(monkeypatch):
    """注入一个 fake 飞书 notifier，send_text 为 AsyncMock."""
    notifier = MagicMock()
    notifier.send_text = AsyncMock(return_value={"code": 0, "msg": "ok"})
    monkeypatch.setattr(
        "stockhot.notification.feishu_bot.get_feishu_notifier",
        lambda: notifier,
    )
    # build_eod_feishu_summary 默认读 SQLite，测试里替换为常量避免 DB 依赖
    monkeypatch.setattr(
        mod, "build_eod_feishu_summary", lambda d: f"📊 盘后总结 | {d} (fake)"
    )
    return notifier


# ── 幂等核心行为 ──


def test_push_writes_marker_on_success(isolated_lock_dir, mock_notifier):
    """首次推送：send_text 被调用一次，且 marker 落盘."""
    assert not mod._is_feishu_pushed("2026-08-13")

    ok = mod.push_eod_feishu("2026-08-13")

    assert ok is True
    mock_notifier.send_text.assert_awaited_once()
    assert mod._is_feishu_pushed("2026-08-13") is True
    assert mod._push_marker_path("2026-08-13").exists()


def test_push_skipped_when_marker_exists(isolated_lock_dir, mock_notifier):
    """当日已推过：跳过 send_text（幂等），返回 True."""
    # 模拟当日已成功推过一次
    mod._mark_feishu_pushed("2026-08-13")

    ok = mod.push_eod_feishu("2026-08-13")

    assert ok is True
    mock_notifier.send_text.assert_not_awaited()  # 关键：不再发送


def test_force_overrides_marker(isolated_lock_dir, mock_notifier):
    """force=True 时即使当日已推过也强制重推."""
    mod._mark_feishu_pushed("2026-08-13")

    ok = mod.push_eod_feishu("2026-08-13", force=True)

    assert ok is True
    mock_notifier.send_text.assert_awaited_once()


def test_repeated_calls_send_only_once(isolated_lock_dir, mock_notifier):
    """复现现场：一个 session 内连调 14 次，飞书只应收到 1 条."""
    results = [mod.push_eod_feishu("2026-08-13") for _ in range(14)]

    assert all(results)  # 全部返回 True（成功或跳过）
    assert mock_notifier.send_text.await_count == 1  # 仅首次真正发送


# ── 边界：失败/未配置 不应落锁（保证可重试）──


def test_no_marker_when_send_fails(isolated_lock_dir, mock_notifier):
    """send_text 抛异常：返回 False，且不落 marker（下次仍可重试）."""
    mock_notifier.send_text = AsyncMock(side_effect=RuntimeError("network"))

    ok = mod.push_eod_feishu("2026-08-13")

    assert ok is False
    assert mod._is_feishu_pushed("2026-08-13") is False


def test_no_marker_when_notifier_disabled(isolated_lock_dir, monkeypatch):
    """飞书未配置（notifier=None）：不落 marker，配置后仍可推送."""
    monkeypatch.setattr(
        "stockhot.notification.feishu_bot.get_feishu_notifier", lambda: None
    )

    ok = mod.push_eod_feishu("2026-08-13")

    assert ok is True  # 未配置不算失败
    assert mod._is_feishu_pushed("2026-08-13") is False


# ── main 入口：--force 透传 ──


def test_main_force_flag_threads_through(monkeypatch, isolated_lock_dir):
    """main(--force) 应把 force=True 传给 push_eod_feishu."""
    pushed = {}
    monkeypatch.setattr(
        mod, "commit_push_report", lambda d: True
    )  # 跳过真实 git 操作
    monkeypatch.setattr(
        mod,
        "push_eod_feishu",
        lambda d, *, force=False: pushed.setdefault("force", force) or True,
    )

    rc = mod.main(["--date", "2026-08-13", "--force"])

    assert rc == 0
    assert pushed["force"] is True
