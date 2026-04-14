"""Cerebras cloud LLM provider — thin wrapper over OpenAI-compatible API."""
from __future__ import annotations

from typing import Any

from core.providers.openai_compat import OpenAICompatProvider


class CerebrasProvider(OpenAICompatProvider):
    """Cerebras inference API."""

    def __init__(self, **kwargs: Any) -> None:
        defaults: dict[str, Any] = {
            "name": "cerebras",
            "endpoint": "https://api.cerebras.ai/v1/chat/completions",
            "env_key": "CEREBRAS_API_KEY",
            "model": "qwen-3-235b-a22b-instruct-2507",
            "daily_limit": 1000,
        }
        defaults.update(kwargs)
        super().__init__(**defaults)
