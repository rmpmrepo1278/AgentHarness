"""Smart LLM router — routes requests by complexity, budget, and availability."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from core.providers.base import (
    Complexity,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)
from core.providers.budget import BudgetTracker

logger = logging.getLogger(__name__)

# Default routing order: complexity -> list of provider names in priority order.
_DEFAULT_ROUTING = {                                                                                                                           
    Complexity.LOW.value: ["local", "google-alt", "groq", "cerebras", "sambanova", "together", "fireworks"],
    Complexity.MEDIUM.value: ["google-alt", "groq", "cerebras", "sambanova", "together", "fireworks", "local", "google"],                                         
    Complexity.HIGH.value: ["google-alt", "groq", "cerebras", "openrouter", "sambanova", "together", "fireworks", "local", "google"],
    Complexity.CRITICAL.value: ["google-alt", "groq", "openrouter", "cerebras", "local", "google", "anthropic"],
} 


class Router:
    """Route LLM requests to the best available provider.

    Selection logic per candidate (in priority order):
    1. Skip if not enabled or not available.
    2. Skip if budget_status().estimated_remaining is not None and <= 0.
    3. Call complete(request).
    4. On success: record_usage in budget, return response.
    5. On 429 in error text: log and skip to next.
    6. On 401/403 in error text: disable provider and skip.
    7. On other error: skip to next.
    8. If all exhausted: return an error LLMResponse.
    """

    def __init__(
        self,
        providers: List[LLMProvider],
        budget: BudgetTracker,
        routing: Optional[Dict[str, List[str]]] = None,
        policies: Optional[List[Dict[str, Any]]] = None,
        max_retries: int = 3,
    ) -> None:
        self._providers_by_name: Dict[str, LLMProvider] = {p.name: p for p in providers}
        self._budget = budget
        self._routing = routing or _DEFAULT_ROUTING
        self._policies = policies or []
        self._max_retries = max_retries

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(self, request: LLMRequest) -> LLMResponse:
        """Pick the best provider and return a response."""

        # Policy override: if request.tool_name matches a policy, force that provider.
        forced = self._match_policy(request)
        if forced is not None:
            provider = self._providers_by_name.get(forced)
            if provider is not None:
                resp = self._try_provider(provider, request)
                if resp is not None:
                    return resp

        # Normal routing by complexity.
        complexity_key = request.complexity.value
        candidate_names = self._routing.get(complexity_key, [])

        for name in candidate_names:
            provider = self._providers_by_name.get(name)
            if provider is None:
                continue
            resp = self._try_provider(provider, request)
            if resp is not None:
                return resp

        return LLMResponse.error("router", "No provider available for request")  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _try_provider(self, provider: LLMProvider, request: LLMRequest) -> Optional[LLMResponse]:
        """Attempt a single provider. Return LLMResponse on success, None to skip."""

        # 1. Skip if not enabled or not available.
        if not getattr(provider, "enabled", True):
            return None
        if not provider.is_available():
            return None

        # 2. Skip if budget exhausted.
        status = provider.budget_status()
        if status.estimated_remaining is not None and status.estimated_remaining <= 0:
            logger.info("Skipping %s: budget exhausted", provider.name)
            return None

        # 3. Call complete.
        response = provider.complete(request)

        if response.success:
            # 4. Record usage and return.
            self._budget.record_usage(
                provider.name,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                success=True,
            )
            return response

        # Failure path — inspect error text.
        err = response.error or ""

        if "429" in err:
            # 5. Rate-limited.
            logger.warning("Provider %s returned 429, skipping", provider.name)
            return None

        if "401" in err or "403" in err:
            # 6. Auth failure — disable.
            logger.error("Provider %s returned auth error, disabling", provider.name)
            provider.enabled = False  # type: ignore[attr-defined]
            return None

        # 7. Other error — skip to next.
        logger.warning("Provider %s error: %s", provider.name, err)
        return None

    def _match_policy(self, request: LLMRequest) -> Optional[str]:
        """Return the provider name forced by policy, or None."""
        if request.tool_name is None:
            return None
        for policy in self._policies:
            pattern = policy.get("match", "")
            forced_provider = policy.get("provider")
            if pattern and forced_provider and re.search(pattern, request.tool_name):
                return forced_provider
        return None
