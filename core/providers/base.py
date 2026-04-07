"""Base classes and data types for LLM providers."""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional


class Complexity(enum.Enum):
    """Task complexity levels for routing decisions."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class BudgetStatus:
    """Tracks remaining budget/quota for an LLM provider."""

    known_remaining: Optional[int] = None
    estimated_remaining: Optional[int] = None
    reset_at: Optional[datetime] = None
    cost_model: str = "free"


@dataclass
class LLMResponse:
    """Response from an LLM provider."""

    text: str
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out


def _llm_response_error(provider: str, message: str) -> LLMResponse:
    """Factory for error responses."""
    return LLMResponse(
        text="",
        provider=provider,
        model="",
        success=False,
        error=message,
    )


# Attach as a class-level callable that doesn't shadow the instance 'error' field.
LLMResponse.error = staticmethod(_llm_response_error)  # type: ignore[attr-defined]


@dataclass
class LLMRequest:
    """Request to an LLM provider."""

    prompt: str
    max_tokens: int = 1024
    temperature: float = 0.7
    complexity: Complexity = Complexity.MEDIUM
    system_prompt: Optional[str] = None
    tool_name: Optional[str] = None


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    def __init__(self, name: str, tier: int, model: str, **kwargs: Any) -> None:
        self.name = name
        self.tier = tier
        self.model = model

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        """Send a completion request to the provider."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider is currently available."""

    @abstractmethod
    def budget_status(self) -> BudgetStatus:
        """Return current budget/quota status."""

    def capabilities(self) -> List[str]:
        """Return list of provider capabilities."""
        return []
