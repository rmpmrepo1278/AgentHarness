"""llama.cpp provider — local LLM inference via OpenAI-compatible API."""
from __future__ import annotations

import time
from typing import Any, List

import httpx

from core.providers.base import BudgetStatus, LLMProvider, LLMRequest, LLMResponse


class LlamaCppProvider(LLMProvider):
    """LLM provider for llama.cpp server (OpenAI-compatible endpoint)."""

    def __init__(
        self,
        name: str = "llamacpp",
        endpoint: str = "http://localhost:8080",
        model: str = "",
        timeout: int = 120,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, tier=1, model=model)
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Send a completion request to the llama.cpp server."""
        messages: List[dict] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }

        try:
            t0 = time.monotonic()
            resp = httpx.post(
                f"{self.endpoint}/v1/chat/completions",
                json=payload,
                timeout=self.timeout,
            )
            latency = (time.monotonic() - t0) * 1000
            resp.raise_for_status()
            data = resp.json()

            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})

            return LLMResponse(
                text=text,
                provider=self.name,
                model=self.model,
                tokens_in=usage.get("prompt_tokens", 0),
                tokens_out=usage.get("completion_tokens", 0),
                latency_ms=latency,
                success=True,
            )
        except Exception as exc:
            return LLMResponse(
                text="",
                provider=self.name,
                model=self.model,
                success=False,
                error=str(exc),
            )

    def is_available(self) -> bool:
        """Check if the llama.cpp server is reachable."""
        try:
            resp = httpx.get(
                f"{self.endpoint}/health",
                timeout=5,
            )
            if resp.status_code == 200:
                return True
        except Exception:
            pass

        # Fallback: try the models endpoint
        try:
            resp = httpx.get(
                f"{self.endpoint}/v1/models",
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def budget_status(self) -> BudgetStatus:
        """Local inference is always free and unlimited."""
        return BudgetStatus(cost_model="free", estimated_remaining=None)

    def capabilities(self) -> List[str]:
        """Return provider capabilities."""
        return ["chat", "local", "offline"]
