"""Tests for FeishuNotifier — 全 mock，不触碰真实 webhook.

复用 telegram_bot 测试的 httpx.MockTransport 模式。
"""

from __future__ import annotations

import asyncio
import json
import pytest
import httpx
from stockhot.notification.feishu_bot import FeishuNotifier, _sign


def _make_transport(responses: list[dict | Exception]) -> httpx.MockTransport:
    """构造按顺序返回 responses 的 MockTransport."""
    iter_resp = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            resp = next(iter_resp)
        except StopIteration:
            resp = {"code": 0, "msg": "ok"}
        if isinstance(resp, Exception):
            raise resp
        return httpx.Response(200, json=resp)

    return httpx.MockTransport(handler)


def test_send_text_success():
    """正常发送：飞书返回 code=0."""
    transport = _make_transport([{"code": 0, "msg": "ok"}])
    notifier = FeishuNotifier(
        "https://open.feishu.cn/open-apis/bot/v2/hook/test_token",
        _transport=transport,
        _backoff_override=lambda _: 0,  # 测试不等待
    )
    result = asyncio.run(notifier.send_text("test message"))
    assert result["code"] == 0


def test_send_text_retries_on_rate_limit():
    """限频（code=130102）触发重试，第三次成功."""
    transport = _make_transport([
        {"code": 130102, "msg": "rate limited"},
        {"code": 130102, "msg": "rate limited"},
        {"code": 0, "msg": "ok"},
    ])
    notifier = FeishuNotifier(
        "https://open.feishu.cn/open-apis/bot/v2/hook/test_token",
        _transport=transport,
        _backoff_override=lambda _: 0,
    )
    result = asyncio.run(notifier.send_text("test"))
    assert result["code"] == 0


def test_send_text_no_retry_on_business_error():
    """非限频业务错误（如 19021 URL 失效）不重试，直接返回."""
    transport = _make_transport([{"code": 19021, "msg": "invalid webhook"}])
    notifier = FeishuNotifier(
        "https://open.feishu.cn/open-apis/bot/v2/hook/test_token",
        _transport=transport,
        _backoff_override=lambda _: 0,
    )
    result = asyncio.run(notifier.send_text("test"))
    assert result["code"] == 19021


def test_send_text_retries_on_network_error():
    """网络错误触发重试."""
    transport = _make_transport([
        httpx.ConnectError("connection refused"),
        {"code": 0, "msg": "ok"},
    ])
    notifier = FeishuNotifier(
        "https://open.feishu.cn/open-apis/bot/v2/hook/test_token",
        _transport=transport,
        _backoff_override=lambda _: 0,
    )
    result = asyncio.run(notifier.send_text("test"))
    assert result["code"] == 0


def test_sign_format():
    """签名格式：HMAC-SHA256 → base64."""
    sig = _sign(1234567890, "my_secret")
    assert isinstance(sig, str)
    assert len(sig) > 0  # base64 非空


def test_send_text_with_sign():
    """配置 secret 时 payload 含 timestamp + sign."""
    captured_payload = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "msg": "ok"})

    transport = httpx.MockTransport(handler)
    notifier = FeishuNotifier(
        "https://open.feishu.cn/open-apis/bot/v2/hook/test_token",
        secret="my_secret",
        _transport=transport,
        _backoff_override=lambda _: 0,
    )
    asyncio.run(notifier.send_text("signed message"))
    assert "timestamp" in captured_payload
    assert "sign" in captured_payload
    assert captured_payload["msg_type"] == "text"


def test_missing_webhook_url_raises():
    """webhook_url 为空时构造报错."""
    with pytest.raises(ValueError, match="webhook_url is required"):
        FeishuNotifier("")
