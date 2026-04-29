"""Generic OpenAI-compatible LLM provider.

Works with OpenAI, LocalAI, vLLM, LM Studio, and any endpoint that speaks
the OpenAI /v1/chat/completions format.  Cerebras, SambaNova, and OpenRouter
providers subclass this with their own defaults.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

from core.providers.base import BudgetStatus, LLMProvider, LLMRequest, LLMResponse


class OpenAICompatProvider(LLMProvider):
    """OpenAI-compatible chat completions provider."""

    def __init__(
        self,
        name: str = "openai_compat",
        tier: int = 2,
        endpoint: str = "https://api.openai.com/v1/chat/completions",
        api_key: str | None = None,
        env_key: str = "OPENAI_API_KEY",
        model: str = "gpt-4o-mini",
        daily_limit: int = 500,
        timeout: float = 600.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, tier=tier, model=model, **kwargs)
        self.endpoint = endpoint
        self.api_key = api_key or os.environ.get(env_key, "")
        self.daily_limit = daily_limit
        self.timeout = timeout
        self._usage_today: int = 0

    # -- LLMProvider interface ------------------------------------------------

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.is_available():
            return LLMResponse(
                text="", provider=self.name, model=self.model,
                success=False, error=f"{self.name} provider not available",
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }

        t0 = time.monotonic()
        try:
            resp = httpx.post(
                self.endpoint, json=payload, headers=headers, timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            return LLMResponse(
                text="", provider=self.name, model=self.model,
                success=False, error=f"HTTP error: {exc}",
            )
        latency = (time.monotonic() - t0) * 1000

        if resp.status_code == 429:
            return LLMResponse(
                text="", provider=self.name, model=self.model,
                success=False, error="Rate limited (429)",
            )
        if resp.status_code != 200:
            return LLMResponse(
                text="", provider=self.name, model=self.model,
                success=False, error=f"HTTP {resp.status_code}: {resp.text}",
            )

        data = resp.json()
        choice = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        self._usage_today += 1

        return LLMResponse(
            text=choice,
            provider=self.name,
            model=self.model,
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            latency_ms=latency,
        )

    def is_available(self) -> bool:
        return self._usage_today < self.daily_limit

    def budget_status(self) -> BudgetStatus:
        return BudgetStatus(
            cost_model="per_request",
            estimated_remaining=max(0, self.daily_limit - self._usage_today),
        )
