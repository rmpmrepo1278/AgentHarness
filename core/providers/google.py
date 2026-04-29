"""Google Gemini cloud LLM provider."""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

from core.providers.base import BudgetStatus, LLMProvider, LLMRequest, LLMResponse


class GoogleProvider(LLMProvider):
    """Google Generative Language API (Gemini models)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.5-flash",
        daily_limit: int = 1500,
        timeout: float = 600.0,
        **kwargs: Any,
    ) -> None:
        provider_name = kwargs.pop("name", "google-primary")
        super().__init__(name=provider_name, tier=2, model=model, **kwargs)
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        self.daily_limit = daily_limit
        self.timeout = timeout
        self._usage_today: int = 0

    # -- LLMProvider interface ------------------------------------------------

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.is_available():
            return LLMResponse(
                text="", provider=self.name, model=self.model,
                success=False, error="Google provider not available",
            )

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        headers = {"Content-Type": "application/json"}

        # Use systemInstruction for system prompts - enables Gemini automatic caching
        # (repeated prefixes get cached at ~8x cheaper input token rate)
        contents: list[dict[str, Any]] = [{
            "role": "user",
            "parts": [{"text": request.prompt}],
        }]

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": request.max_tokens,
                "temperature": request.temperature,
            },
        }
        if request.system_prompt:
            payload["systemInstruction"] = {
                "parts": [{"text": request.system_prompt}],
            }

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
        # Gemini response: candidates[0].content.parts[0].text
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            return LLMResponse(
                text="", provider=self.name, model=self.model,
                success=False, error="Unexpected response format",
            )

        usage = data.get("usageMetadata", {})
        self._usage_today += 1

        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            tokens_in=usage.get("promptTokenCount", 0),
            tokens_out=usage.get("candidatesTokenCount", 0),
            latency_ms=latency,
        )

    def is_available(self) -> bool:
        return bool(self.api_key) and self._usage_today < self.daily_limit

    def budget_status(self) -> BudgetStatus:
        return BudgetStatus(
            cost_model="per_request",
            estimated_remaining=max(0, self.daily_limit - self._usage_today),
        )
