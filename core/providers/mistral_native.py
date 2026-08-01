from __future__ import annotations

from typing import Any

from core.providers.openai_compat import OpenAICompatProvider


class MistralNativeProvider(OpenAICompatProvider):
    """Mistral AI (Le Plateforme) — OpenAI-compatible, free tier available.

    Uses the native Mistral API (not OpenRouter). Free tier models:
    open-mistral-nemo, mistral-small-latest, codestral.
    """

    def __init__(self, **kwargs: Any) -> None:
        defaults: dict[str, Any] = {
            "name": "mistral-native",
            "endpoint": "https://api.mistral.ai/v1/chat/completions",
            "env_key": "MISTRAL_API_KEY",
            "model": "open-mistral-nemo",
            "daily_limit": 50000,
        }
        defaults.update(kwargs)
        super().__init__(**defaults)
