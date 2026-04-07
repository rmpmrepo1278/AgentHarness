"""Pattern detection — detect repetitive commands, alert fatigue, failure patterns.

Reads metrics.jsonl. Creates proposal dicts for patterns worth acting on.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REPETITIVE_THRESHOLD = 5   # Same command 5+ times
ALERT_FATIGUE_THRESHOLD = 10  # Same alert 10+ times
FAILURE_THRESHOLD = 3  # Same tool fails 3+ times


class Synthesizer:
    """Watch operational patterns and propose new tools or adjustments."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)

    def detect_patterns(self) -> list:
        """Scan metrics for actionable patterns."""
        metrics = self._read_metrics()
        patterns: list = []
        patterns.extend(self._detect_repetitive(metrics))
        patterns.extend(self._detect_alert_fatigue(metrics))
        patterns.extend(self._detect_failures(metrics))
        return patterns

    def propose(self) -> list:
        """Detect patterns and generate proposal dicts."""
        patterns = self.detect_patterns()
        proposals = []
        for p in patterns:
            proposals.append({
                "tool_name": p.get("suggested_tool", "unknown"),
                "reason": p["detail"],
                "proposal_type": p.get("proposal_type", "tool_synthesis"),
                "pattern": p,
            })
        return proposals

    def _detect_repetitive(self, metrics: list) -> list:
        """Find commands that repeat 5+ times."""
        commands = [m.get("request", "") for m in metrics if m.get("type") == "unhandled_request"]
        # Normalize: group by first 2 words
        normalized = []
        for cmd in commands:
            parts = cmd.split()[:2]
            normalized.append(" ".join(parts))
        counts = Counter(normalized)
        patterns = []
        for cmd, count in counts.items():
            if count >= REPETITIVE_THRESHOLD:
                patterns.append({
                    "type": "repetitive_command",
                    "detail": f"'{cmd}' appeared {count} times — consider creating a permanent tool",
                    "count": count,
                    "suggested_tool": cmd.replace(" ", "_"),
                    "proposal_type": "tool_synthesis",
                })
        return patterns

    def _detect_alert_fatigue(self, metrics: list) -> list:
        """Find checks that fire 10+ times without action."""
        check_warns = [m.get("check", "") for m in metrics
                       if m.get("type") == "check" and m.get("status") in ("warn", "critical")]
        counts = Counter(check_warns)
        patterns = []
        for check, count in counts.items():
            if count >= ALERT_FATIGUE_THRESHOLD:
                patterns.append({
                    "type": "alert_fatigue",
                    "detail": f"'{check}' alerted {count} times — consider adjusting threshold or adding auto-remediation",
                    "count": count,
                    "suggested_tool": f"adjust_{check}_threshold",
                    "proposal_type": "config_change",
                })
        return patterns

    def _detect_failures(self, metrics: list) -> list:
        """Find tools that fail repeatedly."""
        failures = [m.get("tool", "") for m in metrics
                    if m.get("type") == "tool_call" and not m.get("success")]
        counts = Counter(failures)
        patterns = []
        for tool, count in counts.items():
            if count >= FAILURE_THRESHOLD:
                patterns.append({
                    "type": "repeated_failure",
                    "detail": f"'{tool}' failed {count} times — investigate root cause",
                    "count": count,
                    "proposal_type": "tool_execution",
                })
        return patterns

    def _read_metrics(self) -> list:
        metrics_file = self.data_dir / "metrics.jsonl"
        if not metrics_file.exists():
            return []
        entries = []
        for line in metrics_file.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries
