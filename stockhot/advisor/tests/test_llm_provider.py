"""TDD tests for the LLM provider abstraction layer.

Covers:
    * All 3 providers (GLM / OpenAI / DeepSeek) return a correctly-populated
      :class:`LLMResponse` when the underlying ``openai`` client is mocked.
    * Token tracking: ``response.usage`` → ``prompt_tokens`` / ``completion_tokens``.
    * Latency tracking: ``latency_ms`` is a non-negative integer.
    * Retry: first N-1 attempts raise, final attempt succeeds → success.
    * Retry exhausted: all attempts fail → :class:`LLMUnavailableError`.
    * Anti-hallucination: no fabricated content on failure.
    * Factory ``get_provider()``: returns the right concrete class per
      ``LLM_PROVIDER``; raises :class:`EnvironmentError` when
      ``LLM_API_KEY`` is missing.
    * Default base_url / model wiring per provider.

The ``openai.OpenAI`` client is mocked at the module level so no real
network calls are made.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from stockhot.advisor import (
    DeepSeekProvider,
    GLMProvider,
    LLMProvider,
    LLMResponse,
    LLMUnavailableError,
    OpenAIProvider,
    get_provider,
)
import stockhot.advisor.llm_provider as lp_module


# ── Fixtures / helpers ───────────────────────────────────────────────────


def _make_mock_response(
    content: str = "mocked completion",
    prompt_tokens: int = 42,
    completion_tokens: int = 13,
):
    """Build an object shaped like ``openai``'s ``ChatCompletion``."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = prompt_tokens
    resp.usage.completion_tokens = completion_tokens
    return resp


def _build_provider(cls, monkeypatch, **ctor_kwargs):
    """Instantiate a provider while preventing a real ``OpenAI`` client.

    ``OpenAI.__init__`` performs no network I/O, but we patch it so the
    tests don't depend on having a valid api_key format and so we can
    capture the constructor arguments.
    """
    captured: dict = {}

    def fake_openai_init(self, api_key=None, base_url=None, **kw):
        captured["api_key"] = api_key
        captured["base_url"] = base_url
        captured.update(kw)
        self.chat = MagicMock()
        self.models = MagicMock()

    monkeypatch.setattr(lp_module.OpenAI, "__init__", fake_openai_init)
    provider = cls(api_key="test-key", **ctor_kwargs)
    return provider, captured


# ── LLMResponse dataclass ────────────────────────────────────────────────


class TestLLMResponse:
    def test_is_frozen(self):
        resp = LLMResponse(
            content="x", prompt_tokens=1, completion_tokens=2,
            model="m", latency_ms=10,
        )
        with pytest.raises(Exception):
            resp.content = "y"  # type: ignore[misc]

    def test_fields(self):
        resp = LLMResponse(
            content="hello", prompt_tokens=5, completion_tokens=7,
            model="glm-5.2", latency_ms=123,
        )
        assert resp.content == "hello"
        assert resp.prompt_tokens == 5
        assert resp.completion_tokens == 7
        assert resp.model == "glm-5.2"
        assert resp.latency_ms == 123


# ── Provider success path ────────────────────────────────────────────────


class TestProviderSuccess:
    @pytest.mark.parametrize(
        "provider_cls",
        [GLMProvider, OpenAIProvider, DeepSeekProvider],
    )
    def test_complete_returns_llm_response(self, provider_cls, monkeypatch):
        provider, _ = _build_provider(provider_cls, monkeypatch)
        mock_resp = _make_mock_response(content="hi", prompt_tokens=10, completion_tokens=5)
        provider._client.chat.completions.create.return_value = mock_resp

        result = provider.complete(prompt="hello")

        assert isinstance(result, LLMResponse)
        assert result.content == "hi"
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 5
        assert result.model == provider.model

    @pytest.mark.parametrize(
        "provider_cls",
        [GLMProvider, OpenAIProvider, DeepSeekProvider],
    )
    def test_token_tracking(self, provider_cls, monkeypatch):
        provider, _ = _build_provider(provider_cls, monkeypatch)
        mock_resp = _make_mock_response(
            prompt_tokens=77, completion_tokens=33,
        )
        provider._client.chat.completions.create.return_value = mock_resp

        result = provider.complete(prompt="x")

        assert result.prompt_tokens == 77
        assert result.completion_tokens == 33

    @pytest.mark.parametrize(
        "provider_cls",
        [GLMProvider, OpenAIProvider, DeepSeekProvider],
    )
    def test_latency_tracking(self, provider_cls, monkeypatch):
        provider, _ = _build_provider(provider_cls, monkeypatch)
        provider._client.chat.completions.create.return_value = _make_mock_response()

        result = provider.complete(prompt="x")

        assert isinstance(result.latency_ms, int)
        assert result.latency_ms >= 0

    def test_system_message_passed_through(self, monkeypatch):
        provider, _ = _build_provider(GLMProvider, monkeypatch)
        provider._client.chat.completions.create.return_value = _make_mock_response()

        provider.complete(prompt="user-msg", system="sys-msg")

        call_kwargs = provider._client.chat.completions.create.call_args.kwargs
        messages = call_kwargs["messages"]
        assert messages[0] == {"role": "system", "content": "sys-msg"}
        assert messages[1] == {"role": "user", "content": "user-msg"}

    def test_no_system_message_omits_system_role(self, monkeypatch):
        provider, _ = _build_provider(GLMProvider, monkeypatch)
        provider._client.chat.completions.create.return_value = _make_mock_response()

        provider.complete(prompt="user-msg")

        call_kwargs = provider._client.chat.completions.create.call_args.kwargs
        messages = call_kwargs["messages"]
        assert all(m["role"] != "system" for m in messages)
        assert messages == [{"role": "user", "content": "user-msg"}]

    def test_max_tokens_and_temperature_forwarded(self, monkeypatch):
        provider, _ = _build_provider(GLMProvider, monkeypatch)
        provider._client.chat.completions.create.return_value = _make_mock_response()

        provider.complete(prompt="x", max_tokens=512, temperature=0.7)

        call_kwargs = provider._client.chat.completions.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 512
        assert call_kwargs["temperature"] == 0.7


# ── Retry logic ──────────────────────────────────────────────────────────


class TestRetry:
    def test_retry_succeeds_on_third_attempt(self, monkeypatch):
        provider, _ = _build_provider(GLMProvider, monkeypatch)
        mock_resp = _make_mock_response(content="third-time")
        provider._client.chat.completions.create.side_effect = [
            RuntimeError("boom-1"),
            RuntimeError("boom-2"),
            mock_resp,
        ]
        monkeypatch.setattr(lp_module.time, "sleep", lambda s: None)

        result = provider.complete(prompt="x")

        assert result.content == "third-time"
        assert provider._client.chat.completions.create.call_count == 3

    def test_retry_exhausted_raises_llm_unavailable(self, monkeypatch):
        provider, _ = _build_provider(GLMProvider, monkeypatch)
        provider._client.chat.completions.create.side_effect = RuntimeError("always-boom")
        monkeypatch.setattr(lp_module.time, "sleep", lambda s: None)

        with pytest.raises(LLMUnavailableError):
            provider.complete(prompt="x")

        assert provider._client.chat.completions.create.call_count == 3

    def test_retry_backoff_schedule(self, monkeypatch):
        provider, _ = _build_provider(GLMProvider, monkeypatch)
        provider._client.chat.completions.create.side_effect = RuntimeError("boom")
        sleeps: list[float] = []
        monkeypatch.setattr(lp_module.time, "sleep", lambda s: sleeps.append(s))

        with pytest.raises(LLMUnavailableError):
            provider.complete(prompt="x")

        # 3 attempts → 2 sleeps between them: 1s, 2s
        assert sleeps == [1.0, 2.0]

    def test_first_attempt_success_no_sleep(self, monkeypatch):
        provider, _ = _build_provider(GLMProvider, monkeypatch)
        provider._client.chat.completions.create.return_value = _make_mock_response()
        slept: list[float] = []
        monkeypatch.setattr(lp_module.time, "sleep", lambda s: slept.append(s))

        provider.complete(prompt="x")

        assert slept == []


# ── Anti-hallucination ───────────────────────────────────────────────────


class TestAntiHallucination:
    def test_no_fabricated_content_on_failure(self, monkeypatch):
        provider, _ = _build_provider(GLMProvider, monkeypatch)
        provider._client.chat.completions.create.side_effect = RuntimeError("network-down")
        monkeypatch.setattr(lp_module.time, "sleep", lambda s: None)

        with pytest.raises(LLMUnavailableError) as exc_info:
            provider.complete(prompt="x")

        msg = str(exc_info.value).lower()
        assert "network-down" in msg or "failed" in msg

    def test_empty_response_raises(self, monkeypatch):
        provider, _ = _build_provider(GLMProvider, monkeypatch)
        bad_resp = MagicMock()
        bad_resp.choices = []
        provider._client.chat.completions.create.return_value = bad_resp

        with pytest.raises(LLMUnavailableError):
            provider.complete(prompt="x")

    def test_none_content_raises(self, monkeypatch):
        provider, _ = _build_provider(GLMProvider, monkeypatch)
        bad_resp = MagicMock()
        bad_resp.choices = [MagicMock()]
        bad_resp.choices[0].message.content = None
        provider._client.chat.completions.create.return_value = bad_resp

        with pytest.raises(LLMUnavailableError):
            provider.complete(prompt="x")

    def test_missing_usage_defaults_to_zero(self, monkeypatch):
        provider, _ = _build_provider(GLMProvider, monkeypatch)
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "ok"
        resp.usage = None
        provider._client.chat.completions.create.return_value = resp

        result = provider.complete(prompt="x")

        assert result.prompt_tokens == 0
        assert result.completion_tokens == 0
        assert result.content == "ok"


# ── Default base_url / model wiring ──────────────────────────────────────


class TestProviderDefaults:
    def test_glm_defaults(self, monkeypatch):
        _, captured = _build_provider(GLMProvider, monkeypatch)
        assert captured["base_url"] == "https://open.bigmodel.cn/api/paas/v4/"

    def test_glm_default_model(self, monkeypatch):
        provider, _ = _build_provider(GLMProvider, monkeypatch)
        assert provider.model == "glm-5.2"

    def test_openai_defaults(self, monkeypatch):
        _, captured = _build_provider(OpenAIProvider, monkeypatch)
        assert captured["base_url"] == "https://api.openai.com/v1"

    def test_openai_default_model(self, monkeypatch):
        provider, _ = _build_provider(OpenAIProvider, monkeypatch)
        assert provider.model == "gpt-4o-mini"

    def test_deepseek_defaults(self, monkeypatch):
        _, captured = _build_provider(DeepSeekProvider, monkeypatch)
        assert captured["base_url"] == "https://api.deepseek.com/v1"

    def test_deepseek_default_model(self, monkeypatch):
        provider, _ = _build_provider(DeepSeekProvider, monkeypatch)
        assert provider.model == "deepseek-chat"

    def test_base_url_override(self, monkeypatch):
        provider, captured = _build_provider(
            GLMProvider, monkeypatch, base_url="https://custom.example.com/v1",
        )
        assert captured["base_url"] == "https://custom.example.com/v1"

    def test_model_override(self, monkeypatch):
        provider, _ = _build_provider(GLMProvider, monkeypatch, model="glm-4-flash")
        assert provider.model == "glm-4-flash"


# ── Factory: get_provider() ──────────────────────────────────────────────


class TestGetProvider:
    _REQUIRED_ENV = {
        "LLM_API_KEY": "factory-key",
        "LLM_BASE_URL": "",
        "LLM_MODEL": "",
    }

    def _patch_openai(self, monkeypatch):
        def fake_openai_init(self, api_key=None, base_url=None, **kw):
            self.chat = MagicMock()
            self.models = MagicMock()
        monkeypatch.setattr(lp_module.OpenAI, "__init__", fake_openai_init)

    def test_default_is_glm(self, monkeypatch):
        self._patch_openai(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "")
        monkeypatch.setenv("LLM_API_KEY", "k")
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)

        provider = get_provider()

        assert isinstance(provider, GLMProvider)
        assert provider.model == "glm-5.2"

    def test_explicit_glm(self, monkeypatch):
        self._patch_openai(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "glm")
        monkeypatch.setenv("LLM_API_KEY", "k")

        assert isinstance(get_provider(), GLMProvider)

    def test_openai_provider(self, monkeypatch):
        self._patch_openai(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_API_KEY", "k")

        provider = get_provider()
        assert isinstance(provider, OpenAIProvider)
        assert provider.model == "gpt-4o-mini"

    def test_deepseek_provider(self, monkeypatch):
        self._patch_openai(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("LLM_API_KEY", "k")

        provider = get_provider()
        assert isinstance(provider, DeepSeekProvider)
        assert provider.model == "deepseek-chat"

    def test_missing_api_key_raises_environment_error(self, monkeypatch):
        self._patch_openai(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "glm")
        monkeypatch.setenv("LLM_API_KEY", "")

        with pytest.raises(EnvironmentError):
            get_provider()

    def test_api_key_unset_raises_environment_error(self, monkeypatch):
        self._patch_openai(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "glm")
        monkeypatch.delenv("LLM_API_KEY", raising=False)

        with pytest.raises(EnvironmentError):
            get_provider()

    def test_invalid_provider_raises_value_error(self, monkeypatch):
        self._patch_openai(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "claude")
        monkeypatch.setenv("LLM_API_KEY", "k")

        with pytest.raises(ValueError):
            get_provider()

    def test_model_override_via_env(self, monkeypatch):
        self._patch_openai(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "glm")
        monkeypatch.setenv("LLM_API_KEY", "k")
        monkeypatch.setenv("LLM_MODEL", "glm-4-flash")

        provider = get_provider()
        assert provider.model == "glm-4-flash"

    def test_provider_name_case_insensitive(self, monkeypatch):
        self._patch_openai(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "OpenAI")
        monkeypatch.setenv("LLM_API_KEY", "k")

        assert isinstance(get_provider(), OpenAIProvider)


# ── ABC contract ─────────────────────────────────────────────────────────


class TestABCContract:
    def test_cannot_instantiate_abc_directly(self):
        with pytest.raises(TypeError):
            LLMProvider(api_key="k", base_url="x", model="m")  # type: ignore[abstract]
