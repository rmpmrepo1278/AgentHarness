"""Tests for core.providers.router — smart LLM routing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.providers.base import BudgetStatus, Complexity, LLMRequest, LLMResponse
from core.providers.budget import BudgetTracker
from core.providers.router import Router


def _make_provider(name, tier, available=True, budget_ok=True):
    """Create a mock LLMProvider with sensible defaults."""
    p = MagicMock()
    p.name = name
    p.tier = tier
    p.enabled = True
    p.is_available.return_value = available
    p.budget_status.return_value = BudgetStatus(
        estimated_remaining=100 if budget_ok else 0,
    )
    p.complete.return_value = LLMResponse(
        text=f"from {name}",
        provider=name,
        model="mock",
        success=True,
        tokens_in=10,
        tokens_out=5,
    )
    return p


@pytest.fixture()
def data_dir(tmp_path):
    return str(tmp_path)


def test_route_low_prefers_local(data_dir):
    """LOW complexity routes to local_small first."""
    local = _make_provider("local_small", tier=1)
    groq = _make_provider("groq", tier=2)

    router = Router(
        providers=[local, groq],
        budget=BudgetTracker(data_dir),
        routing={Complexity.LOW.value: ["local_small", "groq"]},
    )

    req = LLMRequest(prompt="hello", complexity=Complexity.LOW)
    resp = router.route(req)

    assert resp.success is True
    assert resp.provider == "local_small"
    local.complete.assert_called_once()
    groq.complete.assert_not_called()


def test_route_falls_through_on_unavailable(data_dir):
    """local unavailable, routes to groq."""
    local = _make_provider("local_small", tier=1, available=False)
    groq = _make_provider("groq", tier=2)

    router = Router(
        providers=[local, groq],
        budget=BudgetTracker(data_dir),
        routing={Complexity.LOW.value: ["local_small", "groq"]},
    )

    req = LLMRequest(prompt="hello", complexity=Complexity.LOW)
    resp = router.route(req)

    assert resp.success is True
    assert resp.provider == "groq"


def test_route_skips_exhausted_budget(data_dir):
    """groq budget=0, skips to google."""
    local = _make_provider("local_small", tier=1, available=False)
    groq = _make_provider("groq", tier=2, budget_ok=False)
    google = _make_provider("google", tier=3)

    router = Router(
        providers=[local, groq, google],
        budget=BudgetTracker(data_dir),
        routing={Complexity.LOW.value: ["local_small", "groq", "google"]},
    )

    req = LLMRequest(prompt="hello", complexity=Complexity.LOW)
    resp = router.route(req)

    assert resp.success is True
    assert resp.provider == "google"
    groq.complete.assert_not_called()


def test_route_all_exhausted_returns_error(data_dir):
    """All unavailable, returns error with success=False."""
    local = _make_provider("local_small", tier=1, available=False)
    groq = _make_provider("groq", tier=2, available=False)

    router = Router(
        providers=[local, groq],
        budget=BudgetTracker(data_dir),
        routing={Complexity.LOW.value: ["local_small", "groq"]},
    )

    req = LLMRequest(prompt="hello", complexity=Complexity.LOW)
    resp = router.route(req)

    assert resp.success is False
    assert "No provider available" in resp.error


def test_route_records_budget_on_success(data_dir):
    """After successful route, budget.get_usage shows requests==1."""
    local = _make_provider("local_small", tier=1)
    budget = BudgetTracker(data_dir)

    router = Router(
        providers=[local],
        budget=budget,
        routing={Complexity.LOW.value: ["local_small"]},
    )

    req = LLMRequest(prompt="hello", complexity=Complexity.LOW)
    resp = router.route(req)

    assert resp.success is True
    usage = budget.get_usage("local_small")
    assert usage["requests"] == 1
