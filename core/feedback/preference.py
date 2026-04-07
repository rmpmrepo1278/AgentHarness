"""Advisory preference model — tracks approval/rejection patterns.

NEVER auto-suppresses proposals. Surfaces observations after min_data_points.
User can reset all learned preferences via CLI.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from core.resilience.atomic_json import safe_read_json, atomic_write_json

log = logging.getLogger(__name__)


class PreferenceModel:
    """Track and surface approval/rejection patterns."""

    def __init__(self, data_dir: str, min_data_points: int = 5):
        self.data_file = Path(data_dir) / "preference_model.json"
        self.min_data_points = min_data_points

    def record(self, pattern_key: str, outcome: str) -> None:
        """Record an approval or rejection."""
        data = safe_read_json(self.data_file, default={})
        entry = data.setdefault(pattern_key, {"approved": 0, "rejected": 0, "total": 0})
        if outcome in ("approved", "rejected"):
            entry[outcome] += 1
        entry["total"] = entry["approved"] + entry["rejected"]
        atomic_write_json(self.data_file, data)

    def get_history(self, pattern_key: str) -> dict:
        """Get approval/rejection history for a pattern key."""
        data = safe_read_json(self.data_file, default={})
        return data.get(pattern_key, {"approved": 0, "rejected": 0, "total": 0})

    def get_suggestion(self, pattern_key: str) -> Optional[dict]:
        """Get a suggestion based on pattern history. Returns None if insufficient data."""
        history = self.get_history(pattern_key)
        total = history.get("total", 0)
        if total < self.min_data_points:
            return None
        rejected = history.get("rejected", 0)
        approved = history.get("approved", 0)
        if rejected >= total * 0.8:
            return {"action": "suppress", "reason": f"Rejected {rejected}/{total} times", "pattern": pattern_key}
        if approved >= total * 0.8:
            return {"action": "promote", "reason": f"Approved {approved}/{total} times", "pattern": pattern_key}
        return None

    def reset(self) -> None:
        """Clear all learned preferences."""
        atomic_write_json(self.data_file, {})
        log.info("Preference model reset")
