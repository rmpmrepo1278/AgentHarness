"""Push infrastructure insights to the agent via file-based communication."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger(__name__)


class FeedbackBridge:
    """Push briefings and insights to the agent's file-based inbox."""

    def __init__(self, data_dir: str, bridge_dir: str):
        self.data_dir = Path(data_dir)
        self.bridge_dir = Path(bridge_dir)

    def push_briefing(self, briefing: Dict[str, Any]) -> None:
        """Write a briefing JSON to the agent's briefings directory."""
        briefings_dir = self.bridge_dir / "briefings"
        try:
            briefings_dir.mkdir(parents=True, exist_ok=True)
            date = briefing.get("date", time.strftime("%Y-%m-%d"))
            path = briefings_dir / f"{date}.json"
            path.write_text(json.dumps(briefing, indent=2, default=str))
            log.info(f"Briefing pushed: {path}")
        except OSError as e:
            log.warning(f"Failed to push briefing: {e}")

    def push_insight(self, key: str, value: str, category: str = "operational") -> None:
        """Write an insight to the agent's insights inbox."""
        inbox = self.bridge_dir / "insights_inbox"
        try:
            inbox.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            entry = {"key": key, "value": value, "category": category, "timestamp": ts}
            path = inbox / f"{ts}_{key}.json"
            path.write_text(json.dumps(entry, indent=2))
            log.info(f"Insight pushed: {path}")
        except OSError as e:
            log.warning(f"Failed to push insight: {e}")
