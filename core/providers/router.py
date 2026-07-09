"""Smart LLM router — routes requests by complexity, budget, and availability."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

from core.providers.base import (
    Complexity,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)
from core.providers.budget import BudgetTracker
from core.providers.circuit_breaker import (
    is_available as cb_is_available,
    record_failure,
    record_success,
)
from core.providers.rate_limit_tracker import RateLimitTracker

logger = logging.getLogger(__name__)

# ponytail: module-level failure counter, exposed as a helper; per-provider
# locks if this ever runs multi-process.
_failure_counts: Dict[str, int] = {}


def get_failure_counts() -> Dict[str, int]:
    return dict(_failure_counts)

# Default routing order: complexity -> list of provider names in priority order.
_DEFAULT_ROUTING: Dict[str, List[str]] = {
    Complexity.LOW.value: ["local_small"],
    Complexity.MEDIUM.value: ["local_small", "groq", "google"],
    Complexity.HIGH.value: ["groq", "google", "openrouter"],
    Complexity.CRITICAL.value: ["google", "openrouter", "anthropic"],
}


class Router:
    """Route LLM requests to the best available provider.

    Selection logic per candidate (in priority order):
    1. Skip if not enabled.
    2. Skip if circuit breaker OPEN (but not DEGRADED/HALF_OPEN).
    3. Skip if budget_status().estimated_remaining is not None and <= 0.
    4. Call complete(request).
    5. On success: record_usage in budget, reset circuit breaker, return response.
    6. On 429 in error text: log, set circuit breaker, and skip to next.
    7. On 401/403 in error text: disable provider and skip.
    8. On other error: skip to next.
    9. If all exhausted: return an error LLMResponse.
    """

    def __init__(
        self,
        providers: List[LLMProvider],
        budget: BudgetTracker,
        routing: Optional[Dict[str, List[str]]] = None,
        policies: Optional[List[Dict[str, Any]]] = None,
        max_retries: int = 3,
        rate_limit_tracker: Optional[RateLimitTracker] = None,
    ) -> None:
        self._providers_by_name: Dict[str, LLMProvider] = {p.name: p for p in providers}
        self._budget = budget
        self._routing = routing or _DEFAULT_ROUTING
        self._policies = policies or []
        self._max_retries = max_retries
        self._rate_limit_tracker = rate_limit_tracker  # Used for cooldown-aware retry

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

        # 1. Skip if not enabled.
        if not getattr(provider, "enabled", True):
            return None

        # 2. Circuit breaker check — skip if OPEN (but DEGRADED/HALF_OPEN can probe).
        # Providers like Google/OAI are OAuth (auth_category="oauth"); others are apikey.
        auth_category = "oauth" if provider.name in ("google", "anthropic", "openai") else "apikey"
        if not cb_is_available(provider.name, auth_category):
            logger.debug("Provider %s circuit breaker OPEN, skipping", provider.name)
            return None

        # 3. Skip if budget exhausted.
        status = provider.budget_status()
        if status.estimated_remaining is not None and status.estimated_remaining <= 0:
            logger.info("Skipping %s: budget exhausted", provider.name)
            return None

        # 4. Call complete.
        response = provider.complete(request)

        # Track consecutive failures for the health_probe status view.
        if not response.success:
            _failure_counts[provider.name] = _failure_counts.get(provider.name, 0) + 1
        else:
            _failure_counts.pop(provider.name, None)

        # 4b. Treat empty/whitespace-only content as a failure so the
        # router falls through to the next provider. Some free-tier
        # models (e.g. owl-alpha) return HTTP 200 with empty content on
        # simple prompts — without this, the empty string is returned
        # as the final answer and Hermes has no fallback to retry.
        if response.success and not (response.text or "").strip():
            logger.warning(
                "Provider %s returned empty content, skipping", provider.name
            )
            record_failure(provider.name, auth_category)
            return None

        if response.success:
            # 5. Record usage, reset circuit breaker on success, and return.
            self._budget.record_usage(
                provider.name,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                success=True,
            )
            record_success(provider.name, auth_category)
            return response

        # 6. Failure path — inspect error text.
        err = response.error or ""

        if "429" in err:
            # Rate-limited — record failure and check for cooldown retry.
            logger.warning("Provider %s returned 429, skipping", provider.name)
            record_failure(provider.name, auth_category)
            # Check if we should wait for cooldown (OmniRoute-style).
            retry_info = _check_cooldown_retry(provider.name, auth_category)
            if retry_info and retry_info.get("should_wait"):
                logger.info(
                    "Provider %s in short cooldown (%.1fs) — could wait if retry allowed",
                    provider.name,
                    retry_info.get("wait_ms", 0) / 1000
                )
            return None

        if "401" in err or "403" in err:
            # 7. Auth failure — disable.
            logger.error("Provider %s returned auth error, disabling", provider.name)
            provider.enabled = False  # type: ignore[attr-defined]
            record_failure(provider.name, auth_category)
            return None

        # 8. Other error — skip to next.
        logger.warning("Provider %s error: %s", provider.name, err)
        record_failure(provider.name, auth_category)
        return None

    def _check_cooldown_retry(self, provider: str, auth_category: str) -> Optional[dict]:
        """
        Check if provider is in short cooldown we could wait for.
        Mirror of OmniRoute's CooldownAwareRetry decision.
        Returns {wait_ms, should_wait} or None if no wait.
        """
        # Get rate limit tracker for cooldown info
        tracker = getattr(self, "_rate_limit_tracker", None)
        if tracker is None:
            return None

        remaining_ms = tracker.get_cooldown_remaining(provider)

        # Configure retry thresholds (mirror OmniRoute: maxWaitMs=5000, maxAttempts=2)
        max_wait_ms = int(os.environ.get("CB_COOLDOWN_MAX_WAIT_MS", "5000"))
        if remaining_ms > 0 and remaining_ms <= max_wait_ms:
            return {"wait_ms": remaining_ms, "should_wait": True}
        return {"wait_ms": 0, "should_wait": False}

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
