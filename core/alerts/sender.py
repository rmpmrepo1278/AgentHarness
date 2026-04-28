"""File-based alert sender — writes alerts for the agent to consume.

AgentHarness does NOT talk to Telegram directly. It writes alert JSON
files to an alerts_inbox/ directory. The agent (Chaguli/OpenClaw/etc.)
reads these and decides when/how to deliver them (Telegram, email, etc.).

If no agent is installed, alerts are only visible via:
  - `agentharness alerts` CLI command
  - Dashboard /api/alerts endpoint
  - Logs
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.resilience.atomic_json import atomic_append_json, safe_read_json

log = logging.getLogger(__name__)


@dataclass
class Alert:
    """A single alert."""
    severity: str        # "info", "warn", "critical"
    message: str
    source: str = ""     # Which script/module generated this
    timestamp: str = ""
    delivered: bool = False
    requires_approval: bool = False
    approval_id: Optional[str] = None
    actions: Optional[list[dict]] = None

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class AlertSender:
    """Write alerts to a file-based inbox for the agent to consume.

    Replaces the old alert.sh which talked directly to Telegram.
    """

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.alerts_file = self.data_dir / "alerts_inbox.jsonl"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def send(self, severity: str, message: str, source: str = "", requires_approval: bool = False, approval_id: Optional[str] = None, actions: Optional[list[dict]] = None) -> None:
        """Queue an alert for the agent to deliver."""
        print(f"sender.py send() called with:")
        print(f"  severity: {severity}")
        print(f"  message: {message}")
        print(f"  source: {source}")
        print(f"  requires_approval: {requires_approval}")
        print(f"  approval_id: {approval_id}")
        print(f"  actions: {actions}")
        alert = Alert(
            severity=severity,
            message=message,
            source=source,
            requires_approval=requires_approval,
            approval_id=approval_id,
            actions=actions,
        )
        print(f"Created Alert object: {alert}")
        atomic_append_json(self.alerts_file, asdict(alert))
        print(f"Appended alert to {self.alerts_file}")
        log.info(f"Alert [{severity}] from {source}: {message[:100]}")

    def info(self, message: str, source: str = "") -> None:
        self.send("info", message, source)

    def warn(self, message: str, source: str = "") -> None:
        self.send("warn", message, source)

    def critical(self, message: str, source: str = "") -> None:
        self.send("critical", message, source)

    def get_pending(self) -> list[dict]:
        """Get all undelivered alerts."""
        all_alerts = safe_read_json(self.alerts_file, default=[])
        if not isinstance(all_alerts, list):
            return []
        return [a for a in all_alerts if not a.get("delivered", False)]

    def get_all(self, limit: int = 50) -> list[dict]:
        """Get recent alerts (delivered and pending)."""
        all_alerts = safe_read_json(self.alerts_file, default=[])
        if not isinstance(all_alerts, list):
            return []
        return all_alerts[-limit:]

    def mark_delivered(self, count: int = 0) -> int:
        """Mark pending alerts as delivered (called by the agent after sending).

        If count=0, marks all pending as delivered.
        Returns number of alerts marked.
        """
        all_alerts = safe_read_json(self.alerts_file, default=[])
        if not isinstance(all_alerts, list):
            return 0

        marked = 0
        for alert in all_alerts:
            if not alert.get("delivered", False):
                alert["delivered"] = True
                marked += 1
                if count > 0 and marked >= count:
                    break

        from core.resilience.atomic_json import atomic_write_json
        atomic_write_json(self.alerts_file, all_alerts)
        return marked

    def clear_old(self, max_age_days: int = 30) -> int:
        """Remove delivered alerts older than max_age_days."""
        all_alerts = safe_read_json(self.alerts_file, default=[])
        if not isinstance(all_alerts, list):
            return 0

        cutoff = time.time() - max_age_days * 86400
        kept = []
        removed = 0
        for alert in all_alerts:
            ts = alert.get("timestamp", "")
            try:
                alert_time = datetime.fromisoformat(ts).timestamp()
            except (ValueError, TypeError):
                alert_time = time.time()

            if alert.get("delivered") and alert_time < cutoff:
                removed += 1
            else:
                kept.append(alert)

        if removed > 0:
            from core.resilience.atomic_json import atomic_write_json
            atomic_write_json(self.alerts_file, kept)

        return removed


def get_alert_sender(data_dir: str = "") -> AlertSender:
    """Get an AlertSender for the current data directory.

    Convenience function for shell scripts calling via Python one-liner.
    """
    if not data_dir:
        data_dir = os.environ.get("AH_DATA_DIR", ".")
    return AlertSender(data_dir=data_dir)
