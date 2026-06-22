"""LLM provider abstraction for the advisor layer.

Supports three OpenAI-compatible backends behind a single
:class:`LLMProvider` ABC:

================  ============================  ================
Provider          ``base_url``                 default model
================  ============================  ================
GLM (primary)     https://open.bigmodel.cn/... ``glm-5.2``
OpenAI (compat)   (library default)            ``gpt-4o-mini``
DeepSeek (compat) https://api.deepseek.com/v1  ``deepseek-chat``
================  ============================  ================

All three are driven through the ``openai`` Python library's
``OpenAI(api_key=..., base_url=...)`` client, which speaks the OpenAI
chat-completions wire format that every backend above implements.

Anti-hallucination contract
---------------------------
On ANY failure — network error, upstream 5xx, malformed response, exhausted
retries — :meth:`LLMProvider.complete` raises :class:`LLMUnavailableError`.
It NEVER returns fabricated, hardcoded, or empty-string content.  Callers
must catch the exception and apply their own deterministic fallback.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from stockhot.advisor.exceptions import LLMUnavailableError

# ── Defaults per provider ────────────────────────────────────────────────

_GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
_GLM_DEFAULT_MODEL = "glm-5.2"

_OPENAI_BASE_URL = "https://api.openai.com/v1"
_OPENAI_DEFAULT_MODEL = "gpt-4o-mini"

_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
_DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"

# ── Retry configuration ──────────────────────────────────────────────────

_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 1.0  # produces 1s, 2s, 4s delays


# ── Response dataclass ───────────────────────────────────────────────────


@dataclass(frozen=True)
class LLMResponse:
    """Structured result of a single :meth:`LLMProvider.complete` call."""

    content: str
    prompt_tokens: int
    completion_tokens: int
    model: str
    latency_ms: int


# ── Abstract base class ──────────────────────────────────────────────────


class LLMProvider(ABC):
    """Abstract LLM backend.

    Concrete subclasses own an ``openai.OpenAI`` client configured with the
    appropriate ``base_url`` / ``api_key`` / default ``model``.  The
    :meth:`complete` method is shared and implements retry + token/latency
    tracking uniformly.
    """

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self._model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    @property
    def model(self) -> str:
        return self._model

    @abstractmethod
    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 800,
        temperature: float = 0.3,
    ) -> LLMResponse:
        """Return the model's completion for ``prompt``.

        Raises:
            LLMUnavailableError: On any failure after all retries.
        """


# ── Concrete providers ───────────────────────────────────────────────────


class GLMProvider(LLMProvider):
    """Zhipu GLM via OpenAI-compatible endpoint (primary provider)."""

    def __init__(self, api_key: str, base_url: str | None = None, model: str | None = None) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url or _GLM_BASE_URL,
            model=model or _GLM_DEFAULT_MODEL,
        )

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 800,
        temperature: float = 0.3,
    ) -> LLMResponse:
        return _complete_with_retry(
            self._client, self._model, prompt, system, max_tokens, temperature
        )


class OpenAIProvider(LLMProvider):
    """OpenAI via the standard OpenAI API."""

    def __init__(self, api_key: str, base_url: str | None = None, model: str | None = None) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url or _OPENAI_BASE_URL,
            model=model or _OPENAI_DEFAULT_MODEL,
        )

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 800,
        temperature: float = 0.3,
    ) -> LLMResponse:
        return _complete_with_retry(
            self._client, self._model, prompt, system, max_tokens, temperature
        )


class DeepSeekProvider(LLMProvider):
    """DeepSeek via OpenAI-compatible endpoint."""

    def __init__(self, api_key: str, base_url: str | None = None, model: str | None = None) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url or _DEEPSEEK_BASE_URL,
            model=model or _DEEPSEEK_DEFAULT_MODEL,
        )

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 800,
        temperature: float = 0.3,
    ) -> LLMResponse:
        return _complete_with_retry(
            self._client, self._model, prompt, system, max_tokens, temperature
        )


# ── Shared completion + retry core ───────────────────────────────────────


def _complete_with_retry(
    client: OpenAI,
    model: str,
    prompt: str,
    system: str,
    max_tokens: int,
    temperature: float,
) -> LLMResponse:
    """Call ``client.chat.completions.create`` with exponential-backoff retry.

    Retry schedule: 3 attempts, sleeping 1s → 2s → 4s between failures.
    On the final failure (or any unrecoverable error) raises
    :class:`LLMUnavailableError` — never returns fabricated content.
    """
    if system:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
    else:
        messages = [{"role": "user", "content": prompt}]

    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        start = time.monotonic()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                backoff = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                time.sleep(backoff)
                continue
            raise LLMUnavailableError(
                f"LLM call failed after {_MAX_RETRIES} attempts: {exc}"
            ) from exc

        latency_ms = int((time.monotonic() - start) * 1000)

        if (
            response is None
            or not response.choices
            or response.choices[0].message is None
            or response.choices[0].message.content is None
        ):
            raise LLMUnavailableError(
                "LLM returned an empty or malformed response (no choices/content)"
            )

        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0

        return LLMResponse(
            content=response.choices[0].message.content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=model,
            latency_ms=latency_ms,
        )

    # Defensive: the loop above always either returns or raises.
    raise LLMUnavailableError(
        f"LLM call exhausted retries without returning: {last_exc}"
    )


# ── Factory ──────────────────────────────────────────────────────────────

_PROVIDERS = {
    "glm": GLMProvider,
    "openai": OpenAIProvider,
    "deepseek": DeepSeekProvider,
}


def get_provider() -> LLMProvider:
    """Return the :class:`LLMProvider` selected by environment variables.

    Reads (with sensible defaults):

    * ``LLM_PROVIDER``  — one of ``glm`` / ``openai`` / ``deepseek``
      (default ``glm``).
    * ``LLM_API_KEY``   — **required**.  Raises :class:`EnvironmentError`
      if missing or empty.
    * ``LLM_BASE_URL``  — optional override for the provider endpoint.
    * ``LLM_MODEL``     — optional override for the model name.

    Raises:
        EnvironmentError: ``LLM_API_KEY`` is missing or empty.
        ValueError: ``LLM_PROVIDER`` is not one of the supported names.
    """
    provider_name = (os.environ.get("LLM_PROVIDER", "") or "glm").strip().lower()
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    base_url = os.environ.get("LLM_BASE_URL", "").strip() or None
    model = os.environ.get("LLM_MODEL", "").strip() or None

    if not api_key:
        raise EnvironmentError(
            "LLM_API_KEY is not set. Configure it in .env or export it "
            "as an environment variable."
        )

    cls = _PROVIDERS.get(provider_name)
    if cls is None:
        raise ValueError(
            f"Unsupported LLM_PROVIDER='{provider_name}'. "
            f"Supported: {sorted(_PROVIDERS)}"
        )

    return cls(api_key=api_key, base_url=base_url, model=model)
