"""AI trading advisor — LLM-backed analysis modules.

Public API:

    from stockhot.advisor import get_provider, LLMProvider, LLMResponse

The :mod:`llm_provider` sub-module implements the provider abstraction;
importers should use the re-exports below rather than reaching into the
sub-module directly.
"""

from stockhot.advisor.exceptions import LLMUnavailableError
from stockhot.advisor.llm_provider import (
    DeepSeekProvider,
    GLMProvider,
    LLMProvider,
    LLMResponse,
    OpenAIProvider,
    get_provider,
)

__all__ = [
    "DeepSeekProvider",
    "GLMProvider",
    "LLMProvider",
    "LLMResponse",
    "LLMUnavailableError",
    "OpenAIProvider",
    "get_provider",
]
