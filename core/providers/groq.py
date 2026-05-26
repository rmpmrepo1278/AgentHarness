from __future__ import annotations
import os
import time
from typing import Any
import httpx
from core.providers.base import BudgetStatus, LLMProvider, LLMRequest, LLMResponse

class GroqProvider(LLMProvider):
    def __init__(
        self,
        name: str = "groq",
        api_key: str | None = None,
        model: str = "llama-3.3-70b-versatile",
        daily_limit: int = 200,
        timeout: float = 600.0,
        **kwargs: Any
    ) -> None:
        # Clear out potential duplicates for the base class
        for k in ["name", "tier", "model"]:
            kwargs.pop(k, None)
        super().__init__(name=name, tier=2, model=model, **kwargs)
        self.api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        self.daily_limit = daily_limit
        self.timeout = timeout
        self._usage_today: int = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.is_available():
            return LLMResponse(text="", provider=self.name, model=self.model, success=False, error="Groq not available")
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature
        }
        t0 = time.monotonic()
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
        except Exception as e:
            return LLMResponse(text="", provider=self.name, model=self.model, success=False, error=str(e))
        latency = (time.monotonic() - t0) * 1000
        if resp.status_code == 429:
            return LLMResponse(text="", provider=self.name, model=self.model, success=False, error="429")
        if resp.status_code != 200:
            return LLMResponse(text="", provider=self.name, model=self.model, success=False, error=resp.text)
        data = resp.json()
        usage = data.get("usage", {})
        return LLMResponse(
            text=data["choices"][0]["message"]["content"],
            provider=self.name,
            model=self.model,
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            latency_ms=latency
        )

    def is_available(self) -> bool:
        return bool(self.api_key) and self._usage_today < self.daily_limit

    def budget_status(self) -> BudgetStatus:
        return BudgetStatus(cost_model="per_request", estimated_remaining=max(0, self.daily_limit - self._usage_today))
