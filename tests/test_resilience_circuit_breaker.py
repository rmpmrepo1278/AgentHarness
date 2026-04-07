"""Tests for CircuitBreaker."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core.resilience.circuit_breaker import CircuitBreaker


@pytest.fixture
def cb(tmp_path: Path) -> CircuitBreaker:
    return CircuitBreaker(data_dir=str(tmp_path), max_failures=3)


def test_record_failure_and_check_open(cb: CircuitBreaker) -> None:
    """3 failures with max_failures=3 opens circuit."""
    assert not cb.is_open("dns")
    cb.record_failure("dns")
    cb.record_failure("dns")
    assert not cb.is_open("dns")
    cb.record_failure("dns")
    assert cb.is_open("dns")


def test_record_success_resets(cb: CircuitBreaker) -> None:
    """Success after open circuit closes it."""
    for _ in range(3):
        cb.record_failure("disk")
    assert cb.is_open("disk")
    cb.record_success("disk")
    assert not cb.is_open("disk")


def test_reset_check(cb: CircuitBreaker) -> None:
    """Manual reset closes circuit."""
    for _ in range(3):
        cb.record_failure("net")
    assert cb.is_open("net")
    cb.reset("net")
    assert not cb.is_open("net")


def test_unknown_check_not_open(cb: CircuitBreaker) -> None:
    """Never-seen check is not open."""
    assert not cb.is_open("never_registered")


def test_get_open_circuits(cb: CircuitBreaker) -> None:
    """Returns list of all open circuits."""
    for _ in range(3):
        cb.record_failure("a")
        cb.record_failure("b")
    cb.record_failure("c")  # only 1 failure, not open
    open_list = cb.get_open_circuits()
    assert sorted(open_list) == ["a", "b"]
