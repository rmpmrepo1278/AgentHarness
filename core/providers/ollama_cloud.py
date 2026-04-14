"""Ollama Cloud LLM provider — free-tier access to large cloud models."""
from __future__ import annotations

from typing import Any

from core.providers.openai_compat import OpenAICompatProvider


class OllamaCloudProvider(OpenAICompatProvider):
    """Ollama Cloud inference API (OpenAI-compatible, Bearer auth)."""

    def __init__(self, **kwargs: Any) -> None:
        defaults: dict[str, Any] = {
            "name": "ollama_cloud",
            "endpoint": "https://ollama.com/v1/chat/completions",
            "env_key": "OLLAMA_API_KEY",
            "model": "deepseek-v3.1:671b-cloud",
            "daily_limit": 200,
            "timeout": 60.0,
        }
        defaults.update(kwargs)
        super().__init__(**defaults)
