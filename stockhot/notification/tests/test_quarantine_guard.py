"""隔离层自检：未显式注入 transport 的通知器在 pytest 内被自动隔离.

验证根 conftest 的 _quarantine_feishu 防线真实生效（不发真实网络请求）。
"""

from __future__ import annotations

import asyncio

import httpx

from stockhot.notification.feishu_bot import EnterpriseFeishuNotifier, FeishuNotifier


def test_implicit_notifier_is_quarantined(monkeypatch) -> None:
    """不传 _transport 构造（生产代码 get_feishu_notifier 的真实路径形态）：
    发送应命中隔离层并返回 ok(quarantined)，而非触网."""
    notifier = FeishuNotifier("https://open.feishu.cn/open-apis/bot/v2/hook/REAL")
    result = asyncio.run(notifier.send_text("quarantine self-test"))
    assert result["code"] == 0
    assert result["msg"] == "ok(quarantined)"


def test_explicit_transport_still_wins() -> None:
    """显式注入的 MockTransport 不被隔离层覆盖（test_feishu_bot 的既有模式）."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "msg": "explicit"})

    notifier = FeishuNotifier(
        "https://open.feishu.cn/open-apis/bot/v2/hook/x",
        _transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(notifier.send_text("t"))
    assert result["msg"] == "explicit"


def test_enterprise_notifier_quarantined(monkeypatch) -> None:
    """企业自建应用通知器同样被隔离（构造签名差异不影响 setdefault）."""
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec_test")
    notifier = EnterpriseFeishuNotifier(
        "cli_test", "sec_test", chat_id="oc_test"
    )
    result = asyncio.run(notifier.send_text("quarantine self-test"))
    assert result["code"] == 0
