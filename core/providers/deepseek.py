from __future__ import annotations

from typing import Any

from core.providers.openai_compat import OpenAICompatProvider


class DeepSeekProvider(OpenAICompatProvider):
    """DeepSeek API — OpenAI-compatible, free tier available."""

    def __init__(self, **kwargs: Any) -> None:
        defaults: dict[str, Any] = {
            "name": "deepseek",
            "endpoint": "https://api.deepseek.com/v1/chat/completions",
            "env_key": "DEEPSEEK_API_KEY",
            "model": "deepseek-chat",
            "daily_limit": 50000,
        }
        defaults.update(kwargs)
        super().__init__(**defaults)
