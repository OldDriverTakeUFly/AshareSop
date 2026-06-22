"""Telegram Bot API notification module.

Push-only: sends AI trading recommendations via Telegram Bot API.
Does NOT accept commands from unauthorized users — users in the
``TELEGRAM_ALLOWED_USER_IDS`` allowlist are the only ones recognised.

Uses raw ``httpx`` calls (no python-telegram-bot dependency).
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Any, Callable

import httpx
from loguru import logger

_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0
_STOCKS_PER_MESSAGE = 20
_URGENT_ACTIONS = {"EXIT"}
_URGENT_CONFIDENCE = {"HIGH"}


class TelegramNotifier:
    """Push notifications to a Telegram chat via the Bot API.

    The notifier is constructed with explicit credentials so it can be
    unit-tested without touching the environment.  For test-time HTTP
    mocking pass ``_transport=httpx.MockTransport(handler)``.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        allowed_user_ids: list[int],
        *,
        _transport: httpx.AsyncBaseTransport | None = None,
        _backoff_override: Callable[[int], float] | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._allowed = set(allowed_user_ids)
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self._transport = _transport
        self._backoff_override = _backoff_override

    async def send_message(
        self, text: str, parse_mode: str = "Markdown"
    ) -> dict[str, Any]:
        """Send a single message via the Telegram Bot API.

        Retries up to 3 times. On a 429 the ``retry_after`` field from the
        response body is honoured instead of exponential backoff.

        Raises:
            httpx.HTTPStatusError: on final failure after all retries.
        """
        url = f"{self._base_url}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }

        last_response: httpx.Response | None = None
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(transport=self._transport) as client:
                    resp = await client.post(url, json=payload)
            except Exception as exc:
                last_exc = exc
                last_response = None
                if attempt < _MAX_RETRIES:
                    backoff = self._calc_backoff(attempt)
                    logger.warning(
                        "Telegram send error attempt {}/{}: {} — retrying in {:.1f}s",
                        attempt,
                        _MAX_RETRIES,
                        exc,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                break
            else:
                if resp.is_success:
                    return resp.json()

                last_response = resp
                last_exc = None

                if resp.status_code == 429:
                    sleep_time = _extract_retry_after(resp)
                    logger.warning(
                        "Telegram 429 rate-limit (attempt {}/{}), sleeping {}s",
                        attempt,
                        _MAX_RETRIES,
                        sleep_time,
                    )
                else:
                    sleep_time = self._calc_backoff(attempt)
                    logger.warning(
                        "Telegram HTTP {} attempt {}/{}, retrying in {:.1f}s",
                        resp.status_code,
                        attempt,
                        _MAX_RETRIES,
                        sleep_time,
                    )

                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(sleep_time)
                    continue

        logger.error("Telegram send_message failed after {} retries", _MAX_RETRIES)
        if last_response is not None:
            last_response.raise_for_status()
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("unreachable")

    async def send_recommendations_batch(
        self, recommendations: list[dict], max_messages: int = 5
    ) -> list[dict[str, Any]]:
        """Send recommendations as a sequence of batched messages.

        Urgent recommendations (``action`` in ``{EXIT}`` or ``confidence``
        equal to ``HIGH``) get their own message and are sent **first**.
        Remaining recommendations are grouped (max 20 stocks per message).

        The total number of messages is capped at *max_messages* (default 5).
        Returns a list of API response dicts.
        """
        if not recommendations:
            return []

        urgent, normal = _partition_urgent(recommendations)
        responses: list[dict[str, Any]] = []
        today = date.today().isoformat()

        for rec in urgent:
            if len(responses) >= max_messages:
                break
            text = _format_single_urgent(rec, today)
            resp = await self.send_message(text)
            responses.append(resp)

        for i in range(0, len(normal), _STOCKS_PER_MESSAGE):
            if len(responses) >= max_messages:
                break
            chunk = normal[i : i + _STOCKS_PER_MESSAGE]
            text = _format_normal_batch(chunk, today)
            resp = await self.send_message(text)
            responses.append(resp)

        return responses

    def verify_user(self, user_id: int) -> bool:
        """Return ``True`` if *user_id* is in the allowlist."""
        return user_id in self._allowed

    def _calc_backoff(self, attempt: int) -> float:
        if self._backoff_override is not None:
            return self._backoff_override(attempt)
        return _BACKOFF_BASE * (2 ** (attempt - 1))


# ── Helpers ────


def _extract_retry_after(resp: httpx.Response) -> float:
    """Read ``retry_after`` from a 429 response, default 1 second."""
    try:
        data = resp.json()
        return float(data.get("parameters", {}).get("retry_after", 1))
    except (ValueError, TypeError):
        return 1.0


def _is_urgent(rec: dict) -> bool:
    """A recommendation is urgent if action=EXIT or confidence=HIGH."""
    action = str(rec.get("action", "")).upper()
    confidence = str(rec.get("confidence", "")).upper()
    return action in _URGENT_ACTIONS or confidence in _URGENT_CONFIDENCE


def _partition_urgent(
    recs: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Split recommendations into (urgent, normal) preserving order."""
    urgent = [r for r in recs if _is_urgent(r)]
    normal = [r for r in recs if not _is_urgent(r)]
    return urgent, normal


def _format_single_urgent(rec: dict, today: str) -> str:
    """Format an urgent recommendation as its own message."""
    code = rec.get("code", "—")
    action = rec.get("action", "—")
    confidence = rec.get("confidence", "—")
    reason = rec.get("reason", "—")
    return (
        f"🚨 **紧急建议** ({today})\n\n"
        f"| 代码 | 操作 | 置信度 | 理由 |\n"
        f"|------|------|--------|------|\n"
        f"| {code} | {action} | {confidence} | {reason} |"
    )


def _format_normal_batch(recs: list[dict], today: str) -> str:
    """Format a batch of non-urgent recommendations as a Markdown table."""
    lines = [
        f"📊 **交易建议** ({today})\n",
        "| 代码 | 操作 | 置信度 | 理由 |",
        "|------|------|--------|------|",
    ]
    for r in recs:
        code = r.get("code", "—")
        action = r.get("action", "—")
        confidence = r.get("confidence", "—")
        reason = r.get("reason", "—")
        lines.append(f"| {code} | {action} | {confidence} | {reason} |")
    return "\n".join(lines)


# ── Config ────


def get_telegram_config() -> tuple[str, str, list[int]]:
    """Read Telegram config from environment variables.

    Returns:
        ``(bot_token, chat_id, allowed_user_ids)``.

    Raises:
        EnvironmentError: if any required variable is missing or empty.
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    allowed_str = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()

    if not bot_token:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN not set")
    if not chat_id:
        raise EnvironmentError("TELEGRAM_CHAT_ID not set")
    if not allowed_str:
        raise EnvironmentError("TELEGRAM_ALLOWED_USER_IDS not set")

    allowed_ids = [int(x.strip()) for x in allowed_str.split(",") if x.strip()]
    return bot_token, chat_id, allowed_ids
