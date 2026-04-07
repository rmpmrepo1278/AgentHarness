"""OpenRouter cloud LLM provider — thin wrapper over OpenAI-compatible API."""
from __future__ import annotations

from typing import Any

from core.providers.openai_compat import OpenAICompatProvider


class OpenRouterProvider(OpenAICompatProvider):
    """OpenRouter inference API."""

    def __init__(self, **kwargs: Any) -> None:
        defaults: dict[str, Any] = {
            "name": "openrouter",
            "endpoint": "https://openrouter.ai/api/v1/chat/completions",
            "env_key": "OPENROUTER_API_KEY",
            "model": "meta-llama/llama-3.3-70b-instruct:free",
            "daily_limit": 50,
        }
        defaults.update(kwargs)
        super().__init__(**defaults)
