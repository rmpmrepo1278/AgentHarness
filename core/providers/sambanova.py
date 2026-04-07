"""SambaNova cloud LLM provider — thin wrapper over OpenAI-compatible API."""
from __future__ import annotations

from typing import Any

from core.providers.openai_compat import OpenAICompatProvider


class SambaNovaProvider(OpenAICompatProvider):
    """SambaNova inference API."""

    def __init__(self, **kwargs: Any) -> None:
        defaults: dict[str, Any] = {
            "name": "sambanova",
            "endpoint": "https://api.sambanova.ai/v1/chat/completions",
            "env_key": "SAMBANOVA_API_KEY",
            "model": "Meta-Llama-3.3-70B-Instruct",
            "daily_limit": 500,
        }
        defaults.update(kwargs)
        super().__init__(**defaults)
