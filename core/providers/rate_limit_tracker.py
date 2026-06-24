"""
Rate Limit Tracker — Per-provider, per-model rate limit state management.

Tracks ALL failure types (429, 500, timeout, connection refused, empty response)
per provider-MODEL pair (not just per-provider, since OpenRouter rate-limits
owl-alpha:free differently than claude-sonnet-4:paid).

Features:
- Per-model tracking (not just per-provider)
- All failure types tracked (not just 429)
- Atomic file writes (write-to-temp + rename)
- Proactive health decay (failure score decays over time)
- All-down deadlock prevention (forcibly retry best provider after N minutes)
- Configurable via env vars:
    RL_COOLDOWN_THRESHOLD (default: 2)
    RL_BASE_COOLDOWN_SECS (default: 120)
    RL_MAX_COOLDOWN_SECS (default: 1800)
    RL_ALL_DOWN_RETRY_SECS (default: 300)
    RL_HEALTH_DECAY_SECS (default: 600)
    RL_FAILURE_WEIGHTS (default: 429:3,500:2,timeout:2,conn_refused:1,empty:1)
- Observability: counters for skips, cooldowns, deadlocks, health scores
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────

COOLDOWN_THRESHOLD = int(os.environ.get("RL_COOLDOWN_THRESHOLD", "3"))
BASE_COOLDOWN_SECS = int(os.environ.get("RL_BASE_COOLDOWN_SECS", "60"))
MAX_COOLDOWN_SECS = int(os.environ.get("RL_MAX_COOLDOWN_SECS", "900"))
ALL_DOWN_RETRY_SECS = int(os.environ.get("RL_ALL_DOWN_RETRY_SECS", "60"))
HEALTH_DECAY_SECS = int(os.environ.get("RL_HEALTH_DECAY_SECS", "300"))

# Failure type weights (higher = more severe)
FAILURE_WEIGHTS: dict[str, int] = {}
try:
    FAILURE_WEIGHTS = json.loads(os.environ.get("RL_FAILURE_WEIGHTS", "{}"))
except Exception:
    pass
if not FAILURE_WEIGHTS:
    FAILURE_WEIGHTS = {
        "429": 3,
        "500": 2,
        "timeout": 2,
        "connection_refused": 1,
        "empty_response": 1,
        "other_error": 1,
    }

STATE_FILE = Path("/home/rohit/agentharness/data/rate_limit_state.json")
LOCK_FILE = Path("/home/rohit/agentharness/data/rate_limit_state.lock")

# ── Transient Error Detection ──────────────────────────────────────────────
# If multiple providers fail with connection_refused within a short window,
# it's likely a local network issue, not per-provider rate limiting.
_TRANSIENT_WINDOW_SECS = 5  # providers failing within this window = transient
_TRANSIENT_THRESHOLD = 3    # this many failures = transient network issue


class _TransientErrorDetector:
    """Detect transient network errors vs per-provider rate limits."""

    def __init__(self):
        self._recent_failures: list[tuple[str, float, str]] = []  # (provider, time, type)

    def record(self, provider: str, error_type: str):
        now = time.monotonic()
        self._recent_failures.append((provider, now, error_type))
        # Prune old entries
        cutoff = now - _TRANSIENT_WINDOW_SECS
        self._recent_failures = [f for f in self._recent_failures if f[1] > cutoff]

    def is_transient(self, error_type: str) -> bool:
        """Check if current failure is part of a transient network issue."""
        if error_type != "connection_refused":
            return False
        # Count unique providers with connection_refused in window
        recent_providers = set()
        cutoff = time.monotonic() - _TRANSIENT_WINDOW_SECS
        for p, t, e in self._recent_failures:
            if t > cutoff and e == "connection_refused":
                recent_providers.add(p)
        return len(recent_providers) >= _TRANSIENT_THRESHOLD


# Global transient error detector (shared across all tracker instances)
_transient_detector = _TransientErrorDetector()

# ── Atomic File Write (with lock for concurrent workers) ───────────────────

def _atomic_write(path: Path, data: dict):
    """
    Write JSON atomically via temp file + rename.
    Uses file locking to prevent corruption from concurrent uvicorn workers.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = path.parent / (path.name + ".lock")
        with open(lock_file, "w") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                fd, tmp_path = tempfile.mkstemp(
                    dir=str(path.parent), suffix=".tmp", prefix=path.stem + "_"
                )
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.rename(tmp_path, str(path))
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
    except Exception:
        # Best effort — if we can't persist, in-memory state still works
        pass


# ── Failure Scoring ─────────────────────────────────────────────────────────

def _failure_weight(status_code: int | None, error_type: str | None) -> int:
    """Get weight for a failure type."""
    if error_type == "timeout":
        return FAILURE_WEIGHTS.get("timeout", 2)
    if error_type == "connection_refused":
        return FAILURE_WEIGHTS.get("connection_refused", 1)
    if error_type == "empty_response":
        return FAILURE_WEIGHTS.get("empty_response", 1)
    if status_code:
        return FAILURE_WEIGHTS.get(str(status_code), FAILURE_WEIGHTS.get("other_error", 1))
    return FAILURE_WEIGHTS.get("other_error", 1)


# ── State ───────────────────────────────────────────────────────────────────

class RateLimitTracker:
    """
    Track per-provider-model rate limit state with failure scoring.
    """

    def __init__(self, data_dir: str = ""):
        self._data: dict[str, dict] = {}
        self._state_file = Path(data_dir) / "rate_limit_state.json" if data_dir else STATE_FILE
        self._load()

    def _load(self):
        try:
            if self._state_file.exists():
                self._data = json.loads(self._state_file.read_text())
        except Exception:
            self._data = {}
        # Sanity check: time.monotonic() resets on reboot, so cooldown_until
        # values from a previous boot are garbage. Clamp any stale entries.
        now = time.monotonic()
        for key, entry in list(self._data.items()):
            if entry.get("cooldown_until", 0.0) > now + MAX_COOLDOWN_SECS:
                entry["cooldown_until"] = 0.0
                entry["cooldown_count"] = 0

    def _save(self):
        _atomic_write(self._state_file, self._data)

    def _key(self, provider: str, model: str) -> str:
        """Per-model key. Falls back to provider-only if model is empty."""
        return f"{provider}:{model}" if model else provider

    def record_response(self, provider: str, status_code: int,
                        model: str = "", error_type: str | None = None):
        """
        Record an API response.

        Args:
            provider: provider name (e.g. "owl", "groq")
            status_code: HTTP status code (200, 429, 500, etc.)
            model: model ID (e.g. "openrouter/owl-alpha") for per-model tracking
            error_type: "timeout" | "connection_refused" | "empty_response" | None
        """
        key = self._key(provider, model)
        now = time.monotonic()
        entry = self._data.get(key, {
            "consecutive_failures": 0,
            "cooldown_until": 0.0,
            "total_failures": 0,
            "total_success": 0,
            "total_429s": 0,
            "total_timeouts": 0,
            "total_500s": 0,
            "last_response": 0.0,
            "last_failure_type": None,
            "failure_score": 0.0,
            "cooldown_count": 0,
        })

        entry["last_response"] = now

        if status_code == 200 and not error_type:
            # Success — reset
            if entry["consecutive_failures"] > 0:
                log.debug("Rate limit: %s recovered (was %d consecutive failures)",
                         key, entry["consecutive_failures"])
            entry["consecutive_failures"] = 0
            entry["cooldown_count"] = 0
            entry["failure_score"] = max(0, entry["failure_score"] - 1)
            entry["total_success"] += 1
        else:
            # TRANSIENT ERROR CHECK: if multiple providers fail with connection_refused
            # within a short window, it's a local network issue — don't penalize
            is_transient = False
            if error_type == "connection_refused":
                _transient_detector.record(provider, error_type)
                is_transient = _transient_detector.is_transient(error_type)
                if is_transient:
                    # Still apply gentle penalty (30% weight) — transient errors
                    # degrade health slightly but don't trigger cooldown.
                    weight = _failure_weight(status_code, error_type)
                    entry["failure_score"] = entry["failure_score"] + (weight * 0.3)
                    entry["total_failures"] += 1
                    entry["last_failure_type"] = error_type + "(transient)"
                    log.info("Rate limit: transient network error — "
                            "gentle penalty on %s (score +%.1f)", key, weight * 0.3)
                    self._data[key] = entry
                    self._save()
                    return  # skip cooldown for transient errors

            # Failure (non-transient)
            weight = _failure_weight(status_code, error_type)
            entry["consecutive_failures"] += 1
            entry["total_failures"] += 1
            entry["failure_score"] = entry["failure_score"] + weight
            entry["last_failure_type"] = error_type or str(status_code)

            if status_code == 429:
                entry["total_429s"] += 1
            elif error_type == "timeout":
                entry["total_timeouts"] += 1
            elif status_code and status_code >= 500:
                entry["total_500s"] += 1

            # Check cooldown threshold
            if entry["consecutive_failures"] >= COOLDOWN_THRESHOLD:
                cooldown_count = entry.get("cooldown_count", 0)
                duration = min(BASE_COOLDOWN_SECS * (2 ** cooldown_count), MAX_COOLDOWN_SECS)
                entry["cooldown_until"] = now + duration
                entry["cooldown_count"] = cooldown_count + 1
                log.info("Rate limit: %s cooldown %ds (%s, score=%.0f)",
                        key, duration, entry["last_failure_type"], entry["failure_score"])

        self._data[key] = entry
        self._save()

    def record_headers(self, provider: str, model: str, headers: dict) -> None:
        """
        Parse x-ratelimit-* response headers for proactive throttling.
        If remaining requests drop below 10%, apply a short 30s anticipatory cooldown.
        """
        # Check for various header naming conventions
        remaining = (
            headers.get("x-ratelimit-remaining-requests")
            or headers.get("x-ratelimit-remaining")
            or headers.get("ratelimit-remaining")
        )
        limit = (
            headers.get("x-ratelimit-limit-requests")
            or headers.get("x-ratelimit-limit")
            or headers.get("ratelimit-limit")
        )
        if remaining is None or limit is None:
            return
        try:
            pct = int(remaining) / int(limit)
        except (ValueError, ZeroDivisionError):
            return
        if pct < 0.1:
            key = self._key(provider, model)
            entry = self._data.setdefault(key, {
                "consecutive_failures": 0,
                "cooldown_until": 0.0,
                "total_failures": 0,
                "total_success": 0,
                "total_429s": 0,
                "total_timeouts": 0,
                "total_500s": 0,
                "last_response": 0.0,
                "last_failure_type": None,
                "failure_score": 0.0,
                "cooldown_count": 0,
            })
            entry["cooldown_until"] = time.monotonic() + 30
            entry["approaching_limit"] = True
            log.info("Rate limit: %s approaching limit (%s/%s left), 30s anticipatory cooldown",
                     key, remaining, limit)
            self._save()

    def is_in_cooldown(self, provider: str, model: str = "") -> bool:
        """Check if a provider:model pair is in cooldown."""
        key = self._key(provider, model)
        entry = self._data.get(key, {})
        cooldown_until = entry.get("cooldown_until", 0.0)
        if cooldown_until and time.monotonic() < cooldown_until:
            return True
        return False

    def get_cooldown_remaining(self, provider: str, model: str = "") -> int:
        """Get remaining cooldown seconds."""
        key = self._key(provider, model)
        entry = self._data.get(key, {})
        remaining = int(entry.get("cooldown_until", 0.0) - time.monotonic())
        return max(0, remaining)

    def get_health_score(self, provider: str, model: str = "") -> float:
        """
        Get a health score between 0 (dead) and 1 (healthy).
        Decays over time so stale failures don't penalize forever.
        """
        key = self._key(provider, model)
        entry = self._data.get(key, {})
        if not entry:
            return 1.0  # unknown = assumed healthy

        failure_score = entry.get("failure_score", 0.0)
        last_response = entry.get("last_response", 0.0)

        # Decay failure score over time
        if last_response > 0:
            elapsed = time.monotonic() - last_response
            decay = elapsed / HEALTH_DECAY_SECS  # 1.0 per decay period
            failure_score = max(0, failure_score - decay)

        # Convert to 0-1 health score
        # failure_score of 0 → health 1.0, score of 10+ → health 0.0
        return max(0.0, 1.0 - (failure_score / 10.0))

    def filter_available(self, providers: list[str], models: dict[str, str] | None = None) -> list[str]:
        """
        Filter out providers in cooldown. Implements all-down deadlock prevention:
        if ALL providers are in cooldown, forcibly return the healthiest one.
        """
        models = models or {}
        available = []
        all_cooldown_since = 0.0

        for p in providers:
            model = models.get(p, "")
            if not self.is_in_cooldown(p, model):
                available.append(p)
            else:
                # Track how long ALL providers have been down
                remaining = self.get_cooldown_remaining(p, model)
                all_cooldown_since = max(all_cooldown_since, remaining)

        if not available and providers:
            # ALL DOWN — staggered retry: pick provider with shortest remaining cooldown
            # Tiered: <30s cooldown → retry immediately; <60s → retry with 5s backoff hint;
            # <300s → retry healthiest; ≥300s → fail (real outage)
            shortest_remaining = float("inf")
            best_provider = None
            for p in providers:
                remaining = self.get_cooldown_remaining(p, models.get(p, ""))
                if remaining < shortest_remaining:
                    shortest_remaining = remaining
                    best_provider = p

            if shortest_remaining < 30:
                # Provider almost recovered — retry immediately
                log.info("Rate limit: ALL cooldown, %s recovers in %ds — staggered retry",
                         best_provider, int(shortest_remaining))
                return [best_provider]
            elif shortest_remaining < 60:
                # Mid-cooldown — retry healthiest with backoff hint
                best_healthy = max(
                    providers,
                    key=lambda p: self.get_health_score(p, models.get(p, ""))
                )
                log.info("Rate limit: ALL cooldown (shortest %ds) — retry healthiest: %s",
                         int(shortest_remaining), best_healthy)
                return [best_healthy]
            elif all_cooldown_since >= ALL_DOWN_RETRY_SECS:
                # Extended deadlock — force retry healthiest
                best_healthy = max(
                    providers,
                    key=lambda p: self.get_health_score(p, models.get(p, ""))
                )
                best_score = self.get_health_score(best_healthy, models.get(best_healthy, ""))
                log.warning("Rate limit: ALL providers in cooldown for %.0fs — "
                           "forcing retry of healthiest: %s (score=%.2f)",
                           all_cooldown_since, best_healthy, best_score)
                return [best_healthy]
            else:
                log.warning("Rate limit: ALL %d providers in cooldown "
                           "(shortest %ds, oldest %.0fs, deadlock threshold %ds)",
                           len(providers), int(shortest_remaining),
                           all_cooldown_since, ALL_DOWN_RETRY_SECS)

        return available

    def get_stats(self, provider: str, model: str = "") -> dict:
        """Get stats for a provider:model pair."""
        key = self._key(provider, model)
        entry = self._data.get(key, {})
        return {
            "consecutive_failures": entry.get("consecutive_failures", 0),
            "total_failures": entry.get("total_failures", 0),
            "total_success": entry.get("total_success", 0),
            "total_429s": entry.get("total_429s", 0),
            "total_timeouts": entry.get("total_timeouts", 0),
            "total_500s": entry.get("total_500s", 0),
            "in_cooldown": self.is_in_cooldown(provider, model),
            "cooldown_remaining": self.get_cooldown_remaining(provider, model),
            "health_score": self.get_health_score(provider, model),
            "failure_score": entry.get("failure_score", 0),
            "last_failure_type": entry.get("last_failure_type"),
        }

    def get_all_stats(self) -> dict:
        """Get stats for all tracked provider:model pairs."""
        return {key: self.get_stats(*key.split(":", 1) if ":" in key else (key, ""))
                for key in self._data}

    def get_observability(self) -> dict:
        """Return aggregate counters for monitoring."""
        total_cooldowns = sum(1 for e in self._data.values()
                            if e.get("cooldown_until", 0) > time.monotonic())
        return {
            "tracked_pairs": len(self._data),
            "in_cooldown": total_cooldowns,
            "all_time_429s": sum(e.get("total_429s", 0) for e in self._data.values()),
            "all_time_timeouts": sum(e.get("total_timeouts", 0) for e in self._data.values()),
            "all_time_500s": sum(e.get("total_500s", 0) for e in self._data.values()),
            "all_time_success": sum(e.get("total_success", 0) for e in self._data.values()),
        }

    def get_staggered_retry_hint(self, providers: list[str], models: dict[str, str] | None = None) -> dict:
        """
        Return retry guidance when all providers are cooldown.
        Returns {"retry_after": seconds, "suggest_provider": name} or {"retry_after": -1} if no retry.
        """
        models = models or {}
        if not providers:
            return {"retry_after": -1}

        # Find provider with shortest cooldown
        best = None
        best_remaining = float("inf")
        for p in providers:
            remaining = self.get_cooldown_remaining(p, models.get(p, ""))
            if remaining < best_remaining:
                best_remaining = remaining
                best = p

        if best_remaining < 30:
            return {"retry_after": 0, "suggest_provider": best}
        elif best_remaining < 120:
            return {"retry_after": min(5, best_remaining), "suggest_provider": best}
        else:
            return {"retry_after": -1}

    def reset(self, provider: str | None = None, model: str | None = None):
        """Reset state for a specific pair or all."""
        if provider:
            key = self._key(provider, model or "")
            self._data.pop(key, None)
        else:
            self._data = {}
        self._save()
