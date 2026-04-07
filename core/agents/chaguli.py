"""Chaguli agent bridge — reference implementation.

File-based communication:
- briefings/         — daily executive briefings
- insights_inbox/    — infrastructure pattern insights
- tool_updates/      — tool addition/removal notifications

Discovery probes Chaguli's container/directory to find capabilities.
If Chaguli has a webhook, it can be used as an opportunistic upgrade.
The file-based path always works as a fallback.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from core.agents.base import (
    AgentBridge,
    Briefing,
    CapabilityReport,
    Insight,
    ToolUpdate,
)

log = logging.getLogger("agents.chaguli")

# Known Chaguli module files that indicate capabilities
CAPABILITY_MARKERS = {
    "memory.py": "memory",
    "briefings.py": "briefings",
    "self_improve.py": "self_improve",
    "tools.py": "tools",
    "heartbeat.py": "heartbeat",
}

DEFAULT_TTL_SECONDS = 7 * 24 * 3600  # 7 days


class ChaguliBridge(AgentBridge):
    """Bridge to Chaguli agent via file-based communication.

    Writes JSON files to well-known directories. Chaguli reads
    them at its own pace. Files are cleaned up after TTL.
    """

    def __init__(
        self,
        briefings_dir: str,
        insights_dir: str,
        tool_updates_dir: str,
        agent_dir: str = "",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ):
        self.briefings_dir = Path(briefings_dir)
        self.insights_dir = Path(insights_dir)
        self.tool_updates_dir = Path(tool_updates_dir)
        self.agent_dir = Path(agent_dir) if agent_dir else None
        self.ttl_seconds = ttl_seconds

        # Ensure directories exist
        for d in [self.briefings_dir, self.insights_dir, self.tool_updates_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def _write_json(self, directory: Path, prefix: str, data: dict) -> Path:
        """Write a JSON file with a unique timestamped name."""
        timestamp = int(time.time() * 1000)
        filename = f"{prefix}_{timestamp}.json"
        path = directory / filename

        # Ensure unique
        while path.exists():
            timestamp += 1
            filename = f"{prefix}_{timestamp}.json"
            path = directory / filename

        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        os.rename(tmp, path)
        return path

    def send_briefing(self, briefing: Briefing) -> bool:
        """Write a briefing JSON to the briefings directory."""
        try:
            data = asdict(briefing)
            data["_source"] = "agentharness"
            data["_written_at"] = time.time()
            path = self._write_json(self.briefings_dir, "briefing", data)
            log.info("Wrote briefing to %s", path)
            return True
        except OSError as e:
            log.error("Failed to write briefing: %s", e)
            return False

    def send_insight(self, insight: Insight) -> bool:
        """Write an insight JSON to the insights inbox."""
        try:
            data = asdict(insight)
            data["_source"] = "agentharness"
            data["_written_at"] = time.time()
            path = self._write_json(self.insights_dir, "insight", data)
            log.info("Wrote insight to %s", path)
            return True
        except OSError as e:
            log.error("Failed to write insight: %s", e)
            return False

    def send_tool_update(self, update: ToolUpdate) -> bool:
        """Write a tool update JSON to the tool_updates directory."""
        try:
            data = asdict(update)
            data["_source"] = "agentharness"
            data["_written_at"] = time.time()
            path = self._write_json(self.tool_updates_dir, "tool_update", data)
            log.info("Wrote tool update to %s", path)
            return True
        except OSError as e:
            log.error("Failed to write tool update: %s", e)
            return False

    def generate_capability_report(self) -> CapabilityReport:
        """Probe Chaguli's directory and report detected capabilities."""
        communication = {
            "file_inbox": str(self.insights_dir),
            "briefings_dir": str(self.briefings_dir),
            "tool_updates_dir": str(self.tool_updates_dir),
            "webhook": None,
            "memory_api": None,
            "telegram": None,  # Detected separately via discovery
        }

        capabilities = []
        warnings = []
        tools_integration = "file_based"

        if self.agent_dir and self.agent_dir.exists():
            # Probe for known module files
            for filename, capability in CAPABILITY_MARKERS.items():
                if (self.agent_dir / filename).exists():
                    capabilities.append(capability)

            # Check for webhook endpoint
            if (self.agent_dir / "webhook.py").exists():
                communication["webhook"] = "detected"

            # Check for memory API
            if "memory" in capabilities:
                communication["memory_api"] = "detected"
                tools_integration = "patched_tools_py"
        else:
            warnings.append(
                f"Agent directory not found: {self.agent_dir}. "
                "File-based communication will still work, but "
                "capability detection is limited."
            )

        return CapabilityReport(
            agent="chaguli",
            communication=communication,
            tools_integration=tools_integration,
            capabilities_detected=capabilities,
            warnings=warnings,
        )

    def cleanup(self, directory: Optional[str] = None) -> int:
        """Remove files older than TTL from a communication directory.

        Returns the number of files removed.
        """
        target = Path(directory) if directory else self.briefings_dir
        removed = 0
        now = time.time()

        for path in target.glob("*.json"):
            if path.name.endswith(".tmp"):
                continue
            try:
                mtime = path.stat().st_mtime
                if now - mtime > self.ttl_seconds:
                    path.unlink()
                    removed += 1
                    log.debug("Cleaned up %s (age: %.0fs)", path, now - mtime)
            except OSError:
                continue

        return removed
