from __future__ import annotations

import os
import time
from typing import Any

import httpx

from core.providers.base import BudgetStatus, LLMProvider, LLMRequest, LLMResponse


class CohereProvider(LLMProvider):
    """Cohere (Command A) — native v2 chat API, free trial tier."""

    def __init__(
        self,
        name: str = "cohere",
        api_key: str | None = None,
        model: str = "command-a-03-2025",
        daily_limit: int = 5000,
        timeout: float = 600.0,
        **kwargs: Any,
    ) -> None:
        for k in ["name", "tier", "model"]:
            kwargs.pop(k, None)
        super().__init__(name=name, tier=2, model=model, **kwargs)
        self.api_key = api_key or os.environ.get("COHERE_API_KEY", "")
        self.daily_limit = daily_limit
        self.timeout = timeout
        self._usage_today: int = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.is_available():
            return LLMResponse(
                text="", provider=self.name, model=self.model,
                success=False, error="Cohere provider not available",
            )

        url = "https://api.cohere.ai/v2/chat"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        messages: list[dict[str, Any]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }

        if request.tools:
            payload["tools"] = request.tools
            payload["tool_choice"] = "auto"

        t0 = time.monotonic()
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
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
        message = data.get("message", {}) or {}
        content = message.get("content", []) or []
        text_parts = [
            c.get("text", "") for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        choice = "".join(text_parts)

        usage = data.get("usage", {})
        tokens = usage.get("tokens", {})
        self._usage_today += 1

        return LLMResponse(
            text=choice,
            provider=self.name,
            model=self.model,
            tokens_in=tokens.get("input_tokens", 0),
            tokens_out=tokens.get("output_tokens", 0),
            latency_ms=latency,
        )

    def is_available(self) -> bool:
        return bool(self.api_key) and self._usage_today < self.daily_limit

    def budget_status(self) -> BudgetStatus:
        return BudgetStatus(
            cost_model="per_request",
            estimated_remaining=max(0, self.daily_limit - self._usage_today),
        )
