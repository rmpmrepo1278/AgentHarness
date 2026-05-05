from __future__ import annotations
from typing import Any
import httpx
import time
import logging
import asyncio
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
        self._free_models = []
        self._last_refresh = 0
        logger.info(f"OpenRouterProvider initialized with model {self.model}")

    async def _refresh_free_models(self):
        """Fetch free models from OpenRouter API."""
        if time.time() - self._last_refresh < 3600:
            return
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get("https://openrouter.ai/api/v1/models")
                if resp.status_code == 200:
                    data = resp.json()
                    self._free_models = [
                        m["id"] for m in data.get("data", [])
                        if m.get("pricing", {}).get("prompt") == "0" 
                        and m.get("pricing", {}).get("completion") == "0"
                    ]
                    # Also include anything that has :free in the ID as a safety measure
                    for m in data.get("data", []):
                        if ":free" in m["id"] and m["id"] not in self._free_models:
                            self._free_models.append(m["id"])

                    self._last_refresh = time.time()
                    logger.info(f"Refreshed free models: {len(self._free_models)} found")
        except Exception as e:
            logger.error(f"Failed to refresh free models: {e}")

    async def complete_async(self, request: LLMRequest) -> LLMResponse:
        await self._refresh_free_models()
        
        # SAFETY NET: Force free models only.
        # If the requested model is NOT in the free list, switch to the first available free model.
        if self._free_models and self.model not in self._free_models:
            logger.warning(f"COST PROTECTION: Requested model {self.model} is not free! Switching to {self._free_models[0]}")
            self.model = self._free_models[0]

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
        }

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(self.endpoint, json=payload, headers=headers, timeout=self.timeout)
                latency = (time.monotonic() - t0) * 1000
                
                if resp.status_code != 200:
                    logger.warning(f"OpenRouter returned {resp.status_code}: {resp.text}")
                    if resp.status_code == 402: # Payment Required
                         logger.error(f"Cost Alert! Model {self.model} attempted to charge. Switching to free list immediately.")
                         if self._free_models:
                             self.model = self._free_models[0]
                    
                    return LLMResponse(text="", provider=self.name, model=self.model, success=False, error=f"HTTP {resp.status_code}: {resp.text}")
                
                data = resp.json()
                choice = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                self._usage_today += 1
                
                return LLMResponse(text=choice, provider=self.name, model=self.model, tokens_in=usage.get("prompt_tokens", 0), tokens_out=usage.get("completion_tokens", 0), latency_ms=latency)
        except Exception as e:
            logger.error(f"OpenRouter exception: {str(e)}")
            return LLMResponse(text="", provider=self.name, model=self.model, success=False, error=str(e))

    def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            return asyncio.run(self.complete_async(request))
        except Exception as e:
            logger.error(f"Async run error: {e}")
            return LLMResponse(text="", provider=self.name, model=self.model, success=False, error=str(e))
