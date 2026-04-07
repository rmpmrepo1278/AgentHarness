"""Circuit breaker for health checks.

When a check fails N consecutive times the circuit *opens* and the check is
suppressed.  It re-closes on a recorded success or a manual reset.
"""
from __future__ import annotations

import os
from typing import Dict, List

from core.resilience.atomic_json import atomic_write_json, safe_read_json


class CircuitBreaker:
    """Track consecutive failures per health-check and open/close circuits."""

    def __init__(self, data_dir: str, max_failures: int = 5) -> None:
        self._data_dir = data_dir
        self._max_failures = max_failures
        self._path = os.path.join(data_dir, "circuit_breaker.json")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> Dict[str, int]:
        """Return ``{check_name: consecutive_failure_count, ...}``."""
        data = safe_read_json(self._path)
        if not isinstance(data, dict):
            return {}
        return data

    def _save(self, state: Dict[str, int]) -> None:
        atomic_write_json(self._path, state)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_failure(self, check_name: str) -> None:
        """Increment the consecutive-failure counter for *check_name*."""
        state = self._load()
        state[check_name] = state.get(check_name, 0) + 1
        self._save(state)

    def record_success(self, check_name: str) -> None:
        """Reset the failure counter for *check_name* (close the circuit)."""
        state = self._load()
        if check_name in state:
            del state[check_name]
            self._save(state)

    def is_open(self, check_name: str) -> bool:
        """Return ``True`` when *check_name* has reached *max_failures*."""
        state = self._load()
        return state.get(check_name, 0) >= self._max_failures

    def reset(self, check_name: str) -> None:
        """Manually close the circuit for *check_name*."""
        self.record_success(check_name)

    def reset_all(self) -> None:
        """Close every circuit."""
        self._save({})

    def get_open_circuits(self) -> List[str]:
        """Return a list of all check names whose circuits are open."""
        state = self._load()
        return [name for name, count in state.items()
                if count >= self._max_failures]
