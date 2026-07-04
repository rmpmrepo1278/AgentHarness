from __future__ import annotations
import os
import time
from typing import Any
import httpx
from core.providers.base import LLMProvider, LLMRequest, LLMResponse


class FreeLLMAPIProvider(LLMProvider):
    """FreeLLMAPI — aggregates 16+ free providers behind one endpoint.

    Acts as a super-provider: routes to FreeLLMAPI which handles
    fallback across groq/cerebras/google/openrouter/etc.
    """

    def __init__(
        self,
        name: str = "freellmapi",
        api_key: str | None = None,
        endpoint: str | None = None,
        model: str = "auto",
        daily_limit: int = 100000,
        timeout: float = 120.0,
        **kwargs: Any,
    ) -> None:
        for k in ["name", "tier", "model"]:
            kwargs.pop(k, None)
        super().__init__(name=name, tier=3, model=model, **kwargs)
        self.api_key = api_key or os.environ.get("FREELLMAPI_KEY", "")
        self.endpoint = endpoint or os.environ.get("FREELLMAPI_ENDPOINT", "http://localhost:3005")
        self.daily_limit = daily_limit
        self.timeout = timeout
        self._usage_today: int = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.is_available():
            return LLMResponse(
                text="", provider=self.name, model=self.model,
                success=False, error="FreeLLMAPI not available"
            )
        url = f"{self.endpoint}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
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
            resp = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
        except Exception as e:
            return LLMResponse(
                text="", provider=self.name, model=self.model,
                success=False, error=f"FreeLLMAPI error: {e}"
            )
        latency = (time.monotonic() - t0) * 1000
        if resp.status_code == 429:
            return LLMResponse(
                text="", provider=self.name, model=self.model,
                success=False, error="429 (FreeLLMAPI rate-limited)"
            )
        if resp.status_code != 200:
            return LLMResponse(
                text="", provider=self.name, model=self.model,
                success=False, error=f"FreeLLMAPI {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json()
        if "error" in data:
            return LLMResponse(
                text="", provider=self.name, model=self.model,
                success=False, error=f"FreeLLMAPI: {data['error'].get('message', 'unknown')}"
            )
        usage = data.get("usage", {})
        return LLMResponse(
            text=data["choices"][0]["message"]["content"],
            provider=self.name,
            model=data.get("model", self.model),
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            latency_ms=latency,
        )

    def is_available(self) -> bool:
        return bool(self.api_key and self.endpoint)

    def budget_status(self):
        from core.providers.base import BudgetStatus
        return BudgetStatus(
            cost_model="free",
            estimated_remaining=max(0, self.daily_limit - self._usage_today),
        )
