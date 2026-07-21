"""飞书（Lark）通知模块 — 支持两种机器人模式.

仅推送：向飞书群发送恐慌预警等文本消息。不接收命令。

## 两种模式

### 1. 企业自建应用机器人（推荐，已启用）

通过 App ID + App Secret 换取 tenant_access_token，再调 IM 消息接口推送。
需要在飞书开发者平台创建企业自建应用并发布，把机器人加入目标群。

配置（.env）：
    FEISHU_APP_ID     — 应用 App ID（cli_ 开头）
    FEISHU_APP_SECRET — 应用 App Secret
    FEISHU_CHAT_ID    — 目标群 chat_id（oc_ 开头）

### 2. 群自定义机器人（简单备选）

直接 POST webhook URL，无需应用凭证，但只能往绑定的群发消息。

配置（.env）：
    FEISHU_WEBHOOK_URL — 完整 webhook URL
    FEISHU_SECRET      — 签名校验密钥（可选）

## 自动选择

``get_feishu_notifier()`` 优先读企业自建应用配置，其次读 webhook 配置。
两者都未配置时返回 None。

架构与 ``telegram_bot.py`` 平行：
- 用 ``httpx`` 原生异步 POST（无飞书 SDK 依赖）
- 重试 3 次 + 指数退避
- 测试用 ``_transport=httpx.MockTransport`` 注入，不触碰真实 API
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


class EnterpriseFeishuNotifier:
    """企业自建应用机器人推送（通过 App ID/Secret + IM 消息接口）.

    两步推送：① 用 App ID/Secret 换 tenant_access_token；
              ② 用 token 调 IM 消息接口发到指定 chat_id。

    token 有缓存（有效期约 2 小时），过期自动刷新。
    """

    _TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    _MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        chat_id: str,
        *,
        _transport: httpx.AsyncBaseTransport | None = None,
        _backoff_override: Callable[[int], float] | None = None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._chat_id = chat_id
        self._transport = _transport
        self._backoff_override = _backoff_override
        # token 缓存（避免每次推送都重新获取）
        self._cached_token: str | None = None
        self._token_expire: float = 0  # unix timestamp，过期时间

    async def _get_token(self) -> str:
        """获取 tenant_access_token（带缓存，提前 5 分钟刷新）."""
        import time as _time
        if self._cached_token and _time.time() < self._token_expire:
            return self._cached_token

        async with httpx.AsyncClient(transport=self._transport) as client:
            resp = await client.post(
                self._TOKEN_URL,
                json={"app_id": self._app_id, "app_secret": self._app_secret},
                timeout=10,
            )
        result = resp.json()
        if result.get("code") != 0:
            raise RuntimeError(f"获取 token 失败: {result.get('msg')}")
        self._cached_token = result["tenant_access_token"]
        # token 有效期 expire（秒），提前 5 分钟过期保险
        self._token_expire = _time.time() + result.get("expire", 7200) - 300
        return self._cached_token

    async def send_text(self, text: str) -> dict[str, Any]:
        """发送文本消息到 chat_id 指定的群.

        重试最多 _MAX_RETRIES 次。token 过期会自动刷新重试。
        """
        import json as _json

        last_exc: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                token = await self._get_token()
                payload = {
                    "receive_id": self._chat_id,
                    "msg_type": "text",
                    "content": _json.dumps({"text": text}),
                }
                async with httpx.AsyncClient(transport=self._transport) as client:
                    resp = await client.post(
                        self._MSG_URL,
                        headers={"Authorization": f"Bearer {token}"},
                        json=payload,
                        timeout=10,
                    )
                result = resp.json()
                code = result.get("code", -1)
                if code == 0:
                    logger.info("Feishu enterprise message sent successfully")
                    return result

                # 99991663 = token 过期/无效，清除缓存重试
                if code == 99991663:
                    self._cached_token = None
                    self._token_expire = 0
                    logger.warning(f"Feishu token expired, retrying {attempt}/{_MAX_RETRIES}")

                # 130102 = 限频，可重试
                elif code == 130102:
                    logger.warning(f"Feishu rate limited, retry {attempt}/{_MAX_RETRIES}")
                    last_exc = RuntimeError(f"rate limited: {result.get('msg')}")
                else:
                    # 其他业务错误：不重试
                    logger.error(f"Feishu send failed (code={code}): {result.get('msg')}")
                    return result

            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_exc = exc
                logger.warning(
                    f"Feishu network error (attempt {attempt}): {type(exc).__name__}: {exc}"
                )

            if attempt < _MAX_RETRIES:
                delay = (
                    self._backoff_override(attempt)
                    if self._backoff_override
                    else _BACKOFF_BASE * (2 ** (attempt - 1))
                )
                await asyncio.sleep(delay)

        logger.error(f"Feishu send failed after {_MAX_RETRIES} retries")
        if last_exc:
            raise last_exc
        raise RuntimeError("Feishu send failed for unknown reason")


def get_feishu_notifier() -> FeishuNotifier | EnterpriseFeishuNotifier | None:
    """从环境变量构造飞书 notifier（生产用），自动选择模式.

    优先级：
    1. 企业自建应用（FEISHU_APP_ID + FEISHU_APP_SECRET + FEISHU_CHAT_ID）
    2. 群自定义机器人（FEISHU_WEBHOOK_URL + 可选 FEISHU_SECRET）
    3. 都未配置返回 None
    """
    load_dotenv(override=True)

    # 模式 1：企业自建应用
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    chat_id = os.environ.get("FEISHU_CHAT_ID", "")
    if app_id and app_secret and chat_id:
        logger.info("Feishu notifier: enterprise bot mode")
        return EnterpriseFeishuNotifier(app_id, app_secret, chat_id)

    # 模式 2：群自定义机器人 webhook
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    if webhook_url:
        logger.info("Feishu notifier: custom webhook mode")
        secret = os.environ.get("FEISHU_SECRET") or None
        return FeishuNotifier(webhook_url, secret=secret)

    logger.warning("No Feishu notifier configured (need APP_ID/SECRET/CHAT_ID or WEBHOOK_URL)")
    return None
