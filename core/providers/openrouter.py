from __future__ import annotations
from typing import Any
import httpx
import time
import logging
from core.providers.openai_compat import OpenAICompatProvider
from core.providers.base import LLMRequest, LLMResponse

logger = logging.getLogger(__name__)

class OpenRouterProvider(OpenAICompatProvider):
    def __init__(self, **kwargs: Any) -> None:
        defaults: dict[str, Any] = {
            "name": "openrouter",
            "endpoint": "https://openrouter.ai/api/v1/chat/completions",
            "env_key": "OPENROUTER_API_KEY",
            "model": "poolside/laguna-m.1:free",
            "daily_limit": 1000,
        }
        defaults.update(kwargs)
        super().__init__(**defaults)
        logger.info(f"OpenRouterProvider initialized with model {self.model}")

    def complete(self, request: LLMRequest) -> LLMResponse:
        logger.info(f"OpenRouterProvider.complete called for model {self.model}")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "AgentHarness",
            "HTTP-Referer": "http://localhost:8080",
        }
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            # Force poolside provider
            "providers": None
        }
        
        # If model is NOT poolside, remove provider restriction
        if False:
            payload.pop("providers")

        t0 = time.monotonic()
        try:
            logger.info(f"Sending request to OpenRouter: {self.model}")
            resp = httpx.post(self.endpoint, json=payload, headers=headers, timeout=self.timeout)
            latency = (time.monotonic() - t0) * 1000
            
            if resp.status_code != 200:
                logger.warning(f"OpenRouter returned {resp.status_code}: {resp.text}")
                return LLMResponse(text="", provider=self.name, model=self.model, success=False, error=f"HTTP {resp.status_code}: {resp.text}")
            
            data = resp.json()
            choice = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            self._usage_today += 1
            logger.info(f"OpenRouter success: {len(choice)} chars")
            return LLMResponse(text=choice, provider=self.name, model=self.model, tokens_in=usage.get("prompt_tokens", 0), tokens_out=usage.get("completion_tokens", 0), latency_ms=latency)
        except Exception as e:
            logger.error(f"OpenRouter exception: {str(e)}")
            return LLMResponse(text="", provider=self.name, model=self.model, success=False, error=str(e))
