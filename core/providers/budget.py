"""Per-provider LLM budget tracking with atomic persistence.

Tracks daily and per-minute (RPM) request counts and token usage per provider,
persists to JSON, and auto-resets when the date / minute window rolls over.
"""

from __future__ import annotations

import datetime
import logging
import time as _time
from pathlib import Path

from core.resilience.atomic_json import atomic_write_json, safe_read_json

logger = logging.getLogger(__name__)

_DEPRIORITIZE_THRESHOLD = 0.80  # 80 % of daily limit

# Default RPM limits per provider (requests per minute).
# Override in config.yaml with `rpm_limit` per provider.
_DEFAULT_RPM_LIMITS: dict[str, int] = {
    "groq": 30,          # Groq free tier: 30 req/min
    "cerebras": 30,      # Cerebras free tier: 30 req/min
    "sambanova": 10,     # SambaNova free tier: ~10 req/min
    "google-alt": 15,    # Gemini 2.0 flash free tier: 15 req/min
    "google-alt-2": 15,  # Gemini 2.5 flash free tier: 15 req/min (separate quota)
    "openrouter": 20,    # OpenRouter free models: ~20 req/min
    "owl": 20,           # via OpenRouter
    "laguna": 20,        # via OpenRouter
    "laguna-m1": 20,     # via OpenRouter
    "qwen-coder": 20,    # via OpenRouter
    "local": 0,          # unlimited (local)
}


def _today() -> str:
    return datetime.date.today().isoformat()


def _minute_bucket() -> str:
    """Return current minute bucket string, e.g. '2026-05-26T14:32'."""
    return _time.strftime('%Y-%m-%dT%H:%M', _time.gmtime())


class BudgetTracker:
    """Track per-provider LLM usage with daily + RPM limits and atomic persistence."""

    def __init__(self, data_dir: str, rpm_limits: dict[str, int] | None = None) -> None:
        self._path = Path(data_dir) / "llm_budget.json"
        self._rpm_limits = rpm_limits or _DEFAULT_RPM_LIMITS
        self._data: dict = self._load()

    # -- persistence helpers --------------------------------------------------

    def _load(self) -> dict:
        default = {
            "date": _today(),
            "providers": {},
            "rpm": {},       # provider -> { minute_bucket: count }
        }
        data = safe_read_json(self._path, default=default)
        # Auto-reset on date rollover
        if data.get("date") != _today():
            logger.info("Budget date rollover: %s -> %s", data.get("date"), _today())
            data = {"date": _today(), "providers": {}, "rpm": {}}
            atomic_write_json(self._path, data)
        # Prune old RPM buckets (keep only current + previous minute)
        self._prune_rpm(data)
        return data

    def _save(self) -> None:
        atomic_write_json(self._path, self._data)

    def _ensure_provider(self, provider: str) -> dict:
        if provider not in self._data["providers"]:
            self._data["providers"][provider] = {
                "requests": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "errors": 0,
            }
        return self._data["providers"][provider]

    def _prune_rpm(self, data: dict | None = None) -> None:
        """Remove RPM buckets older than 2 minutes to keep the file small."""
        d = data or self._data
        buckets = d.get("rpm", {})
        now_bucket = _minute_bucket()
        # Also keep the previous minute (in case of boundary calls)
        prev_ts = _time.time() - 60
        prev_bucket = _time.strftime('%Y-%m-%dT%H:%M', _time.gmtime(prev_ts))
        for provider in list(buckets.keys()):
            for bucket in list(buckets[provider].keys()):
                if bucket != now_bucket and bucket != prev_bucket:
                    del buckets[provider][bucket]

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
        # Also record in RPM bucket
        self._record_rpm(provider)
        self._save()

    def _record_rpm(self, provider: str) -> None:
        """Increment the RPM counter for the current minute bucket."""
        if "rpm" not in self._data:
            self._data["rpm"] = {}
        if provider not in self._data["rpm"]:
            self._data["rpm"][provider] = {}
        bucket = _minute_bucket()
        if bucket not in self._data["rpm"][provider]:
            self._data["rpm"][provider][bucket] = 0
        self._data["rpm"][provider][bucket] += 1

    def get_rpm(self, provider: str) -> int:
        """Return the request count for the current minute for *provider*."""
        bucket = _minute_bucket()
        return self._data.get("rpm", {}).get(provider, {}).get(bucket, 0)

    def get_rpm_limit(self, provider: str) -> int:
        """Return the RPM limit for *provider* (0 = unlimited)."""
        return self._rpm_limits.get(provider, 0)



    def get_usage(self, provider: str) -> dict:
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
        self._data = {"date": _today(), "providers": {}, "rpm": {}}
        self._save()


    def daily_report(self) -> str:
        """Return a human-readable summary of today's usage."""
        lines = [f"LLM Budget Report for {self._data['date']}"]
        providers = self._data.get("providers", {})
        if not providers:
            lines.append("  No usage recorded.")
        for name, stats in sorted(providers.items()):
            rpm = self.get_rpm(name)
            rpm_limit = self.get_rpm_limit(name)
            rpm_str = f"{rpm}/{rpm_limit}" if rpm_limit > 0 else f"{rpm}/∞"
            lines.append(
                f"  {name}: {stats['requests']} reqs today | "
                f"RPM: {rpm_str} | "
                f"{stats['tokens_in']:,} in / {stats['tokens_out']:,} out tokens | "
                f"{stats['errors']} errors"
            )
        return "\n".join(lines)
