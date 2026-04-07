"""Per-provider LLM budget tracking with atomic persistence.

Tracks daily request counts and token usage per provider, persists to JSON,
and auto-resets when the date rolls over.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Dict

from core.resilience.atomic_json import atomic_write_json, safe_read_json

logger = logging.getLogger(__name__)

_DEPRIORITIZE_THRESHOLD = 0.80  # 80 % of daily limit


def _today() -> str:
    return datetime.date.today().isoformat()


class BudgetTracker:
    """Track per-provider LLM usage with daily limits and atomic persistence."""

    def __init__(self, data_dir: str) -> None:
        self._path = Path(data_dir) / "llm_budget.json"
        self._data: Dict = self._load()

    # -- persistence helpers --------------------------------------------------

    def _load(self) -> Dict:
        data = safe_read_json(self._path, default={"date": _today(), "providers": {}})
        # Auto-reset on date rollover
        if data.get("date") != _today():
            logger.info("Budget date rollover: %s -> %s", data.get("date"), _today())
            data = {"date": _today(), "providers": {}}
            atomic_write_json(self._path, data)
        return data

    def _save(self) -> None:
        atomic_write_json(self._path, self._data)

    def _ensure_provider(self, provider: str) -> Dict:
        if provider not in self._data["providers"]:
            self._data["providers"][provider] = {
                "requests": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "errors": 0,
            }
        return self._data["providers"][provider]

    # -- public API -----------------------------------------------------------

    def record_usage(
        self,
        provider: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        success: bool = True,
    ) -> None:
        """Record one request for *provider*. Atomically persists after each call."""
        entry = self._ensure_provider(provider)
        entry["requests"] += 1
        entry["tokens_in"] += tokens_in
        entry["tokens_out"] += tokens_out
        if not success:
            entry["errors"] += 1
        self._save()

    def get_usage(self, provider: str) -> Dict:
        """Return usage dict for *provider* (requests, tokens_in, tokens_out, errors)."""
        entry = self._ensure_provider(provider)
        return dict(entry)  # defensive copy

    def can_use(self, provider: str, daily_limit: int) -> bool:
        """Return True if *provider* has not yet hit *daily_limit* requests."""
        return self.get_usage(provider)["requests"] < daily_limit

    def should_deprioritize(self, provider: str, daily_limit: int) -> bool:
        """Return True if *provider* is at or above 80 % of *daily_limit*."""
        return self.get_usage(provider)["requests"] >= daily_limit * _DEPRIORITIZE_THRESHOLD

    def reset_daily(self) -> None:
        """Clear all counters and set today's date."""
        self._data = {"date": _today(), "providers": {}}
        self._save()

    def daily_report(self) -> str:
        """Return a human-readable summary of today's usage."""
        lines = [f"LLM Budget Report for {self._data['date']}"]
        providers = self._data.get("providers", {})
        if not providers:
            lines.append("  No usage recorded.")
        for name, stats in sorted(providers.items()):
            lines.append(
                f"  {name}: {stats['requests']} requests, "
                f"{stats['tokens_in']} tokens in, "
                f"{stats['tokens_out']} tokens out, "
                f"{stats['errors']} errors"
            )
        return "\n".join(lines)
