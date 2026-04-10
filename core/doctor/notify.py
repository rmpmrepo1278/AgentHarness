"""NotificationRouter — route doctor alerts by severity tier.

Three tiers:
- silent:   append to doctor_log.jsonl only
- fyi:      append to log + write JSON to Chaguli inbox dir
- critical: append to log + write to inbox + call alert.sh
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

VALID_LEVELS = ("silent", "fyi", "critical")


class NotificationRouter:
    """Route doctor notifications to the appropriate channels."""

    def __init__(
        self,
        data_dir: str,
        chaguli_inbox_dir: str,
        alert_script: str = "",
    ):
        self.data_dir = Path(data_dir)
        self.chaguli_inbox_dir = Path(chaguli_inbox_dir)
        self.alert_script = alert_script
        self.log_file = self.data_dir / "doctor_log.jsonl"

    # -- public API ----------------------------------------------------------

    def notify(
        self,
        level: str,
        title: str,
        body: str,
        runbook: str | None = None,
    ) -> None:
        """Route a notification to the correct tier handler."""
        level = level.lower()
        if level not in VALID_LEVELS:
            log.warning("Unknown notification level %r, falling back to fyi", level)
            level = "fyi"

        if level == "silent":
            self.log_silent(title, body, runbook)
        elif level == "fyi":
            self.send_fyi(title, body, runbook)
        elif level == "critical":
            self.send_critical(title, body, runbook)

    def log_silent(
        self, title: str, body: str, runbook: str | None = None
    ) -> None:
        """Silent tier — log only."""
        self._append_log("silent", title, body, runbook)

    def send_fyi(
        self, title: str, body: str, runbook: str | None = None
    ) -> None:
        """FYI tier — log + inbox."""
        self._append_log("fyi", title, body, runbook)
        self._write_inbox(title, body, runbook)

    def send_critical(
        self, title: str, body: str, runbook: str | None = None
    ) -> None:
        """Critical tier — log + inbox + alert.sh."""
        self._append_log("critical", title, body, runbook)
        self._write_inbox(title, body, runbook)
        self._send_alert(title, body)

    # -- internal helpers ----------------------------------------------------

    def _append_log(
        self,
        level: str,
        title: str,
        body: str,
        runbook: str | None,
    ) -> None:
        """Append one JSONL line to doctor_log.jsonl."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "title": title,
            "body": body,
            "runbook": runbook,
        }
        with self.log_file.open("a") as fh:
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def _write_inbox(self, title: str, body: str, runbook: str | None) -> None:
        """Write a JSON file to the Chaguli inbox directory."""
        self.chaguli_inbox_dir.mkdir(parents=True, exist_ok=True)
        ts_ms = int(time.time() * 1000)
        filename = f"doctor_{ts_ms}.json"
        payload = {
            "title": title,
            "body": body,
            "runbook": runbook,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "_source": "agentharness_doctor",
        }
        target = self.chaguli_inbox_dir / filename
        target.write_text(json.dumps(payload, indent=2))
        log.debug("Wrote inbox file %s", target)

    def _send_alert(self, title: str, body: str) -> None:
        """Call alert.sh for critical notifications."""
        if not self.alert_script:
            log.warning("No alert_script configured; skipping alert dispatch")
            return
        script = Path(self.alert_script)
        if not script.exists():
            log.error("Alert script not found: %s", script)
            return
        try:
            subprocess.run(
                [str(script), "CRITICAL", f"{title}: {body}", "agentharness_doctor"],
                timeout=30,
                check=False,
                capture_output=True,
            )
        except Exception:
            log.exception("Failed to run alert script %s", script)
