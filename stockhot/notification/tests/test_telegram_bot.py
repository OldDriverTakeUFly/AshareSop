"""Tests for the Telegram Bot notification module.

All HTTP calls are mocked via ``httpx.MockTransport`` — no real network
traffic occurs.
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import httpx
import pytest

from stockhot.notification.telegram_bot import (
    TelegramNotifier,
    get_telegram_config,
)

# ── send_message success ────


class TestSendMessage:
    """Tests for ``TelegramNotifier.send_message``."""

    @pytest.mark.asyncio
    async def test_success_returns_response_dict(self) -> None:
        """Mock returns 200 → assert response dict correct."""
        result_body = {
            "ok": True,
            "result": {
                "message_id": 42,
                "date": 1719043200,
                "chat": {"id": 123, "type": "private"},
                "text": "hello",
            },
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=result_body)

        notifier = TelegramNotifier(
            bot_token="fake-token",
            chat_id="123",
            allowed_user_ids=[123],
            _transport=httpx.MockTransport(handler),
        )

        result = await notifier.send_message("hello")
        assert result["ok"] is True
        assert result["result"]["message_id"] == 42

    @pytest.mark.asyncio
    async def test_success_sends_correct_payload(self) -> None:
        """Verify the outgoing request contains chat_id, text, parse_mode."""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"ok": True})

        notifier = TelegramNotifier(
            bot_token="abc123",
            chat_id="999",
            allowed_user_ids=[1],
            _transport=httpx.MockTransport(handler),
        )

        await notifier.send_message("test message", parse_mode="Markdown")
        assert "botabc123/sendMessage" in captured["url"]
        assert captured["body"]["chat_id"] == "999"
        assert captured["body"]["text"] == "test message"
        assert captured["body"]["parse_mode"] == "Markdown"


# ── 429 retry ────


class TestRateLimitRetry:
    """Tests for 429 rate-limit handling with ``retry_after``."""

    @pytest.mark.asyncio
    async def test_429_then_success(self) -> None:
        """Mock returns 429 (retry_after=1) then 200 → assert success."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    429,
                    json={
                        "ok": False,
                        "error_code": 429,
                        "parameters": {"retry_after": 1},
                    },
                )
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        notifier = TelegramNotifier(
            bot_token="t",
            chat_id="c",
            allowed_user_ids=[1],
            _transport=httpx.MockTransport(handler),
            _backoff_override=lambda _: 0,  # no real sleep for backoff
        )

        with patch("stockhot.notification.telegram_bot.asyncio.sleep") as mock_sleep:
            result = await notifier.send_message("hi")

        assert result["ok"] is True
        assert call_count == 2
        # Verify sleep was called with retry_after value (1) for the 429
        mock_sleep.assert_called_with(1)

    @pytest.mark.asyncio
    async def test_429_exhausted_raises(self) -> None:
        """Mock always returns 429 → assert exception after 3 retries."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "parameters": {"retry_after": 1},
                },
            )

        notifier = TelegramNotifier(
            bot_token="t",
            chat_id="c",
            allowed_user_ids=[1],
            _transport=httpx.MockTransport(handler),
        )

        with patch("stockhot.notification.telegram_bot.asyncio.sleep"):
            with pytest.raises(httpx.HTTPStatusError):
                await notifier.send_message("will fail")

    @pytest.mark.asyncio
    async def test_retry_count_is_three(self) -> None:
        """Verify exactly 3 attempts before giving up on persistent errors."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(500, json={"ok": False, "error_code": 500})

        notifier = TelegramNotifier(
            bot_token="t",
            chat_id="c",
            allowed_user_ids=[1],
            _transport=httpx.MockTransport(handler),
        )

        with patch("stockhot.notification.telegram_bot.asyncio.sleep"):
            with pytest.raises(httpx.HTTPStatusError):
                await notifier.send_message("fail")

        assert call_count == 3


# ── verify_user ────


class TestVerifyUser:
    """Tests for ``TelegramNotifier.verify_user``."""

    def test_allowed_user(self) -> None:
        """allowed=[123, 456], user_id=123 → True."""
        notifier = TelegramNotifier(
            bot_token="t",
            chat_id="c",
            allowed_user_ids=[123, 456],
            _transport=httpx.MockTransport(lambda req: httpx.Response(200)),
        )
        assert notifier.verify_user(123) is True

    def test_denied_user(self) -> None:
        """user_id=999 → False."""
        notifier = TelegramNotifier(
            bot_token="t",
            chat_id="c",
            allowed_user_ids=[123, 456],
            _transport=httpx.MockTransport(lambda req: httpx.Response(200)),
        )
        assert notifier.verify_user(999) is False

    def test_empty_allowlist_denies_all(self) -> None:
        """Empty allowlist → all users denied."""
        notifier = TelegramNotifier(
            bot_token="t",
            chat_id="c",
            allowed_user_ids=[],
            _transport=httpx.MockTransport(lambda req: httpx.Response(200)),
        )
        assert notifier.verify_user(1) is False


# ── batch ────


def _make_rec(
    code: str,
    action: str = "BUY",
    confidence: str = "MEDIUM",
    reason: str = "理由",
) -> dict:
    """Build a minimal recommendation dict."""
    return {"code": code, "action": action, "confidence": confidence, "reason": reason}


class TestSendRecommendationsBatch:
    """Tests for ``send_recommendations_batch``."""

    @pytest.mark.asyncio
    async def test_batch_10_recs_is_one_message(self) -> None:
        """10 recommendations (< 20) → 1 non-urgent message."""
        recs = [_make_rec(f"00000{i}") for i in range(10)]
        sent_texts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            sent_texts.append(body["text"])
            return httpx.Response(200, json={"ok": True})

        notifier = TelegramNotifier(
            bot_token="t",
            chat_id="c",
            allowed_user_ids=[1],
            _transport=httpx.MockTransport(handler),
        )
        results = await notifier.send_recommendations_batch(recs)

        assert len(results) == 1
        assert len(sent_texts) == 1
        assert results[0]["ok"] is True

    @pytest.mark.asyncio
    async def test_batch_100_recs_capped_at_max(self) -> None:
        """100 recommendations → capped at 5 messages."""
        recs = [_make_rec(f"{i:06d}") for i in range(100)]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        notifier = TelegramNotifier(
            bot_token="t",
            chat_id="c",
            allowed_user_ids=[1],
            _transport=httpx.MockTransport(handler),
        )
        results = await notifier.send_recommendations_batch(recs, max_messages=5)

        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_urgent_sent_first(self) -> None:
        """action=EXIT recommendations sent before non-urgent ones."""
        recs = [
            _make_rec("000001", action="BUY"),
            _make_rec("000002", action="EXIT"),
            _make_rec("000003", action="BUY"),
            _make_rec("000004", action="BUY"),
            _make_rec("000005", action="BUY"),
        ]
        sent_texts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            sent_texts.append(body["text"])
            return httpx.Response(200, json={"ok": True})

        notifier = TelegramNotifier(
            bot_token="t",
            chat_id="c",
            allowed_user_ids=[1],
            _transport=httpx.MockTransport(handler),
        )
        await notifier.send_recommendations_batch(recs)

        # First message should be the urgent one (EXIT)
        assert "紧急" in sent_texts[0]
        assert "000002" in sent_texts[0]
        # Remaining messages should be non-urgent
        assert "交易建议" in sent_texts[1]

    @pytest.mark.asyncio
    async def test_high_confidence_is_urgent(self) -> None:
        """confidence=HIGH also treated as urgent."""
        recs = [
            _make_rec("000001", confidence="HIGH"),
            _make_rec("000002", confidence="LOW"),
        ]
        sent_texts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            sent_texts.append(body["text"])
            return httpx.Response(200, json={"ok": True})

        notifier = TelegramNotifier(
            bot_token="t",
            chat_id="c",
            allowed_user_ids=[1],
            _transport=httpx.MockTransport(handler),
        )
        await notifier.send_recommendations_batch(recs)

        assert "紧急" in sent_texts[0]
        assert "000001" in sent_texts[0]

    @pytest.mark.asyncio
    async def test_more_than_20_grouped(self) -> None:
        """25 recommendations → urgent-free, split into 2 messages (20+5)."""
        recs = [_make_rec(f"{i:06d}") for i in range(25)]
        sent_texts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            sent_texts.append(body["text"])
            return httpx.Response(200, json={"ok": True})

        notifier = TelegramNotifier(
            bot_token="t",
            chat_id="c",
            allowed_user_ids=[1],
            _transport=httpx.MockTransport(handler),
        )
        results = await notifier.send_recommendations_batch(recs)

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_empty_batch_returns_empty(self) -> None:
        """Empty recommendations list → no messages sent."""
        notifier = TelegramNotifier(
            bot_token="t",
            chat_id="c",
            allowed_user_ids=[1],
            _transport=httpx.MockTransport(lambda req: httpx.Response(200)),
        )
        results = await notifier.send_recommendations_batch([])
        assert results == []

    @pytest.mark.asyncio
    async def test_markdown_table_format(self) -> None:
        """Message body contains a Markdown table with header row."""
        recs = [_make_rec("000001", action="BUY", confidence="HIGH", reason="强势")]
        sent_text: str = ""

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal sent_text
            body = json.loads(request.content)
            sent_text = body["text"]
            return httpx.Response(200, json={"ok": True})

        notifier = TelegramNotifier(
            bot_token="t",
            chat_id="c",
            allowed_user_ids=[1],
            _transport=httpx.MockTransport(handler),
        )
        await notifier.send_recommendations_batch(recs)

        assert "| 代码 |" in sent_text or "|代码|" in sent_text
        assert "000001" in sent_text


# ── get_telegram_config ────


class TestGetTelegramConfig:
    """Tests for ``get_telegram_config()``."""

    def test_missing_token_raises(self) -> None:
        """TELEGRAM_BOT_TOKEN="" → EnvironmentError."""
        env = {
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "123",
            "TELEGRAM_ALLOWED_USER_IDS": "1,2",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(EnvironmentError, match="TELEGRAM_BOT_TOKEN"):
                get_telegram_config()

    def test_missing_chat_id_raises(self) -> None:
        """TELEGRAM_CHAT_ID="" → EnvironmentError."""
        env = {
            "TELEGRAM_BOT_TOKEN": "tok",
            "TELEGRAM_CHAT_ID": "",
            "TELEGRAM_ALLOWED_USER_IDS": "1,2",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(EnvironmentError, match="TELEGRAM_CHAT_ID"):
                get_telegram_config()

    def test_missing_allowed_raises(self) -> None:
        """TELEGRAM_ALLOWED_USER_IDS="" → EnvironmentError."""
        env = {
            "TELEGRAM_BOT_TOKEN": "tok",
            "TELEGRAM_CHAT_ID": "123",
            "TELEGRAM_ALLOWED_USER_IDS": "",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(EnvironmentError, match="TELEGRAM_ALLOWED_USER_IDS"):
                get_telegram_config()

    def test_all_present_returns_tuple(self) -> None:
        """All vars present → returns (token, chat_id, [ids])."""
        env = {
            "TELEGRAM_BOT_TOKEN": "bot123",
            "TELEGRAM_CHAT_ID": "-100123",
            "TELEGRAM_ALLOWED_USER_IDS": "111,222,333",
        }
        with patch.dict(os.environ, env, clear=False):
            token, chat_id, allowed = get_telegram_config()

        assert token == "bot123"
        assert chat_id == "-100123"
        assert allowed == [111, 222, 333]

    def test_allowed_ids_with_spaces(self) -> None:
        """Comma-separated list with spaces is parsed correctly."""
        env = {
            "TELEGRAM_BOT_TOKEN": "tok",
            "TELEGRAM_CHAT_ID": "1",
            "TELEGRAM_ALLOWED_USER_IDS": " 1 , 2 , 3 ",
        }
        with patch.dict(os.environ, env, clear=False):
            _, _, allowed = get_telegram_config()

        assert allowed == [1, 2, 3]
