"""Track optimization findings and source reliability."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.resilience.atomic_json import safe_read_json, atomic_write_json, atomic_append_json

log = logging.getLogger(__name__)


class OptimizationTracker:
    """Track what's been tried and source reliability."""

    def __init__(self, data_dir: str):
        self.history_file = Path(data_dir) / "optimization_history.json"
        self.reliability_file = Path(data_dir) / "source_reliability.json"

    def record_finding(self, finding: dict[str, Any], outcome: str) -> None:
        """Record a finding and its outcome."""
        entry = {"finding": finding, "outcome": outcome}
        atomic_append_json(self.history_file, entry)

    def get_history(self) -> list[dict]:
        return safe_read_json(self.history_file, default=[])

    def is_seen(self, source: str, repo: str, tag: str) -> bool:
        """Check if a finding has already been recorded."""
        history = self.get_history()
        for entry in history:
            f = entry.get("finding", {})
            if f.get("source") == source and f.get("repo") == repo and f.get("tag") == tag:
                return True
        return False

    def record_source_result(self, source_key: str, useful: bool) -> None:
        """Record whether a source produced useful findings."""
        data = safe_read_json(self.reliability_file, default={})
        entry = data.setdefault(source_key, {"useful": 0, "total": 0})
        entry["total"] += 1
        if useful:
            entry["useful"] += 1
        atomic_write_json(self.reliability_file, data)

    def get_source_reliability(self, source_key: str) -> float:
        """Get reliability score for a source (0.0 to 1.0)."""
        data = safe_read_json(self.reliability_file, default={})
        entry = data.get(source_key, {"useful": 0, "total": 0})
        total = entry.get("total", 0)
        if total == 0:
            return 0.5  # Unknown = neutral
        return entry.get("useful", 0) / total
