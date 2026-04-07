"""Tests for core.providers.budget — per-provider budget tracking."""

from __future__ import annotations

import pytest

from core.providers.budget import BudgetTracker


@pytest.fixture()
def data_dir(tmp_path):
    return str(tmp_path)


def test_record_and_check(data_dir):
    """Record 1 usage for groq, verify can_use and counters."""
    bt = BudgetTracker(data_dir)
    bt.record_usage("groq", tokens_in=100, tokens_out=50)

    assert bt.can_use("groq", daily_limit=200) is True
    usage = bt.get_usage("groq")
    assert usage["requests"] == 1
    assert usage["tokens_in"] == 100


def test_budget_exhausted(data_dir):
    """Record 200 usages, verify can_use returns False."""
    bt = BudgetTracker(data_dir)
    for _ in range(200):
        bt.record_usage("groq", tokens_in=10)

    assert bt.can_use("groq", daily_limit=200) is False


def test_deprioritize_at_80_pct(data_dir):
    """Record 160 usages (80 % of 200), verify deprioritize but still usable."""
    bt = BudgetTracker(data_dir)
    for _ in range(160):
        bt.record_usage("groq", tokens_in=10)

    assert bt.should_deprioritize("groq", daily_limit=200) is True
    assert bt.can_use("groq", daily_limit=200) is True


def test_daily_reset(data_dir):
    """Record usage, reset, verify counters are zero."""
    bt = BudgetTracker(data_dir)
    bt.record_usage("groq", tokens_in=100)
    bt.reset_daily()

    assert bt.get_usage("groq")["requests"] == 0


def test_daily_report(data_dir):
    """Record for groq and google, verify both appear in report."""
    bt = BudgetTracker(data_dir)
    bt.record_usage("groq", tokens_in=100)
    bt.record_usage("google", tokens_in=200)

    report = bt.daily_report()
    assert "groq" in report
    assert "google" in report


def test_persistence(data_dir):
    """Record in one instance, create new instance, verify data persists."""
    bt1 = BudgetTracker(data_dir)
    bt1.record_usage("groq", tokens_in=100, tokens_out=50)

    bt2 = BudgetTracker(data_dir)
    usage = bt2.get_usage("groq")
    assert usage["requests"] == 1
    assert usage["tokens_in"] == 100
    assert usage["tokens_out"] == 50
