"""Nightly infrastructure data compilation.

Reads metrics, budget, alerts, proposals. Outputs structured JSON briefing.
Pure data aggregation — NO LLM needed.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.resilience.atomic_json import safe_read_json

log = logging.getLogger(__name__)


class Distiller:
    """Compile daily infrastructure data into a structured briefing."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.briefings_dir = self.data_dir / "briefings"
        self.briefings_dir.mkdir(parents=True, exist_ok=True)

    def compile(self) -> dict[str, Any]:
        """Compile today's briefing from all data sources."""
        now = datetime.now(timezone.utc)
        metrics = self._read_metrics()
        budget = self._read_budget()
        proposals = self._read_proposals()

        # Health stats from metrics
        checks = [m for m in metrics if m.get("type") == "check"]
        checks_passed = sum(1 for c in checks if c.get("status") == "ok")
        checks_failed = sum(1 for c in checks if c.get("status") != "ok")
        failed_details = [
            {"name": c.get("check", "?"), "status": c.get("status", "?")}
            for c in checks if c.get("status") != "ok"
        ]

        # Tool call stats
        tool_calls = [m for m in metrics if m.get("type") == "tool_call"]
        tools_run = len(tool_calls)
        tools_success = sum(1 for t in tool_calls if t.get("success"))

        # Action items
        action_items = []
        if checks_failed > 0:
            action_items.append({
                "priority": "high",
                "item": f"{checks_failed} health check(s) failing",
            })

        briefing = {
            "date": now.strftime("%Y-%m-%d"),
            "compiled_at": now.isoformat(),
            "health": {
                "checks_run": len(checks),
                "checks_passed": checks_passed,
                "checks_failed": checks_failed,
                "failed": failed_details,
            },
            "tools": {
                "total_runs": tools_run,
                "success": tools_success,
                "failures": tools_run - tools_success,
            },
            "llm_usage": budget.get("providers", {}),
            "proposals": proposals,
            "action_items": action_items,
        }
        return briefing

    def compile_and_save(self) -> str:
        """Compile and save briefing to file. Returns file path."""
        briefing = self.compile()
        date_str = briefing["date"]
        path = self.briefings_dir / f"{date_str}.json"
        path.write_text(json.dumps(briefing, indent=2, default=str))
        log.info(f"Briefing saved: {path}")
        return str(path)

    def format_summary(self, briefing: dict) -> str:
        """Format briefing as a short text summary (<4096 chars)."""
        h = briefing.get("health", {})
        t = briefing.get("tools", {})
        lines = [
            f"Morning Briefing — {briefing.get('date', '?')}",
            "",
            f"Health: {h.get('checks_passed', 0)}/{h.get('checks_run', 0)} checks passed",
        ]
        if h.get("failed"):
            for f in h["failed"][:5]:
                lines.append(f"  ! {f.get('name', '?')}: {f.get('status', '?')}")
        lines.append(f"Tools: {t.get('total_runs', 0)} runs, {t.get('success', 0)} ok")

        llm = briefing.get("llm_usage", {})
        if llm:
            lines.append("LLM budget:")
            for provider, usage in llm.items():
                reqs = usage.get("requests", 0)
                lines.append(f"  {provider}: {reqs} requests")

        actions = briefing.get("action_items", [])
        if actions:
            lines.append("")
            lines.append("Action items:")
            for a in actions[:5]:
                lines.append(f"  [{a.get('priority', '?')}] {a.get('item', '?')}")

        text = "\n".join(lines)
        return text[:4090]

    def _read_metrics(self) -> list[dict]:
        """Read today's metrics from metrics.jsonl."""
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

    def _read_budget(self) -> dict:
        """Read today's budget data."""
        return safe_read_json(self.data_dir / "llm_budget.json", default={})

    def _read_proposals(self) -> dict:
        """Count proposal activity."""
        proposals_dir = self.data_dir / "proposals"
        if not proposals_dir.is_dir():
            return {"pending": 0, "approved": 0, "rejected": 0}
        counts = {"pending": 0, "approved": 0, "rejected": 0}
        for f in proposals_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                status = data.get("status", "pending")
                if status in counts:
                    counts[status] += 1
            except (json.JSONDecodeError, OSError):
                continue
        return counts
