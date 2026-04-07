"""Tests for LLM provider base classes and data types."""
from __future__ import annotations

import pytest
from datetime import datetime

from core.providers.base import (
    BudgetStatus,
    Complexity,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)


def test_budget_status_defaults():
    status = BudgetStatus()
    assert status.known_remaining is None
    assert status.estimated_remaining is None
    assert status.reset_at is None
    assert status.cost_model == "free"


def test_budget_status_custom():
    status = BudgetStatus(known_remaining=150, cost_model="per_request")
    assert status.known_remaining == 150
    assert status.cost_model == "per_request"
    assert status.estimated_remaining is None
    assert status.reset_at is None


def test_llm_response():
    resp = LLMResponse(
        text="hello",
        provider="groq",
        model="llama3",
        tokens_in=10,
        tokens_out=5,
        latency_ms=42.0,
    )
    assert resp.total_tokens == 15
    assert resp.success is True
    assert resp.error is None


def test_llm_response_error():
    resp = LLMResponse.error("groq", "Rate limited")
    assert resp.success is False
    assert resp.error == "Rate limited"
    assert resp.provider == "groq"
    assert resp.text == ""


def test_complexity_enum():
    assert Complexity.LOW.value == "low"
    assert Complexity.MEDIUM.value == "medium"
    assert Complexity.HIGH.value == "high"
    assert Complexity.CRITICAL.value == "critical"


def test_provider_interface_requires_methods():
    with pytest.raises(TypeError):
        LLMProvider(name="test", tier=1, model="m")  # type: ignore[abstract]
