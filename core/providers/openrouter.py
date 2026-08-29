from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from core.providers.base import LLMRequest, LLMResponse
from core.providers.openai_compat import OpenAICompatProvider

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
        # Skip free model refresh for openrouter-tools (it needs gpt-4o-mini for tool support)
        if self.name == "openrouter-tools":
            logger.info("Skipping free model refresh for openrouter-tools (requires gpt-4o-mini for tool support)")
            return
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
        with open("/tmp/openrouter_entry.log", "a") as f:
            f.write(f"ENTRY complete_async: model={self.model!r}, name={self.name!r}\n")
        # Skip cost protection entirely for openrouter-tools (it needs gpt-4o-mini for tool support)
        if self.name != "openrouter-tools":
            await self._refresh_free_models()

                # SAFETY NET: Force free models only.
        # If the requested model is NOT in the free list, switch to the first available free model.
        # EXCEPTION: The openrouter-tools provider is specifically configured for tool use with gpt-4o-mini.
        # Never switch this provider to a free model, as free models on OpenRouter (Novita) do not support tools properly.
        # Check by model name since openrouter-tools is the only provider using gpt-4o-mini
        is_tool_model = self.model == "openai/gpt-4o-mini"
        with open("/tmp/openrouter_debug.log", "a") as f:
            f.write(f"DEBUG_OPENROUTER: name={self.name!r}, model={self.model!r}, is_tool_model={is_tool_model}, free_models={self._free_models[:3] if self._free_models else None}, model_in_free={self.model in self._free_models if self._free_models else None}\n")
        if self._free_models and self.model not in self._free_models and not is_tool_model:
            logger.warning(f"COST PROTECTION: Requested model {self.model} is not free! Switching to {self._free_models[0]}")
            self.model = self._free_models[0]
        elif is_tool_model and self._free_models and self.model not in self._free_models:
            logger.warning(f"COST PROTECTION: Keeping paid model {self.model} for tool-capable model (configured for tool use).")

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

        # Pass through tools if provided
        if request.tools:
            payload["tools"] = request.tools
            # tool_choice from request.tool_name or default to "auto"
            if request.tool_name:
                payload["tool_choice"] = request.tool_name if request.tool_name != "auto" else "auto"
            else:
                payload["tool_choice"] = "auto"

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
                message = data["choices"][0].get("message", {}) or {}
                choice = message.get("content") or ""
                tool_calls = message.get("tool_calls")
                usage = data.get("usage", {})
                self._usage_today += 1

                # If tool_calls present, encode them in text for routing
                if tool_calls:
                    import json
                    choice = json.dumps({"tool_calls": tool_calls}, ensure_ascii=False)

                return LLMResponse(text=choice, provider=self.name, model=self.model, tokens_in=usage.get("prompt_tokens", 0), tokens_out=usage.get("completion_tokens", 0), latency_ms=latency)
        except Exception as e:
            logger.error(f"OpenRouter exception: {e!s}")
            return LLMResponse(text="", provider=self.name, model=self.model, success=False, error=str(e))

    def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            # Check if we're already in an async event loop (e.g. FastAPI)
            loop = asyncio.get_running_loop()
            # We're in an async context — run in a thread to avoid nested event loops
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self.complete_async(request))
                return future.result(timeout=self.timeout + 10)
        except RuntimeError:
            # No running loop — safe to use asyncio.run directly
            return asyncio.run(self.complete_async(request))
        except Exception as e:
            logger.error(f"Async run error: {e}")
            return LLMResponse(text="", provider=self.name, model=self.model, success=False, error=str(e))
