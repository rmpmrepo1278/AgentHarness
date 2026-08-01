from __future__ import annotations

import os
from typing import Any

from core.providers.openai_compat import OpenAICompatProvider


class CloudflareProvider(OpenAICompatProvider):
    """Cloudflare Workers AI — OpenAI-compatible endpoint.

    Uses CLOUDFLARE_API_TOKEN as bearer token and CLOUDFLARE_ACCOUNT_ID
    to construct the endpoint URL.
    """

    def __init__(self, **kwargs: Any) -> None:
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        defaults: dict[str, Any] = {
            "name": "cloudflare",
            "endpoint": (
                f"https://api.cloudflare.com/client/v4/accounts/"
                f"{account_id}/ai/v1/chat/completions"
            ),
            "env_key": "CLOUDFLARE_API_TOKEN",
            "model": "@cf/meta/llama-3.2-3b-instruct",
            "daily_limit": 50000,
        }
        defaults.update(kwargs)
        super().__init__(**defaults)
