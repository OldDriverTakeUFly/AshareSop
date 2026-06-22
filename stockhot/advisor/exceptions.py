"""Advisor-layer exceptions.

A single exception type — :class:`LLMUnavailableError` — is raised whenever
an LLM API call cannot be completed successfully (network failure, upstream
error, all retries exhausted, malformed response, etc.).

Design rule (anti-hallucination): the provider layer MUST NEVER return
fabricated, mocked, or default content on failure.  Callers must always see a
clean :class:`LLMUnavailableError` so they can fall back to deterministic
logic rather than silently consuming fake AI output.
"""


class LLMUnavailableError(Exception):
    """Raised when the LLM API cannot produce a valid response.

    Causes include (but are not limited to):

    * Network / connection errors after all retries.
    * Upstream HTTP errors (5xx, rate-limit, authentication failure).
    * Empty or malformed response body.
    * Any unexpected exception raised by the underlying ``openai`` client.

    The exception message is safe to surface to the operator; it never
    contains secrets such as API keys.
    """
