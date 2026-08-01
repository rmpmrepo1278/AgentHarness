"""
Circuit Breaker — State machine for provider resilience.

Implements CLOSED → DEGRADED → OPEN → HALF_OPEN states with:
- Configurable failure thresholds per auth type (OAuth vs API Key)
- Exponential backoff escalation on repeated open cycles
- Transition history for diagnostics
- Per-provider state with decay-based recovery
"""

from __future__ import annotations

import json
import logging
import os
import time
from enum import Enum
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────

# OAuth (session-based providers like Google, GitHub) - more sensitive
OAUTH_FAILURE_THRESHOLD = int(os.environ.get("CB_OAUTH_THRESHOLD", "8"))
OAUTH_DEGRADATION_THRESHOLD = int(os.environ.get("CB_OAUTH_DEGRADATION", "5"))
OAUTH_RESET_TIMEOUT_MS = int(os.environ.get("CB_OAUTH_RESET_MS", "60000"))  # 1 min

# API Key providers - more resilient
APIKEY_FAILURE_THRESHOLD = int(os.environ.get("CB_APIKEY_THRESHOLD", "12"))
APIKEY_DEGRADATION_THRESHOLD = int(os.environ.get("CB_APIKEY_DEGRADATION", "7"))
APIKEY_RESET_TIMEOUT_MS = int(os.environ.get("CB_APIKEY_RESET_MS", "30000"))  # 30 sec

STATE_FILE = Path("/home/rohit/agentharness/data/circuit_breaker_state.json")
LOCK_FILE = Path("/home/rohit/agentharness/data/circuit_breaker_state.lock")


class CircuitState(Enum):
    CLOSED = "CLOSED"
    DEGRADED = "DEGRADED"  # Warning state before open
    OPEN = "OPEN"  # Requests short-circuited
    HALF_OPEN = "HALF_OPEN"  # Probing for recovery


# ── State ───────────────────────────────────────────────────────────────────

_circuit_state: dict[str, dict] = {}
_transition_history: list[dict] = []


def _load():
    global _circuit_state, _transition_history
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text())
            _circuit_state = data.get("state", {})
            _transition_history = data.get("history", [])
    except Exception:
        _circuit_state = {}
        _transition_history = []


def _save():
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "state": _circuit_state,
            "history": _transition_history[-100:],  # Keep last 100 transitions
        }
        STATE_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


# Load on module import
_load()


def _get_thresholds(provider: str, auth_category: str) -> tuple[int, int, int]:
    """Get failure thresholds for provider based on auth type."""
    # Infer auth category from provider name
    auth = auth_category or ("oauth" if provider in ("google", "anthropic", "openai") else "apikey")
    if auth == "oauth":
        return OAUTH_FAILURE_THRESHOLD, OAUTH_DEGRADATION_THRESHOLD, OAUTH_RESET_TIMEOUT_MS
    return APIKEY_FAILURE_THRESHOLD, APIKEY_DEGRADATION_THRESHOLD, APIKEY_RESET_TIMEOUT_MS


def _record_transition(provider: str, from_state: str, to_state: str, reason: str = ""):
    """Record state transition for diagnostics."""
    global _transition_history
    _transition_history.append({
        "provider": provider,
        "from": from_state,
        "to": to_state,
        "timestamp": time.time(),
        "reason": reason,
    })
    _save()


def get_state(provider: str) -> CircuitState:
    """Get current circuit state for a provider."""
    entry = _circuit_state.get(provider, {})
    state = entry.get("state", "CLOSED")
    try:
        return CircuitState(state)
    except ValueError:
        return CircuitState.CLOSED


def get_failure_count(provider: str) -> int:
    """Get consecutive failure count for a provider."""
    entry = _circuit_state.get(provider, {})
    return entry.get("failure_count", 0)


def is_available(provider: str, auth_category: str = "apikey") -> bool:
    """
    Check if provider is available (CLOSED or DEGRADED or HALF_OPEN).
    OPEN state returns False.
    """
    state = get_state(provider)

    # Check if OPEN cooldown has expired
    if state == CircuitState.OPEN:
        entry = _circuit_state.get(provider, {})
        open_time = entry.get("open_time", 0)
        _, _, reset_timeout = _get_thresholds(provider, auth_category)

        # Calculate backoff with escalation
        open_cycles = entry.get("open_cycles", 0)
        backoff_multiplier = min(2 ** open_cycles, 16)  # Max 16x escalation
        effective_timeout = reset_timeout * backoff_multiplier

        if time.time() - open_time < effective_timeout / 1000:
            return False
        # Ready to probe
        set_state(provider, CircuitState.HALF_OPEN, "cooldown expired")
        return True

    return state != CircuitState.OPEN


def record_failure(provider: str, auth_category: str = "apikey"):
    """Record a failure and potentially change state."""
    entry = _circuit_state.setdefault(provider, {
        "state": "CLOSED",
        "failure_count": 0,
        "open_cycles": 0,
    })

    state = get_state(provider)
    failure_threshold, degradation_threshold, _ = _get_thresholds(provider, auth_category)

    # Don't count failures when already open
    if state == CircuitState.OPEN:
        return

    entry["failure_count"] = entry.get("failure_count", 0) + 1
    entry["last_failure"] = time.time()

    if state == CircuitState.HALF_OPEN:
        # Any failure in HALF_OPEN triggers re-open
        set_state(provider, CircuitState.OPEN, "probe failed")
        return

    # Check thresholds
    if entry["failure_count"] >= failure_threshold:
        set_state(provider, CircuitState.OPEN, "threshold exceeded")
    elif entry["failure_count"] >= degradation_threshold:
        set_state(provider, CircuitState.DEGRADED, "degradation threshold")


def record_success(provider: str, auth_category: str = "apikey"):
    """Record a success and potentially reset state."""
    entry = _circuit_state.setdefault(provider, {
        "state": "CLOSED",
        "failure_count": 0,
        "open_cycles": 0,
    })

    state = get_state(provider)

    if state == CircuitState.HALF_OPEN:
        # Success in HALF_OPEN resets to CLOSED
        entry["failure_count"] = 0
        entry["open_cycles"] = 0
        _record_transition(provider, "HALF_OPEN", "CLOSED", "probe succeeded")
    elif state == CircuitState.OPEN:
        # Success in OPEN resets (shouldn't normally happen)
        entry["failure_count"] = 0
        entry["open_cycles"] = 0
        _record_transition(provider, "OPEN", "CLOSED", "unexpected success")
    else:
        # Normal success - decay failure count
        entry["failure_count"] = max(0, entry.get("failure_count", 0) - 1)

    entry["state"] = state.value if state != CircuitState.HALF_OPEN else "CLOSED"
    _save()


def set_state(provider: str, new_state: CircuitState, reason: str = ""):
    """Set state and record transition."""
    entry = _circuit_state.setdefault(provider, {
        "state": "CLOSED",
        "failure_count": 0,
        "open_cycles": 0,
    })

    old_state = get_state(provider).value

    if old_state == new_state.value:
        return

    _record_transition(provider, old_state, new_state.value, reason)

    entry["state"] = new_state.value

    if new_state == CircuitState.OPEN:
        entry["open_time"] = time.time()
        entry["open_cycles"] = entry.get("open_cycles", 0) + 1
    elif new_state == CircuitState.CLOSED:
        entry["failure_count"] = 0
        entry["open_cycles"] = 0

    _save()


def get_all_states() -> dict:
    """Get all circuit breaker states for monitoring."""
    return {
        provider: {
            "state": get_state(provider).value,
            "failure_count": get_failure_count(provider),
            "available": is_available(provider),
        }
        for provider in _circuit_state
    }


def reset(provider: str | None = None):
    """Reset state for a provider or all."""
    global _circuit_state
    if provider:
        _circuit_state.pop(provider, None)
    else:
        _circuit_state = {}
    _save()