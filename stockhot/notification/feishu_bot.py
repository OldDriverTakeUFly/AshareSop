"""飞书（Lark）自定义机器人 webhook 通知模块.

仅推送：向飞书群发送恐慌预警等文本消息。不接收命令。

架构与 ``telegram_bot.py`` 平行：
- 用 ``httpx`` 原生异步 POST（无飞书 SDK 依赖）
- 重试 3 次 + 指数退避
- 支持可选的签名校验（HMAC-SHA256）
- 测试用 ``_transport=httpx.MockTransport`` 注入，不触碰真实 webhook

飞书自定义机器人文档：
https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot

配置（从 .env 读取）：
    FEISHU_WEBHOOK_URL  — 完整 webhook URL（必填）
    FEISHU_SECRET       — 签名校验密钥（可选，若飞书 bot 开启了签名校验）
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import base64
import os
import time
from typing import Any, Callable

import httpx
from dotenv import load_dotenv
from loguru import logger

_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0


def _sign(timestamp: int, secret: str) -> str:
    """飞书签名校验：HMAC-SHA256(timestamp + "\\n" + secret) → base64.

    飞书要求 body 里额外带 ``timestamp`` 和 ``sign`` 字段。
    """
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


class FeishuNotifier:
    """向飞书群推送文本消息的自定义机器人 notifier.

    构造时传入显式参数以便单元测试（不依赖环境变量）。
    测试时传 ``_transport=httpx.MockTransport(handler)`` 注入 mock transport。
    """

    def __init__(
        self,
        webhook_url: str,
        secret: str | None = None,
        *,
        _transport: httpx.AsyncBaseTransport | None = None,
        _backoff_override: Callable[[int], float] | None = None,
    ) -> None:
        if not webhook_url:
            raise ValueError("webhook_url is required")
        self._webhook_url = webhook_url
        self._secret = secret
        self._transport = _transport
        self._backoff_override = _backoff_override

    async def send_text(self, text: str) -> dict[str, Any]:
        """发送一条文本消息到飞书群.

        重试最多 _MAX_RETRIES 次，指数退避（1s/2s/4s）。
        飞书限频：每分钟 100 条，超限返回 code 130102。

        Returns:
            飞书 API 响应 JSON dict。

        Raises:
            httpx.HTTPStatusError: 重试耗尽后仍失败。
        """
        payload: dict[str, Any] = {
            "msg_type": "text",
            "content": {"text": text},
        }
        # 签名校验（若配置了 secret）
        if self._secret:
            timestamp = int(time.time())
            payload["timestamp"] = str(timestamp)
            payload["sign"] = _sign(timestamp, self._secret)

        last_exc: Exception | None = None
        last_response: httpx.Response | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(transport=self._transport) as client:
                    resp = await client.post(
                        self._webhook_url, json=payload, timeout=10
                    )
                last_response = resp

                # 飞书返回 200 但 body 含 code != 0 表示业务错误
                result = resp.json()
                code = result.get("code", 0)
                if code == 0:
                    logger.info(f"Feishu message sent successfully")
                    return result
                # 130102 = 限频，可重试；其他业务码（如 19021 URL失效）不重试
                if code == 130102:
                    msg = result.get("msg", "rate limited")
                    logger.warning(
                        f"Feishu rate limited (code={code}): {msg}, "
                        f"retry {attempt}/{_MAX_RETRIES}"
                    )
                    last_exc = RuntimeError(f"Feishu code={code}: {msg}")
                else:
                    # 非限频的业务错误：不重试
                    msg = result.get("msg", "unknown error")
                    logger.error(f"Feishu send failed (code={code}): {msg}")
                    return result  # 返回错误响应，不抛

            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                # 网络/HTTP 错误：可重试
                last_exc = exc
                logger.warning(
                    f"Feishu network error (attempt {attempt}): "
                    f"{type(exc).__name__}: {exc}"
                )

            # 退避（最后一次不 sleep）
            if attempt < _MAX_RETRIES:
                delay = (
                    self._backoff_override(attempt)
                    if self._backoff_override
                    else _BACKOFF_BASE * (2 ** (attempt - 1))
                )
                await asyncio.sleep(delay)

        # 重试耗尽
        logger.error(f"Feishu send failed after {_MAX_RETRIES} retries")
        if last_response is not None:
            last_response.raise_for_status()
        if last_exc:
            raise last_exc
        raise RuntimeError("Feishu send failed for unknown reason")


def get_feishu_notifier() -> FeishuNotifier | None:
    """从环境变量构造 FeishuNotifier（生产用）.

    从 .env 读取 ``FEISHU_WEBHOOK_URL``（必填）和 ``FEISHU_SECRET``（可选）。
    未配置 webhook_url 时返回 None（调用方应跳过推送）。
    """
    load_dotenv(override=True)
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    if not webhook_url:
        logger.warning("FEISHU_WEBHOOK_URL not configured, skipping Feishu push")
        return None
    secret = os.environ.get("FEISHU_SECRET") or None
    return FeishuNotifier(webhook_url, secret=secret)
