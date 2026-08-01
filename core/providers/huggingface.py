from __future__ import annotations

from typing import Any

from core.providers.openai_compat import OpenAICompatProvider


class HuggingFaceProvider(OpenAICompatProvider):
    """HuggingFace Inference API — OpenAI-compatible endpoint.

    Uses the hosted chat completions endpoint with a free API key.
    """

    def __init__(self, **kwargs: Any) -> None:
        defaults: dict[str, Any] = {
            "name": "huggingface",
            "endpoint": "https://api-inference.huggingface.co/v1/chat/completions",
            "env_key": "HUGGINGFACE_API_KEY",
            "model": "microsoft/Phi-3-mini-4k-instruct",
            "daily_limit": 50000,
        }
        defaults.update(kwargs)
        super().__init__(**defaults)
