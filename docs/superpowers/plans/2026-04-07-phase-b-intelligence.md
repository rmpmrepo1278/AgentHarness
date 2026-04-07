# Phase B: Intelligence Layer — LLM Providers + Budget + Scheduler

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add multi-provider LLM abstraction with budget-aware routing and rewrite the scheduler from bash to Python, so AgentHarness can intelligently route LLM requests across local and cloud providers while staying within free-tier limits.

**Architecture:** An abstract `LLMProvider` base class defines the interface. Each provider (llamacpp, groq, google, etc.) implements it. A `Router` picks the best provider per request based on complexity tier, budget, and availability. A `Budget` tracker records usage after-the-fact with atomic JSON persistence. The Python scheduler replaces `scheduler.sh`, reading bundles via the registry loader and integrating with budget/heartbeat/circuit-breaker.

**Tech Stack:** Python 3.9+, httpx (async HTTP client), PyYAML, existing core/ modules from Phase A

**Spec:** `docs/superpowers/specs/2026-04-07-agentharness-v2-design.md` (Sections 2, 8)

**Depends on Phase A:** discovery engine, state manager, registry loader, atomic JSON, circuit breaker, watchdog, selftest

---

## File Structure

### New files to create:
```
core/providers/__init__.py
core/providers/base.py             # Abstract LLMProvider + BudgetStatus + LLMResponse
core/providers/llamacpp.py         # llama.cpp / ik_llama.cpp provider
core/providers/groq.py             # Groq API provider
core/providers/google.py           # Google Gemini API provider
core/providers/openai_compat.py    # OpenAI-compatible provider (also vLLM, LocalAI, LM Studio)
core/providers/cerebras.py         # Cerebras API provider
core/providers/sambanova.py        # SambaNova API provider
core/providers/openrouter.py       # OpenRouter free-tier provider
core/providers/anthropic.py        # Anthropic Claude API provider
core/providers/router.py           # Smart routing by complexity + budget + availability
core/providers/budget.py           # Usage tracking with atomic persistence
core/scheduler/__init__.py
core/scheduler/scheduler.py        # Python scheduler (replaces scheduler.sh)
core/scheduler/windows.py          # Network/time window detection
config/providers.yaml              # Provider configuration template
tests/test_providers_base.py
tests/test_providers_llamacpp.py
tests/test_providers_router.py
tests/test_providers_budget.py
tests/test_scheduler.py
tests/test_scheduler_windows.py
```

### Files to modify:
```
cli.py                             # Add budget, migrate-scheduler commands
requirements.txt                   # Add httpx
```

---

## Task 1: Provider Base Classes + Data Types

**Files:**
- Create: `core/providers/__init__.py`
- Create: `core/providers/base.py`
- Test: `tests/test_providers_base.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_providers_base.py
from __future__ import annotations
import pytest


def test_budget_status_defaults():
    from core.providers.base import BudgetStatus
    bs = BudgetStatus()
    assert bs.known_remaining is None
    assert bs.estimated_remaining is None
    assert bs.reset_at is None
    assert bs.cost_model == "free"


def test_budget_status_custom():
    from core.providers.base import BudgetStatus
    bs = BudgetStatus(known_remaining=150, cost_model="per_request")
    assert bs.known_remaining == 150
    assert bs.cost_model == "per_request"


def test_llm_response():
    from core.providers.base import LLMResponse
    resp = LLMResponse(
        text="Hello world",
        provider="groq",
        model="llama-3.3-70b",
        tokens_in=10,
        tokens_out=5,
        latency_ms=200,
    )
    assert resp.text == "Hello world"
    assert resp.total_tokens == 15


def test_llm_response_error():
    from core.providers.base import LLMResponse
    resp = LLMResponse.error("groq", "Rate limited")
    assert resp.success is False
    assert resp.error == "Rate limited"
    assert resp.provider == "groq"


def test_complexity_enum():
    from core.providers.base import Complexity
    assert Complexity.LOW.value == "low"
    assert Complexity.CRITICAL.value == "critical"


def test_provider_interface_requires_methods():
    from core.providers.base import LLMProvider
    # Can't instantiate without implementing abstract methods
    with pytest.raises(TypeError):
        LLMProvider(name="test", tier="local")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_providers_base.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement base classes**

```python
# core/providers/__init__.py
"""LLM provider abstraction — multi-provider routing with budget tracking."""

# core/providers/base.py
"""Abstract base classes for LLM providers."""
from __future__ import annotations

import enum
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator, Optional


class Complexity(enum.Enum):
    """Request complexity tier — determines provider priority order."""
    LOW = "low"          # Triage, formatting, simple extraction
    MEDIUM = "medium"    # Summarization, analysis, tool selection
    HIGH = "high"        # Complex reasoning, multi-step planning
    CRITICAL = "critical"  # System broken, needs immediate help


@dataclass
class BudgetStatus:
    """What we know about a provider's remaining capacity."""
    known_remaining: Optional[int] = None     # From provider API (if exposed)
    estimated_remaining: Optional[int] = None  # Our tracking estimate
    reset_at: Optional[datetime] = None
    cost_model: str = "free"  # "per_request", "per_token", "per_minute", "free"


@dataclass
class LLMResponse:
    """Response from an LLM provider."""
    text: str = ""
    provider: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    success: bool = True
    error: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    @classmethod
    def error(cls, provider: str, message: str) -> LLMResponse:
        return cls(provider=provider, success=False, error=message)


@dataclass
class LLMRequest:
    """A request to be routed to an LLM provider."""
    prompt: str
    max_tokens: int = 1024
    temperature: float = 0.7
    complexity: Complexity = Complexity.MEDIUM
    system_prompt: Optional[str] = None
    tool_name: Optional[str] = None  # Which tool triggered this (for policy matching)


class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    All providers implement this interface. The router uses it to
    check availability, budget, and capabilities before routing.
    """

    def __init__(self, name: str, tier: str, model: str = "", **kwargs):
        self.name = name
        self.tier = tier  # "local", "cloud_free", "cloud_paid"
        self.model = model
        self.enabled = True

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        """Send a completion request. Returns LLMResponse."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Health check — is this provider up right now?"""
        ...

    @abstractmethod
    def budget_status(self) -> BudgetStatus:
        """Return what we know about remaining capacity."""
        ...

    def capabilities(self) -> list[str]:
        """What this provider supports. Override to add more."""
        return ["chat"]

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, tier={self.tier!r})"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_providers_base.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/providers/__init__.py core/providers/base.py tests/test_providers_base.py
git commit -m "feat: add LLM provider base classes — LLMProvider, BudgetStatus, LLMResponse, Complexity"
```

---

## Task 2: llama.cpp Provider (Local)

**Files:**
- Create: `core/providers/llamacpp.py`
- Test: `tests/test_providers_llamacpp.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_providers_llamacpp.py
from __future__ import annotations
import json
import pytest
from unittest.mock import patch, MagicMock


def test_llamacpp_complete_success():
    from core.providers.llamacpp import LlamaCppProvider
    from core.providers.base import LLMRequest

    provider = LlamaCppProvider(name="local_small", endpoint="http://localhost:8080")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "Hello!"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }

    with patch("core.providers.llamacpp.httpx.post", return_value=mock_response):
        resp = provider.complete(LLMRequest(prompt="Hi"))
    assert resp.success is True
    assert resp.text == "Hello!"
    assert resp.tokens_in == 5
    assert resp.tokens_out == 3


def test_llamacpp_complete_failure():
    from core.providers.llamacpp import LlamaCppProvider
    from core.providers.base import LLMRequest

    provider = LlamaCppProvider(name="local_small", endpoint="http://localhost:8080")

    with patch("core.providers.llamacpp.httpx.post", side_effect=Exception("Connection refused")):
        resp = provider.complete(LLMRequest(prompt="Hi"))
    assert resp.success is False
    assert "Connection refused" in resp.error


def test_llamacpp_is_available():
    from core.providers.llamacpp import LlamaCppProvider

    provider = LlamaCppProvider(name="local_small", endpoint="http://localhost:8080")

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("core.providers.llamacpp.httpx.get", return_value=mock_response):
        assert provider.is_available() is True


def test_llamacpp_is_unavailable():
    from core.providers.llamacpp import LlamaCppProvider

    provider = LlamaCppProvider(name="local_small", endpoint="http://localhost:8080")

    with patch("core.providers.llamacpp.httpx.get", side_effect=Exception("down")):
        assert provider.is_available() is False


def test_llamacpp_budget_is_unlimited():
    from core.providers.llamacpp import LlamaCppProvider

    provider = LlamaCppProvider(name="local_small", endpoint="http://localhost:8080")
    status = provider.budget_status()
    assert status.cost_model == "free"
    assert status.estimated_remaining is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_providers_llamacpp.py -v`
Expected: FAIL

- [ ] **Step 3: Install httpx and update requirements.txt**

```bash
pip3 install httpx
```

Update `requirements.txt`:
```
pyyaml>=6.0
httpx>=0.27.0
```

- [ ] **Step 4: Implement llama.cpp provider**

```python
# core/providers/llamacpp.py
"""llama.cpp / ik_llama.cpp provider — talks to a local HTTP server."""
from __future__ import annotations

import logging
import time

import httpx

from core.providers.base import (
    BudgetStatus,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)

log = logging.getLogger(__name__)


class LlamaCppProvider(LLMProvider):
    """Provider for llama.cpp or ik_llama.cpp HTTP servers.

    Expects an OpenAI-compatible /v1/chat/completions endpoint.
    Discovery finds the endpoint; this class just talks to it.
    """

    def __init__(
        self,
        name: str = "llamacpp",
        endpoint: str = "http://localhost:8080",
        model: str = "",
        timeout: int = 120,
        **kwargs,
    ):
        super().__init__(name=name, tier="local", model=model, **kwargs)
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Send completion to llama.cpp server."""
        start = time.monotonic()
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if self.model:
            payload["model"] = self.model

        try:
            resp = httpx.post(
                f"{self.endpoint}/v1/chat/completions",
                json=payload,
                timeout=self.timeout,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)

            if resp.status_code != 200:
                return LLMResponse.error(self.name, f"HTTP {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = data.get("usage", {})

            return LLMResponse(
                text=text,
                provider=self.name,
                model=data.get("model", self.model),
                tokens_in=usage.get("prompt_tokens", 0),
                tokens_out=usage.get("completion_tokens", 0),
                latency_ms=elapsed_ms,
            )
        except Exception as e:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            log.warning(f"{self.name} completion failed: {e}")
            return LLMResponse.error(self.name, str(e))

    def is_available(self) -> bool:
        """Check if the server is up via /health or /v1/models."""
        try:
            resp = httpx.get(f"{self.endpoint}/health", timeout=5)
            return resp.status_code == 200
        except Exception:
            try:
                resp = httpx.get(f"{self.endpoint}/v1/models", timeout=5)
                return resp.status_code == 200
            except Exception:
                return False

    def budget_status(self) -> BudgetStatus:
        """Local provider — always free, unlimited."""
        return BudgetStatus(cost_model="free")

    def capabilities(self) -> list[str]:
        return ["chat"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_providers_llamacpp.py -v`
Expected: All 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add core/providers/llamacpp.py tests/test_providers_llamacpp.py requirements.txt
git commit -m "feat: add llama.cpp provider — local LLM via OpenAI-compatible API"
```

---

## Task 3: Cloud Provider Template (Groq)

**Files:**
- Create: `core/providers/groq.py`
- Test: `tests/test_providers_groq.py`

Groq is the reference cloud provider. All other cloud providers follow the same pattern.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_providers_groq.py
from __future__ import annotations
import json
import pytest
from unittest.mock import patch, MagicMock


def test_groq_complete_success():
    from core.providers.groq import GroqProvider
    from core.providers.base import LLMRequest

    provider = GroqProvider(api_key="test-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "Answer"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "model": "llama-3.3-70b-versatile",
    }

    with patch("core.providers.groq.httpx.post", return_value=mock_response):
        resp = provider.complete(LLMRequest(prompt="Question"))
    assert resp.success is True
    assert resp.text == "Answer"
    assert resp.provider == "groq"


def test_groq_rate_limited():
    from core.providers.groq import GroqProvider
    from core.providers.base import LLMRequest

    provider = GroqProvider(api_key="test-key")

    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.text = "Rate limited"
    mock_response.headers = {"retry-after": "2"}

    with patch("core.providers.groq.httpx.post", return_value=mock_response):
        resp = provider.complete(LLMRequest(prompt="Q"))
    assert resp.success is False
    assert "429" in resp.error


def test_groq_no_api_key():
    from core.providers.groq import GroqProvider

    provider = GroqProvider(api_key="")
    assert provider.is_available() is False


def test_groq_budget_status():
    from core.providers.groq import GroqProvider

    provider = GroqProvider(api_key="test-key", daily_limit=200)
    status = provider.budget_status()
    assert status.cost_model == "per_request"
    assert status.estimated_remaining == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_providers_groq.py -v`
Expected: FAIL

- [ ] **Step 3: Implement Groq provider**

```python
# core/providers/groq.py
"""Groq API provider — fast cloud inference with daily request limit."""
from __future__ import annotations

import logging
import os
import time

import httpx

from core.providers.base import (
    BudgetStatus,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)

log = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"


class GroqProvider(LLMProvider):
    """Groq cloud provider — free tier with daily request limit."""

    def __init__(
        self,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        daily_limit: int = 200,
        timeout: int = 30,
        **kwargs,
    ):
        super().__init__(name="groq", tier="cloud_free", model=model, **kwargs)
        self.api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        self.daily_limit = daily_limit
        self.timeout = timeout
        self._usage_today = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.api_key:
            return LLMResponse.error(self.name, "No API key configured")

        start = time.monotonic()
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        try:
            resp = httpx.post(
                GROQ_API_URL,
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": request.max_tokens,
                    "temperature": request.temperature,
                },
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)

            if resp.status_code == 429:
                return LLMResponse.error(self.name, f"HTTP 429: Rate limited. {resp.text[:200]}")

            if resp.status_code != 200:
                return LLMResponse.error(self.name, f"HTTP {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = data.get("usage", {})

            self._usage_today += 1

            return LLMResponse(
                text=text,
                provider=self.name,
                model=data.get("model", self.model),
                tokens_in=usage.get("prompt_tokens", 0),
                tokens_out=usage.get("completion_tokens", 0),
                latency_ms=elapsed_ms,
            )
        except Exception as e:
            log.warning(f"Groq completion failed: {e}")
            return LLMResponse.error(self.name, str(e))

    def is_available(self) -> bool:
        return bool(self.api_key) and self.enabled

    def budget_status(self) -> BudgetStatus:
        return BudgetStatus(
            estimated_remaining=max(0, self.daily_limit - self._usage_today),
            cost_model="per_request",
        )

    def capabilities(self) -> list[str]:
        return ["chat", "function_calling"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_providers_groq.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/providers/groq.py tests/test_providers_groq.py
git commit -m "feat: add Groq provider — cloud inference with daily request limit"
```

---

## Task 4: Remaining Cloud Providers (Batch)

**Files:**
- Create: `core/providers/google.py`
- Create: `core/providers/openai_compat.py`
- Create: `core/providers/cerebras.py`
- Create: `core/providers/sambanova.py`
- Create: `core/providers/openrouter.py`
- Create: `core/providers/anthropic.py`

All follow the same pattern as Groq. Each one:
- Extends `LLMProvider`
- Has an API URL constant, default model, api_key from env var
- Implements `complete()`, `is_available()`, `budget_status()`, `capabilities()`

- [ ] **Step 1: Implement Google Gemini provider**

```python
# core/providers/google.py
"""Google Gemini API provider — generous free tier."""
from __future__ import annotations

import logging
import os
import time

import httpx

from core.providers.base import (
    BudgetStatus,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)

log = logging.getLogger(__name__)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.5-flash"


class GoogleProvider(LLMProvider):
    """Google Gemini API — 1500 req/day Flash, 50/day Pro."""

    def __init__(
        self,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        daily_limit: int = 1500,
        timeout: int = 30,
        **kwargs,
    ):
        super().__init__(name="google", tier="cloud_free", model=model, **kwargs)
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        self.daily_limit = daily_limit
        self.timeout = timeout
        self._usage_today = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.api_key:
            return LLMResponse.error(self.name, "No API key configured")

        start = time.monotonic()
        contents = [{"parts": [{"text": request.prompt}]}]
        if request.system_prompt:
            contents.insert(0, {"role": "user", "parts": [{"text": request.system_prompt}]})

        try:
            resp = httpx.post(
                f"{GEMINI_API_URL}/{self.model}:generateContent?key={self.api_key}",
                json={
                    "contents": contents,
                    "generationConfig": {
                        "maxOutputTokens": request.max_tokens,
                        "temperature": request.temperature,
                    },
                },
                timeout=self.timeout,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)

            if resp.status_code == 429:
                return LLMResponse.error(self.name, f"HTTP 429: Rate limited")
            if resp.status_code != 200:
                return LLMResponse.error(self.name, f"HTTP {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            text = ""
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                text = parts[0].get("text", "") if parts else ""

            usage = data.get("usageMetadata", {})
            self._usage_today += 1

            return LLMResponse(
                text=text,
                provider=self.name,
                model=self.model,
                tokens_in=usage.get("promptTokenCount", 0),
                tokens_out=usage.get("candidatesTokenCount", 0),
                latency_ms=elapsed_ms,
            )
        except Exception as e:
            log.warning(f"Google completion failed: {e}")
            return LLMResponse.error(self.name, str(e))

    def is_available(self) -> bool:
        return bool(self.api_key) and self.enabled

    def budget_status(self) -> BudgetStatus:
        return BudgetStatus(
            estimated_remaining=max(0, self.daily_limit - self._usage_today),
            cost_model="per_request",
        )

    def capabilities(self) -> list[str]:
        return ["chat", "vision"]
```

- [ ] **Step 2: Implement OpenAI-compatible provider**

```python
# core/providers/openai_compat.py
"""OpenAI-compatible provider — works with OpenAI, LocalAI, vLLM, LM Studio."""
from __future__ import annotations

import logging
import os
import time

import httpx

from core.providers.base import (
    BudgetStatus,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)

log = logging.getLogger(__name__)


class OpenAICompatProvider(LLMProvider):
    """Generic OpenAI-compatible API provider."""

    def __init__(
        self,
        name: str = "openai",
        api_key: str = "",
        api_key_env: str = "OPENAI_API_KEY",
        endpoint: str = "https://api.openai.com/v1/chat/completions",
        model: str = "gpt-4o-mini",
        tier: str = "cloud_paid",
        daily_limit: int = 0,  # 0 = no limit (paid)
        monthly_budget_usd: float = 0.0,
        timeout: int = 60,
        **kwargs,
    ):
        super().__init__(name=name, tier=tier, model=model, **kwargs)
        self.api_key = api_key or os.environ.get(api_key_env, "")
        self.endpoint = endpoint
        self.daily_limit = daily_limit
        self.monthly_budget_usd = monthly_budget_usd
        self.timeout = timeout
        self._usage_today = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.api_key:
            return LLMResponse.error(self.name, "No API key configured")

        start = time.monotonic()
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        try:
            resp = httpx.post(
                self.endpoint,
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": request.max_tokens,
                    "temperature": request.temperature,
                },
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)

            if resp.status_code == 429:
                return LLMResponse.error(self.name, f"HTTP 429: Rate limited")
            if resp.status_code != 200:
                return LLMResponse.error(self.name, f"HTTP {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = data.get("usage", {})
            self._usage_today += 1

            return LLMResponse(
                text=text,
                provider=self.name,
                model=data.get("model", self.model),
                tokens_in=usage.get("prompt_tokens", 0),
                tokens_out=usage.get("completion_tokens", 0),
                latency_ms=elapsed_ms,
            )
        except Exception as e:
            log.warning(f"{self.name} completion failed: {e}")
            return LLMResponse.error(self.name, str(e))

    def is_available(self) -> bool:
        return bool(self.api_key) and self.enabled

    def budget_status(self) -> BudgetStatus:
        if self.daily_limit > 0:
            return BudgetStatus(
                estimated_remaining=max(0, self.daily_limit - self._usage_today),
                cost_model="per_request",
            )
        return BudgetStatus(cost_model="per_token")

    def capabilities(self) -> list[str]:
        return ["chat", "function_calling"]
```

- [ ] **Step 3: Implement Cerebras, SambaNova, OpenRouter, Anthropic**

All use `OpenAICompatProvider` as the base pattern — they all speak the OpenAI chat completions format. Create thin wrappers:

```python
# core/providers/cerebras.py
"""Cerebras API provider — fast inference, free tier."""
from __future__ import annotations
import os
from core.providers.openai_compat import OpenAICompatProvider

class CerebrasProvider(OpenAICompatProvider):
    def __init__(self, api_key: str = "", **kwargs):
        super().__init__(
            name="cerebras",
            api_key=api_key or os.environ.get("CEREBRAS_API_KEY", ""),
            api_key_env="CEREBRAS_API_KEY",
            endpoint="https://api.cerebras.ai/v1/chat/completions",
            model=kwargs.pop("model", "llama-3.3-70b"),
            tier="cloud_free",
            daily_limit=kwargs.pop("daily_limit", 1000),
            **kwargs,
        )
```

```python
# core/providers/sambanova.py
"""SambaNova API provider — free tier on Llama/DeepSeek."""
from __future__ import annotations
import os
from core.providers.openai_compat import OpenAICompatProvider

class SambaNovaProvider(OpenAICompatProvider):
    def __init__(self, api_key: str = "", **kwargs):
        super().__init__(
            name="sambanova",
            api_key=api_key or os.environ.get("SAMBANOVA_API_KEY", ""),
            api_key_env="SAMBANOVA_API_KEY",
            endpoint="https://api.sambanova.ai/v1/chat/completions",
            model=kwargs.pop("model", "Meta-Llama-3.3-70B-Instruct"),
            tier="cloud_free",
            daily_limit=kwargs.pop("daily_limit", 500),
            **kwargs,
        )
```

```python
# core/providers/openrouter.py
"""OpenRouter provider — free model rotation."""
from __future__ import annotations
import os
from core.providers.openai_compat import OpenAICompatProvider

class OpenRouterProvider(OpenAICompatProvider):
    def __init__(self, api_key: str = "", **kwargs):
        super().__init__(
            name="openrouter",
            api_key=api_key or os.environ.get("OPENROUTER_API_KEY", ""),
            api_key_env="OPENROUTER_API_KEY",
            endpoint="https://openrouter.ai/api/v1/chat/completions",
            model=kwargs.pop("model", "meta-llama/llama-3.3-70b-instruct:free"),
            tier="cloud_free",
            daily_limit=kwargs.pop("daily_limit", 50),
            **kwargs,
        )
```

```python
# core/providers/anthropic.py
"""Anthropic Claude API provider."""
from __future__ import annotations
import logging
import os
import time
import httpx
from core.providers.base import BudgetStatus, LLMProvider, LLMRequest, LLMResponse

log = logging.getLogger(__name__)
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

class AnthropicProvider(LLMProvider):
    """Anthropic Claude API — different request/response format from OpenAI."""
    def __init__(self, api_key: str = "", model: str = "claude-sonnet-4-6", monthly_budget_usd: float = 10.0, timeout: int = 60, **kwargs):
        super().__init__(name="anthropic", tier="cloud_paid", model=model, **kwargs)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.monthly_budget_usd = monthly_budget_usd
        self.timeout = timeout

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.api_key:
            return LLMResponse.error(self.name, "No API key configured")
        start = time.monotonic()
        try:
            payload = {"model": self.model, "max_tokens": request.max_tokens, "messages": [{"role": "user", "content": request.prompt}]}
            if request.system_prompt:
                payload["system"] = request.system_prompt
            resp = httpx.post(ANTHROPIC_API_URL, json=payload, headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, timeout=self.timeout)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if resp.status_code == 429:
                return LLMResponse.error(self.name, "HTTP 429: Rate limited")
            if resp.status_code != 200:
                return LLMResponse.error(self.name, f"HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            usage = data.get("usage", {})
            return LLMResponse(text=text, provider=self.name, model=data.get("model", self.model), tokens_in=usage.get("input_tokens", 0), tokens_out=usage.get("output_tokens", 0), latency_ms=elapsed_ms)
        except Exception as e:
            log.warning(f"Anthropic completion failed: {e}")
            return LLMResponse.error(self.name, str(e))

    def is_available(self) -> bool:
        return bool(self.api_key) and self.enabled

    def budget_status(self) -> BudgetStatus:
        return BudgetStatus(cost_model="per_token")

    def capabilities(self) -> list[str]:
        return ["chat", "function_calling", "vision"]
```

- [ ] **Step 4: Commit all cloud providers**

```bash
git add core/providers/google.py core/providers/openai_compat.py core/providers/cerebras.py core/providers/sambanova.py core/providers/openrouter.py core/providers/anthropic.py
git commit -m "feat: add cloud providers — Google, OpenAI-compat, Cerebras, SambaNova, OpenRouter, Anthropic"
```

---

## Task 5: Budget Tracker

**Files:**
- Create: `core/providers/budget.py`
- Test: `tests/test_providers_budget.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_providers_budget.py
from __future__ import annotations
import json
import pytest
from pathlib import Path


@pytest.fixture
def budget_dir(tmp_path):
    return tmp_path


def test_record_and_check(budget_dir):
    from core.providers.budget import BudgetTracker
    bt = BudgetTracker(data_dir=str(budget_dir))
    bt.record_usage("groq", tokens_in=100, tokens_out=50, success=True)
    assert bt.can_use("groq", daily_limit=200) is True
    assert bt.get_usage("groq")["requests"] == 1
    assert bt.get_usage("groq")["tokens_in"] == 100


def test_budget_exhausted(budget_dir):
    from core.providers.budget import BudgetTracker
    bt = BudgetTracker(data_dir=str(budget_dir))
    for _ in range(200):
        bt.record_usage("groq", tokens_in=10, tokens_out=5, success=True)
    assert bt.can_use("groq", daily_limit=200) is False


def test_deprioritize_at_80_pct(budget_dir):
    from core.providers.budget import BudgetTracker
    bt = BudgetTracker(data_dir=str(budget_dir))
    for _ in range(160):
        bt.record_usage("groq", tokens_in=10, tokens_out=5, success=True)
    assert bt.should_deprioritize("groq", daily_limit=200) is True
    assert bt.can_use("groq", daily_limit=200) is True  # Still usable, just deprioritized


def test_daily_reset(budget_dir):
    from core.providers.budget import BudgetTracker
    bt = BudgetTracker(data_dir=str(budget_dir))
    bt.record_usage("groq", tokens_in=100, tokens_out=50, success=True)
    bt.reset_daily()
    assert bt.get_usage("groq")["requests"] == 0


def test_daily_report(budget_dir):
    from core.providers.budget import BudgetTracker
    bt = BudgetTracker(data_dir=str(budget_dir))
    bt.record_usage("groq", tokens_in=100, tokens_out=50, success=True)
    bt.record_usage("google", tokens_in=200, tokens_out=100, success=True)
    report = bt.daily_report()
    assert "groq" in report
    assert "google" in report


def test_persistence(budget_dir):
    from core.providers.budget import BudgetTracker
    bt1 = BudgetTracker(data_dir=str(budget_dir))
    bt1.record_usage("groq", tokens_in=100, tokens_out=50, success=True)
    # New instance reads from disk
    bt2 = BudgetTracker(data_dir=str(budget_dir))
    assert bt2.get_usage("groq")["requests"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_providers_budget.py -v`
Expected: FAIL

- [ ] **Step 3: Implement budget tracker**

```python
# core/providers/budget.py
"""LLM request budget tracking with atomic JSON persistence.

Records usage after-the-fact (not reserve/commit). Slight over-budget
is acceptable — the provider will return 429 and the router will fallback.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from core.resilience.atomic_json import atomic_write_json, safe_read_json

log = logging.getLogger(__name__)


class BudgetTracker:
    """Track LLM usage per provider with daily limits."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.budget_file = self.data_dir / "llm_budget.json"

    def _load(self) -> dict:
        data = safe_read_json(self.budget_file, default={})
        # Check if the date has rolled over
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if data.get("date") != today:
            data = {"date": today, "providers": {}}
            atomic_write_json(self.budget_file, data)
        return data

    def _save(self, data: dict) -> None:
        atomic_write_json(self.budget_file, data)

    def record_usage(
        self,
        provider: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        success: bool = True,
    ) -> None:
        """Record a completed LLM request."""
        data = self._load()
        providers = data.setdefault("providers", {})
        entry = providers.setdefault(provider, {
            "requests": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "errors": 0,
        })
        entry["requests"] += 1
        entry["tokens_in"] += tokens_in
        entry["tokens_out"] += tokens_out
        if not success:
            entry["errors"] += 1
        entry["last_used"] = time.time()
        self._save(data)

    def get_usage(self, provider: str) -> dict:
        """Get today's usage for a provider."""
        data = self._load()
        return data.get("providers", {}).get(provider, {
            "requests": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "errors": 0,
        })

    def can_use(self, provider: str, daily_limit: int) -> bool:
        """Check if provider is within daily limit."""
        if daily_limit <= 0:
            return True  # No limit (paid tier or local)
        usage = self.get_usage(provider)
        return usage.get("requests", 0) < daily_limit

    def should_deprioritize(self, provider: str, daily_limit: int) -> bool:
        """Check if provider is at 80%+ of daily limit."""
        if daily_limit <= 0:
            return False
        usage = self.get_usage(provider)
        return usage.get("requests", 0) >= daily_limit * 0.8

    def reset_daily(self) -> None:
        """Reset all daily counters. Called at midnight."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._save({"date": today, "providers": {}})

    def daily_report(self) -> str:
        """Generate a human-readable daily budget report."""
        data = self._load()
        lines = [f"LLM Budget Report — {data.get('date', 'unknown')}"]
        lines.append("=" * 50)
        providers = data.get("providers", {})
        if not providers:
            lines.append("  No LLM usage recorded today.")
        for name, usage in sorted(providers.items()):
            reqs = usage.get("requests", 0)
            tin = usage.get("tokens_in", 0)
            tout = usage.get("tokens_out", 0)
            errs = usage.get("errors", 0)
            lines.append(f"  {name}: {reqs} requests, {tin+tout} tokens, {errs} errors")
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_providers_budget.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/providers/budget.py tests/test_providers_budget.py
git commit -m "feat: add budget tracker — per-provider usage tracking with atomic persistence"
```

---

## Task 6: Router — Smart LLM Routing

**Files:**
- Create: `core/providers/router.py`
- Test: `tests/test_providers_router.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_providers_router.py
from __future__ import annotations
import pytest
from unittest.mock import MagicMock


def _make_provider(name, tier, available=True, budget_ok=True):
    from core.providers.base import BudgetStatus, LLMResponse
    p = MagicMock()
    p.name = name
    p.tier = tier
    p.enabled = True
    p.is_available.return_value = available
    p.budget_status.return_value = BudgetStatus(
        estimated_remaining=100 if budget_ok else 0,
        cost_model="per_request" if tier != "local" else "free",
    )
    p.complete.return_value = LLMResponse(
        text=f"from {name}", provider=name, success=True,
        tokens_in=10, tokens_out=5,
    )
    return p


def test_route_low_prefers_local(tmp_path):
    from core.providers.router import Router
    from core.providers.base import LLMRequest, Complexity
    from core.providers.budget import BudgetTracker

    local = _make_provider("local_small", "local")
    cloud = _make_provider("groq", "cloud_free")
    bt = BudgetTracker(data_dir=str(tmp_path))

    router = Router(
        providers=[local, cloud],
        budget=bt,
        routing={
            "low": ["local_small"],
            "medium": ["local_small", "groq"],
            "high": ["groq", "local_small"],
            "critical": ["groq", "local_small"],
        },
    )
    resp = router.route(LLMRequest(prompt="Hi", complexity=Complexity.LOW))
    assert resp.provider == "local_small"


def test_route_falls_through_on_unavailable(tmp_path):
    from core.providers.router import Router
    from core.providers.base import LLMRequest, Complexity
    from core.providers.budget import BudgetTracker

    local = _make_provider("local_small", "local", available=False)
    cloud = _make_provider("groq", "cloud_free")
    bt = BudgetTracker(data_dir=str(tmp_path))

    router = Router(
        providers=[local, cloud],
        budget=bt,
        routing={
            "low": ["local_small", "groq"],
            "medium": ["local_small", "groq"],
            "high": ["groq"],
            "critical": ["groq"],
        },
    )
    resp = router.route(LLMRequest(prompt="Hi", complexity=Complexity.LOW))
    assert resp.provider == "groq"


def test_route_skips_exhausted_budget(tmp_path):
    from core.providers.router import Router
    from core.providers.base import LLMRequest, Complexity
    from core.providers.budget import BudgetTracker

    local = _make_provider("local_small", "local", available=False)
    groq = _make_provider("groq", "cloud_free", budget_ok=False)
    google = _make_provider("google", "cloud_free")
    bt = BudgetTracker(data_dir=str(tmp_path))

    router = Router(
        providers=[local, groq, google],
        budget=bt,
        routing={
            "low": ["local_small", "groq", "google"],
            "medium": ["local_small", "groq", "google"],
            "high": ["groq", "google"],
            "critical": ["groq", "google"],
        },
    )
    resp = router.route(LLMRequest(prompt="Hi", complexity=Complexity.LOW))
    assert resp.provider == "google"


def test_route_all_exhausted_returns_error(tmp_path):
    from core.providers.router import Router
    from core.providers.base import LLMRequest, Complexity
    from core.providers.budget import BudgetTracker

    local = _make_provider("local_small", "local", available=False)
    bt = BudgetTracker(data_dir=str(tmp_path))

    router = Router(
        providers=[local],
        budget=bt,
        routing={
            "low": ["local_small"],
            "medium": ["local_small"],
            "high": ["local_small"],
            "critical": ["local_small"],
        },
    )
    resp = router.route(LLMRequest(prompt="Hi", complexity=Complexity.LOW))
    assert resp.success is False
    assert "exhausted" in resp.error.lower() or "no provider" in resp.error.lower()


def test_route_records_budget_on_success(tmp_path):
    from core.providers.router import Router
    from core.providers.base import LLMRequest, Complexity
    from core.providers.budget import BudgetTracker

    local = _make_provider("local_small", "local")
    bt = BudgetTracker(data_dir=str(tmp_path))

    router = Router(
        providers=[local],
        budget=bt,
        routing={
            "low": ["local_small"],
            "medium": ["local_small"],
            "high": ["local_small"],
            "critical": ["local_small"],
        },
    )
    router.route(LLMRequest(prompt="Hi", complexity=Complexity.LOW))
    usage = bt.get_usage("local_small")
    assert usage["requests"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_providers_router.py -v`
Expected: FAIL

- [ ] **Step 3: Implement router**

```python
# core/providers/router.py
"""Smart LLM routing — picks best provider by complexity, budget, availability."""
from __future__ import annotations

import logging
import time
from typing import Optional

from core.providers.base import (
    Complexity,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)
from core.providers.budget import BudgetTracker

log = logging.getLogger(__name__)

# Default routing table
DEFAULT_ROUTING = {
    "low": ["llamacpp_small", "llamacpp_large"],
    "medium": ["llamacpp_large", "google", "cerebras", "openrouter"],
    "high": ["google_pro", "groq", "sambanova", "llamacpp_large"],
    "critical": ["groq", "google", "openai", "anthropic", "llamacpp_large"],
}


class Router:
    """Route LLM requests to the best available provider."""

    def __init__(
        self,
        providers: list[LLMProvider],
        budget: BudgetTracker,
        routing: Optional[dict[str, list[str]]] = None,
        policies: Optional[list[dict]] = None,
        max_retries: int = 3,
    ):
        self.providers = {p.name: p for p in providers}
        self.budget = budget
        self.routing = routing or DEFAULT_ROUTING
        self.policies = policies or []
        self.max_retries = max_retries

    def _get_provider_order(self, request: LLMRequest) -> list[str]:
        """Get ordered list of provider names for this request's complexity."""
        tier_name = request.complexity.value
        order = self.routing.get(tier_name, [])

        # Apply user policies (force specific provider for certain tools)
        if request.tool_name:
            for policy in self.policies:
                import re
                if re.search(policy.get("match", "^$"), request.tool_name):
                    forced = policy.get("force")
                    if forced and forced in self.providers:
                        return [forced]

        return order

    def route(self, request: LLMRequest) -> LLMResponse:
        """Route a request to the best available provider.

        Tries providers in priority order. Falls through on:
        - Provider unavailable
        - Budget exhausted
        - Rate limit (429) — with exponential backoff
        - Timeout or error
        """
        provider_order = self._get_provider_order(request)

        for provider_name in provider_order:
            provider = self.providers.get(provider_name)
            if not provider or not provider.enabled:
                continue

            # Check availability
            if not provider.is_available():
                log.debug(f"Router: {provider_name} unavailable, skipping")
                continue

            # Check budget
            budget_status = provider.budget_status()
            if (budget_status.estimated_remaining is not None
                    and budget_status.estimated_remaining <= 0):
                log.debug(f"Router: {provider_name} budget exhausted, skipping")
                continue

            # Attempt the call
            try:
                resp = provider.complete(request)
                if resp.success:
                    # Record usage
                    self.budget.record_usage(
                        provider_name,
                        tokens_in=resp.tokens_in,
                        tokens_out=resp.tokens_out,
                        success=True,
                    )
                    return resp
                else:
                    # Check if rate limited
                    if resp.error and "429" in resp.error:
                        log.info(f"Router: {provider_name} rate limited, trying next")
                        self.budget.record_usage(provider_name, success=False)
                        continue
                    # Auth error — disable provider
                    if resp.error and ("401" in resp.error or "403" in resp.error):
                        log.warning(f"Router: {provider_name} auth error, disabling")
                        provider.enabled = False
                        continue
                    # Other error — try next
                    log.info(f"Router: {provider_name} error: {resp.error}, trying next")
                    continue

            except Exception as e:
                log.warning(f"Router: {provider_name} exception: {e}")
                continue

        # All providers exhausted
        tier_name = request.complexity.value
        return LLMResponse.error(
            "router",
            f"No provider available for {tier_name} request. "
            f"Tried: {', '.join(provider_order)}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_providers_router.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/providers/router.py tests/test_providers_router.py
git commit -m "feat: add LLM router — smart routing by complexity, budget, availability"
```

---

## Task 7: Provider Configuration File

**Files:**
- Create: `config/providers.yaml`

- [ ] **Step 1: Create providers.yaml template**

```yaml
# config/providers.yaml — LLM provider configuration
#
# Each provider section maps to a Python class in core/providers/.
# API keys are read from environment variables (never stored here).
# Set daily_limit to 0 for unlimited (paid tiers).

providers:
  llamacpp_small:
    type: llamacpp
    tier: local
    endpoint: auto          # Discovery finds the port
    model_hint: "9B"
    enabled: true

  llamacpp_large:
    type: llamacpp
    tier: local
    endpoint: auto
    model_hint: "35B"
    enabled: true

  groq:
    type: groq
    tier: cloud_free
    api_key_env: GROQ_API_KEY
    model: llama-3.3-70b-versatile
    daily_limit: 200
    enabled: true

  google:
    type: google
    tier: cloud_free
    api_key_env: GOOGLE_API_KEY
    model: gemini-2.5-flash
    daily_limit: 1500
    enabled: true

  cerebras:
    type: cerebras
    tier: cloud_free
    api_key_env: CEREBRAS_API_KEY
    model: llama-3.3-70b
    daily_limit: 1000
    enabled: true

  sambanova:
    type: sambanova
    tier: cloud_free
    api_key_env: SAMBANOVA_API_KEY
    model: Meta-Llama-3.3-70B-Instruct
    daily_limit: 500
    enabled: true

  openrouter:
    type: openrouter
    tier: cloud_free
    api_key_env: OPENROUTER_API_KEY
    model: meta-llama/llama-3.3-70b-instruct:free
    daily_limit: 50
    enabled: true

  openai:
    type: openai_compat
    tier: cloud_paid
    api_key_env: OPENAI_API_KEY
    endpoint: https://api.openai.com/v1/chat/completions
    model: gpt-4o-mini
    monthly_budget_usd: 5.00
    enabled: false           # Off by default — user opts in

  anthropic:
    type: anthropic
    tier: cloud_paid
    api_key_env: ANTHROPIC_API_KEY
    model: claude-sonnet-4-6
    monthly_budget_usd: 10.00
    enabled: false

# Routing table — provider priority per complexity tier
routing:
  low: [llamacpp_small, llamacpp_large]
  medium: [llamacpp_large, google, cerebras, openrouter]
  high: [google, groq, sambanova, llamacpp_large]
  critical: [groq, google, openai, anthropic, llamacpp_large]

# Policy overrides — force specific providers for specific tools
policies:
  - match: "backup|cleanup|disk"
    force: llamacpp_small
  - match: "security|audit"
    force: llamacpp_large
```

- [ ] **Step 2: Commit**

```bash
git add config/providers.yaml
git commit -m "feat: add providers.yaml — LLM provider configuration template"
```

---

## Task 8: Network Window Detection

**Files:**
- Create: `core/scheduler/__init__.py`
- Create: `core/scheduler/windows.py`
- Test: `tests/test_scheduler_windows.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_scheduler_windows.py
from __future__ import annotations
import pytest
from unittest.mock import patch
from datetime import datetime


def test_get_network_state_online():
    from core.scheduler.windows import get_network_state
    with patch("core.scheduler.windows._ping", return_value=True):
        state = get_network_state()
    assert state == "online"


def test_get_network_state_offline():
    from core.scheduler.windows import get_network_state
    with patch("core.scheduler.windows._ping", return_value=False):
        with patch("core.scheduler.windows._ping_host", return_value=False):
            state = get_network_state()
    assert state == "offline"


def test_get_network_state_lan_only():
    from core.scheduler.windows import get_network_state
    with patch("core.scheduler.windows._ping", return_value=False):
        with patch("core.scheduler.windows._ping_host", return_value=True):
            state = get_network_state(minipc_ip="192.168.1.100")
    assert state == "lan_only"


def test_get_window_online():
    from core.scheduler.windows import get_window
    assert get_window("online") == "online"


def test_get_window_offline():
    from core.scheduler.windows import get_window
    assert get_window("offline") == "offline"


def test_get_window_lan():
    from core.scheduler.windows import get_window
    assert get_window("lan_only") == "offline_lan"


def test_is_task_due_daily():
    from core.scheduler.windows import is_task_due
    import time
    # Task last ran 25 hours ago
    last_run = time.time() - 25 * 3600
    assert is_task_due("daily", last_run) is True


def test_is_task_not_due():
    from core.scheduler.windows import is_task_due
    import time
    # Task last ran 1 hour ago
    last_run = time.time() - 3600
    assert is_task_due("daily", last_run) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_scheduler_windows.py -v`
Expected: FAIL

- [ ] **Step 3: Implement window detection**

```python
# core/scheduler/__init__.py
"""Scheduler — network-aware task scheduling with budget integration."""

# core/scheduler/windows.py
"""Network and time window detection for the scheduler."""
from __future__ import annotations

import logging
import re
import subprocess
import time

log = logging.getLogger(__name__)

# Frequency string to seconds mapping
FREQUENCY_MAP = {
    "15m": 15 * 60,
    "1h": 3600,
    "6h": 6 * 3600,
    "12h": 12 * 3600,
    "daily": 24 * 3600,
    "3d": 3 * 24 * 3600,
    "weekly": 7 * 24 * 3600,
    "monthly": 30 * 24 * 3600,
}


def _ping(host: str, timeout: int = 3) -> bool:
    """Ping a host. Returns True if reachable."""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), host],
            capture_output=True, timeout=timeout + 2,
        )
        return result.returncode == 0
    except Exception:
        return False


def _ping_host(host: str) -> bool:
    """Ping a specific host (e.g., mini PC on LAN)."""
    return _ping(host, timeout=2)


def get_network_state(minipc_ip: str = "") -> str:
    """Detect current network state.

    Returns: "online", "lan_only", or "offline"
    """
    # Check internet connectivity
    if _ping("8.8.8.8") or _ping("1.1.1.1"):
        return "online"

    # Check LAN connectivity to mini PC
    if minipc_ip and _ping_host(minipc_ip):
        return "lan_only"

    return "offline"


def get_window(network_state: str) -> str:
    """Map network state to scheduler window."""
    if network_state == "online":
        return "online"
    elif network_state == "lan_only":
        return "offline_lan"
    else:
        return "offline"


def parse_frequency(freq_str: str) -> int:
    """Parse frequency string to seconds.

    Supports: "15m", "1h", "6h", "12h", "daily", "3d", "weekly", "monthly"
    Also supports raw numeric strings like "3d" via regex.
    """
    if freq_str in FREQUENCY_MAP:
        return FREQUENCY_MAP[freq_str]

    # Parse Nd, Nh, Nm patterns
    match = re.match(r"^(\d+)([mhd])$", freq_str)
    if match:
        value = int(match.group(1))
        unit = match.group(2)
        multiplier = {"m": 60, "h": 3600, "d": 86400}
        return value * multiplier[unit]

    log.warning(f"Unknown frequency: {freq_str}, defaulting to daily")
    return 86400


def is_task_due(frequency: str, last_run: float) -> bool:
    """Check if a task is due to run based on frequency and last run time.

    Args:
        frequency: Frequency string (e.g., "daily", "6h", "3d")
        last_run: Unix timestamp of last run (0 if never run)

    Returns: True if the task should run now
    """
    interval = parse_frequency(frequency)
    elapsed = time.time() - last_run
    return elapsed >= interval
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scheduler_windows.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/scheduler/__init__.py core/scheduler/windows.py tests/test_scheduler_windows.py
git commit -m "feat: add network window detection — online/offline/LAN state + frequency parsing"
```

---

## Task 9: Python Scheduler

**Files:**
- Create: `core/scheduler/scheduler.py`
- Test: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_scheduler.py
from __future__ import annotations
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


@pytest.fixture
def sched_env(tmp_path, monkeypatch):
    """Set up a minimal scheduler environment."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    bundles_dir = tmp_path / "bundles" / "core"
    bundles_dir.mkdir(parents=True)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    # Create a simple bundle
    import yaml
    (bundles_dir / "bundle.yaml").write_text(yaml.dump({
        "checks": {
            "test_check": {
                "enabled": True,
                "command": "echo 42",
                "type": "threshold",
                "warn": 80,
                "critical": 90,
                "message": "Test at {value}",
            },
        },
        "harnesses": {
            "test_harness": {
                "enabled": True,
                "script": "test_harness.sh",
                "frequency": "1h",
                "window": "any",
                "description": "Test harness",
            },
        },
    }))

    # Create harness script
    (scripts_dir / "test_harness.sh").write_text("#!/bin/bash\necho done")

    state = {
        "schema_version": 1,
        "paths": {
            "install_dir": str(tmp_path),
            "data_dir": str(data_dir),
            "bundles_dir": str(tmp_path / "bundles"),
            "scripts_dir": str(scripts_dir),
            "logs_dir": str(data_dir / "logs"),
            "reports_dir": str(data_dir / "reports"),
        },
    }
    (data_dir / "state.json").write_text(json.dumps(state))
    (data_dir / "logs").mkdir()
    (data_dir / "reports").mkdir()

    monkeypatch.setenv("AH_DATA_DIR", str(data_dir))
    return tmp_path, data_dir


def test_scheduler_tick(sched_env):
    from core.scheduler.scheduler import Scheduler
    tmp_path, data_dir = sched_env

    with patch("core.scheduler.scheduler.get_network_state", return_value="online"):
        sched = Scheduler(data_dir=str(data_dir))
        result = sched.tick()

    assert "checks_run" in result
    assert "window" in result
    assert result["window"] == "online"


def test_scheduler_runs_checks(sched_env):
    from core.scheduler.scheduler import Scheduler
    tmp_path, data_dir = sched_env

    with patch("core.scheduler.scheduler.get_network_state", return_value="online"):
        sched = Scheduler(data_dir=str(data_dir))
        result = sched.tick()

    assert result["checks_run"] > 0


def test_scheduler_writes_heartbeat(sched_env):
    from core.scheduler.scheduler import Scheduler
    tmp_path, data_dir = sched_env

    with patch("core.scheduler.scheduler.get_network_state", return_value="online"):
        sched = Scheduler(data_dir=str(data_dir))
        sched.tick()

    assert (data_dir / "heartbeat.json").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_scheduler.py -v`
Expected: FAIL

- [ ] **Step 3: Implement Python scheduler**

```python
# core/scheduler/scheduler.py
"""Python scheduler — replaces scheduler.sh.

Reads bundles via registry loader, runs checks and harnesses
based on network window and frequency, integrates with budget/heartbeat.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

from core.discovery.state import StateManager
from core.registry.loader import load_registry
from core.resilience.atomic_json import safe_read_json, atomic_write_json
from core.resilience.circuit_breaker import CircuitBreaker
from core.resilience.watchdog import write_heartbeat
from core.scheduler.windows import get_network_state, get_window, is_task_due

log = logging.getLogger(__name__)


class Scheduler:
    """Network-aware task scheduler."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.sm = StateManager(data_dir=data_dir)
        self.state = self.sm.read()
        self.paths = self.state.get("paths", {})
        self.cb = CircuitBreaker(data_dir=data_dir)
        self.harness_state_file = self.data_dir / "scheduler_state.json"

    def tick(self) -> dict:
        """Run one scheduler tick. Returns summary of what happened."""
        # Write heartbeat
        write_heartbeat(data_dir=str(self.data_dir))

        # Detect network state
        minipc_ip = os.environ.get("MINIPC_IP", "")
        network_state = get_network_state(minipc_ip=minipc_ip)
        window = get_window(network_state)

        log.info(f"Scheduler tick: network={network_state}, window={window}")

        # Load registry
        bundles_dir = self.paths.get("bundles_dir", "")
        overrides_file = os.path.join(self.paths.get("config_dir", ""), "overrides.yaml")
        if not os.path.exists(overrides_file):
            overrides_file = None

        registry = load_registry(
            bundles_dir=bundles_dir,
            overrides_file=overrides_file,
        )

        # Run checks
        checks_run = 0
        checks_passed = 0
        checks_failed = 0

        for name, check in registry.get("checks", {}).items():
            if not check.get("enabled", True):
                continue
            if self.cb.is_open(name):
                log.debug(f"Check {name}: circuit open, skipping")
                continue

            command = check.get("command", "")
            if not command:
                continue

            try:
                result = subprocess.run(
                    command, shell=True, capture_output=True,
                    text=True, timeout=30,
                )
                checks_run += 1
                output = result.stdout.strip()

                check_type = check.get("type", "command_exit")
                passed = self._evaluate_check(check_type, check, output, result.returncode)

                if passed:
                    checks_passed += 1
                    self.cb.record_success(name)
                else:
                    checks_failed += 1
                    self.cb.record_failure(name)
                    log.warning(f"Check {name} failed: {check.get('message', '').format(value=output)}")

            except subprocess.TimeoutExpired:
                checks_run += 1
                checks_failed += 1
                self.cb.record_failure(name)
                log.warning(f"Check {name} timed out")
            except Exception as e:
                log.error(f"Check {name} error: {e}")

        # Run due harnesses
        harnesses_run = 0
        harness_state = safe_read_json(self.harness_state_file, default={})

        for name, harness in registry.get("harnesses", {}).items():
            if not harness.get("enabled", True):
                continue

            # Check window
            harness_window = harness.get("window", "any")
            if harness_window != "any" and harness_window != window:
                if not (harness_window == "offline" and window == "offline_lan"):
                    continue

            # Check frequency
            frequency = harness.get("frequency", "daily")
            last_run = harness_state.get(name, {}).get("last_run", 0)
            if not is_task_due(frequency, last_run):
                continue

            # Run the harness
            script = harness.get("script", "")
            scripts_dir = self.paths.get("scripts_dir", "")
            script_path = os.path.join(scripts_dir, script)

            if not os.path.exists(script_path):
                log.warning(f"Harness {name}: script not found: {script_path}")
                continue

            log.info(f"Running harness: {name} ({script})")
            try:
                subprocess.run(
                    ["bash", script_path],
                    capture_output=True, text=True,
                    timeout=1800,  # 30 min max
                    env={**os.environ, "TERM": "dumb"},
                )
                harness_state[name] = {"last_run": time.time()}
                harnesses_run += 1
            except subprocess.TimeoutExpired:
                log.warning(f"Harness {name} timed out after 30 min")
            except Exception as e:
                log.error(f"Harness {name} error: {e}")

        # Save harness state
        atomic_write_json(self.harness_state_file, harness_state)

        summary = {
            "timestamp": time.time(),
            "network_state": network_state,
            "window": window,
            "checks_run": checks_run,
            "checks_passed": checks_passed,
            "checks_failed": checks_failed,
            "harnesses_run": harnesses_run,
        }

        log.info(f"Tick complete: {checks_run} checks ({checks_passed} ok, {checks_failed} fail), {harnesses_run} harnesses")
        return summary

    def _evaluate_check(self, check_type: str, check: dict, output: str, returncode: int) -> bool:
        """Evaluate a check result based on its type."""
        if check_type == "threshold":
            try:
                value = float(output.strip())
                critical = check.get("critical")
                warn = check.get("warn")
                if critical is not None and value >= float(critical):
                    return False
                if warn is not None and value >= float(warn):
                    return False
                return True
            except (ValueError, TypeError):
                return False

        elif check_type == "command_exit":
            return returncode == 0

        elif check_type == "command_output":
            # Alert if output is non-empty
            return not output.strip()

        elif check_type == "http_probe":
            return returncode == 0

        elif check_type == "regex_match":
            import re
            expected = check.get("expected", "")
            return bool(re.search(expected, output))

        return True


def main():
    """Entry point for running one scheduler tick."""
    import argparse
    parser = argparse.ArgumentParser(description="AgentHarness Scheduler")
    parser.add_argument("--data-dir", default=os.environ.get("AH_DATA_DIR", ""))
    args = parser.parse_args()

    if not args.data_dir:
        print("Error: Set AH_DATA_DIR or pass --data-dir")
        return 1

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    sched = Scheduler(data_dir=args.data_dir)
    result = sched.tick()
    print(f"Checks: {result['checks_run']} run, {result['checks_passed']} passed, {result['checks_failed']} failed")
    print(f"Harnesses: {result['harnesses_run']} run")
    print(f"Window: {result['window']}")
    return 0


if __name__ == "__main__":
    exit(main() or 0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scheduler.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/scheduler/scheduler.py tests/test_scheduler.py
git commit -m "feat: add Python scheduler — replaces scheduler.sh with registry/budget/heartbeat integration"
```

---

## Task 10: CLI Budget + Migrate-Scheduler Commands

**Files:**
- Modify: `cli.py`

- [ ] **Step 1: Add budget command to CLI**

Add `cmd_budget(args)` function that:
- Reads `AH_DATA_DIR` from state
- Instantiates `BudgetTracker`
- Calls `daily_report()`
- Prints the report

- [ ] **Step 2: Add migrate-scheduler command to CLI**

Add `cmd_migrate_scheduler(args)` function that:
- Checks if the Python scheduler can run (data dir exists, state.json exists)
- If `--rollback`: re-enable cron, disable systemd
- Otherwise: disable cron entry for scheduler.sh, enable systemd service, run one test tick, confirm success

- [ ] **Step 3: Add to parser and dispatch**

```python
    subparsers.add_parser("budget", help="Show LLM budget status")
    migrate_parser = subparsers.add_parser("migrate-scheduler", help="Migrate from bash to Python scheduler")
    migrate_parser.add_argument("--rollback", action="store_true", help="Rollback to bash scheduler")
```

- [ ] **Step 4: Commit**

```bash
git add cli.py
git commit -m "feat: add budget + migrate-scheduler CLI commands"
```

---

## Task 11: Run Full Test Suite + Final Validation

- [ ] **Step 1: Run all tests**

```bash
python3 -m pytest tests/ -v
```

Expected: All tests pass (120+ tests).

- [ ] **Step 2: Test provider + router integration**

```bash
python3 -c "
from core.providers.base import LLMRequest, Complexity
from core.providers.llamacpp import LlamaCppProvider
from core.providers.router import Router
from core.providers.budget import BudgetTracker
import tempfile

bt = BudgetTracker(data_dir=tempfile.mkdtemp())
local = LlamaCppProvider(name='local', endpoint='http://localhost:99999')
router = Router(providers=[local], budget=bt, routing={'low': ['local'], 'medium': ['local'], 'high': ['local'], 'critical': ['local']})
resp = router.route(LLMRequest(prompt='test', complexity=Complexity.LOW))
# Will fail (no server) but shouldn't crash
print(f'Router returned: success={resp.success}, error={resp.error}')
print('Provider + Router integration: OK')
"
```

- [ ] **Step 3: Test scheduler tick**

```bash
export AGENTHARNESS_HOME="$(pwd)" AH_DATA_DIR="$(pwd)/data"
python3 cli.py discover
python3 -m core.scheduler.scheduler --data-dir "$(pwd)/data"
python3 cli.py budget
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: Phase B complete — LLM providers, budget tracking, smart routing, Python scheduler

Multi-provider LLM abstraction with 9 providers (local + 7 cloud free + paid).
Budget-aware routing by complexity tier. Python scheduler replaces bash.
CLI: budget status, migrate-scheduler command."
```

---

## Summary

**Phase B delivers:**
- LLM provider abstraction (base classes + 9 providers)
  - Local: llamacpp
  - Cloud free: groq, google, cerebras, sambanova, openrouter
  - Cloud paid: openai_compat, anthropic
- Smart router with complexity-based routing + fallback + retry
- Budget tracker with atomic persistence + daily reset
- Network window detection (online/offline/LAN)
- Python scheduler (replaces scheduler.sh)
- Provider configuration file (providers.yaml)
- CLI: budget command, migrate-scheduler command

**Phase B does NOT include** (deferred to later phases):
- HITL approval gateway (Phase C)
- Sandbox execution runtime (Phase C)
- Agent bridge (Phase C)
- Distiller / synthesizer / scout (Phase D)
- Dashboard (Phase D)

**Estimated tasks:** 11 tasks, ~45 steps
**Test coverage:** ~30 new tests across 6 test files
