"""Repo-level pytest guard: quarantine Feishu notifications inside tests.

背景（2026-08-18）：曾出现跑一次 pytest 向生产飞书群发测试消息。根因是个别
测试路径在未注入 MockTransport 的情况下构造了真实通知器。为在系统内彻底
消化此类问题，本 conftest 对全仓 pytest 生效：

- 任何 `FeishuNotifier` / `EnterpriseFeishuNotifier` 若未显式传 `_transport`，
  构造时自动注入 MockTransport（永不触网，send 返回
  ``{"code": 0, "msg": "ok(quarantined)"}``）。
- 已显式传 MockTransport 的测试（如 stockhot/notification/tests/
  test_feishu_bot.py）不受影响（setdefault 语义）。
- 确有必要触达真实 webhook 的测试：设环境变量 ``FEISHU_ALLOW_REAL=1`` 放行
  （不建议；推送行为请在 CI 外手动验证）。
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _quarantine_feishu(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get("FEISHU_ALLOW_REAL", ""):
        return
    try:
        import httpx

        import stockhot.notification.feishu_bot as fb
    except Exception:  # pragma: no cover - 依赖缺失时无从隔离
        return

    def _handler(request: "httpx.Request") -> "httpx.Response":
        url = str(request.url)
        if "tenant_access_token" in url:
            return httpx.Response(200, json={
                "code": 0, "expire": 7200,
                "tenant_access_token": "quarantined-token",
            })
        return httpx.Response(200, json={"code": 0, "msg": "ok(quarantined)"})

    mock_transport = httpx.MockTransport(_handler)

    for cls in (fb.FeishuNotifier, fb.EnterpriseFeishuNotifier):
        orig_init = cls.__init__

        def _make_guarded(original):
            def guarded(self, *args, **kwargs):  # noqa: ANN002, ANN003
                kwargs.setdefault("_transport", mock_transport)
                original(self, *args, **kwargs)

            return guarded

        monkeypatch.setattr(cls, "__init__", _make_guarded(orig_init))
