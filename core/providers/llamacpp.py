"""llama.cpp provider — local LLM inference via OpenAI-compatible API."""
from __future__ import annotations

import time
from typing import Any, List

import httpx

from core.providers.base import BudgetStatus, LLMProvider, LLMRequest, LLMResponse


class LlamaCppProvider(LLMProvider):  # noqa: N801 - Ollama adapter
    """Ollama adapter — llama.cpp-compatible OpenAI endpoint using Ollama as the local inference engine."""

    def __init__(
        self,
        name: str = "llamacpp",
        endpoint: str = "http://localhost:8080",
        model: str = "",
        timeout: int = 15,
        **kwargs: Any,
    ) -> None:
        for k in ["name", "tier", "model"]: kwargs.pop(k, None)
        super().__init__(name=name, tier=1, model=model, **kwargs)
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Send a completion request to the Ollama server."""
        # Ollama uses /api/chat endpoint with slight format difference
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "model": self.model or "llama3.2:3b",
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": request.max_tokens,
                "temperature": request.temperature,
            },
        }

        try:
            t0 = time.monotonic()
            resp = httpx.post(
                f"{self.endpoint}/api/chat",
                json=payload,
                timeout=self.timeout,
            )
            latency = (time.monotonic() - t0) * 1000
            resp.raise_for_status()
            data = resp.json()

            text = data.get("message", {}).get("content", "")
            # Ollama returns usage in prompt_eval_count (input) and eval_count (output)
            prompt_eval_count = data.get("prompt_eval_count", 0)
            eval_count = data.get("eval_count", 0)

            return LLMResponse(
                text=text,
                provider=self.name,
                model=data.get("model", self.model),
                tokens_in=prompt_eval_count,
                tokens_out=eval_count,
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
        """Check if the Ollama server is reachable."""
        # Ollama uses /api/tags endpoint
        try:
            resp = httpx.get(
                f"{self.endpoint}/api/tags",
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
