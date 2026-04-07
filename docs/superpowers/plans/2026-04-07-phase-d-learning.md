# Phase D: Learning + Optimization — Feedback Loop, Scout, Dashboard

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the learning loop — compile nightly infrastructure briefings, detect operational patterns and propose new tools, scout for optimization opportunities, and provide an optional web dashboard for observability.

**Architecture:** The distiller aggregates daily metrics into structured JSON briefings. The synthesizer watches patterns and creates proposals via Phase C's approval gateway. The optimization scout searches external sources for new techniques. The feedback bridge pushes insights to the agent via Phase C's agent bridge. An optional FastAPI dashboard visualizes everything.

**Tech Stack:** Python 3.9+, FastAPI (optional), Jinja2 (optional), existing core/ modules

**Depends on:** Phase A (discovery, state, atomic_json, metrics), Phase B (budget, scheduler), Phase C (approval gateway, agent bridge)

---

## File Structure

### New files to create:
```
core/feedback/__init__.py
core/feedback/distiller.py         # Nightly infrastructure data compilation
core/feedback/synthesizer.py       # Pattern detection → propose new tools
core/feedback/preference.py        # Advisory preference model
core/feedback/bridge.py            # Push insights to agent via Phase C bridge
core/optimize/__init__.py
core/optimize/scout.py             # Search for new techniques/models
core/optimize/evaluator.py         # Score applicability to current hardware
core/optimize/tracker.py           # Track what's been tried, source reliability
core/observe/__init__.py
core/observe/dashboard.py          # Optional FastAPI web dashboard
tests/test_feedback_distiller.py
tests/test_feedback_synthesizer.py
tests/test_feedback_preference.py
tests/test_feedback_bridge.py
tests/test_optimize_scout.py
tests/test_optimize_evaluator.py
tests/test_optimize_tracker.py
tests/test_observe_dashboard.py
```

### Files to modify:
```
cli.py                             # Add briefing command
```

---

## Task 1: Distiller — Infrastructure Data Compilation

**Files:**
- Create: `core/feedback/__init__.py`
- Create: `core/feedback/distiller.py`
- Test: `tests/test_feedback_distiller.py`

The distiller compiles daily metrics into a structured JSON briefing. Pure data aggregation — NO LLM needed.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_feedback_distiller.py
from __future__ import annotations
import json
import time
import pytest
from pathlib import Path


@pytest.fixture
def distiller_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "logs").mkdir()
    (data_dir / "reports").mkdir()
    (data_dir / "briefings").mkdir()

    # Create sample metrics
    metrics = [
        {"type": "check", "check": "disk_usage", "value": "72", "status": "ok", "timestamp": time.time()},
        {"type": "check", "check": "ram_usage", "value": "64", "status": "ok", "timestamp": time.time()},
        {"type": "check", "check": "llm_server", "value": "", "status": "fail", "timestamp": time.time()},
        {"type": "tool_call", "tool": "cleanup_system", "duration_ms": 3000, "success": True, "timestamp": time.time()},
    ]
    metrics_file = data_dir / "metrics.jsonl"
    metrics_file.write_text("\n".join(json.dumps(m) for m in metrics))

    # Create sample budget
    budget = {"date": "2026-04-07", "providers": {"groq": {"requests": 34, "tokens_in": 5000, "tokens_out": 2000, "errors": 1}}}
    (data_dir / "llm_budget.json").write_text(json.dumps(budget))

    return data_dir


def test_compile_briefing(distiller_env):
    from core.feedback.distiller import Distiller
    d = Distiller(data_dir=str(distiller_env))
    briefing = d.compile()
    assert "health" in briefing
    assert "llm_usage" in briefing
    assert "action_items" in briefing


def test_briefing_has_health_stats(distiller_env):
    from core.feedback.distiller import Distiller
    d = Distiller(data_dir=str(distiller_env))
    briefing = d.compile()
    assert briefing["health"]["checks_run"] >= 0
    assert "checks_passed" in briefing["health"]
    assert "checks_failed" in briefing["health"]


def test_briefing_has_llm_usage(distiller_env):
    from core.feedback.distiller import Distiller
    d = Distiller(data_dir=str(distiller_env))
    briefing = d.compile()
    assert "groq" in str(briefing["llm_usage"])


def test_briefing_saved_to_file(distiller_env):
    from core.feedback.distiller import Distiller
    d = Distiller(data_dir=str(distiller_env))
    path = d.compile_and_save()
    assert Path(path).exists()
    data = json.loads(Path(path).read_text())
    assert "health" in data


def test_format_telegram(distiller_env):
    from core.feedback.distiller import Distiller
    d = Distiller(data_dir=str(distiller_env))
    briefing = d.compile()
    text = d.format_telegram(briefing)
    assert isinstance(text, str)
    assert len(text) < 4096  # Telegram message limit
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_feedback_distiller.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Implement distiller**

```python
# core/feedback/__init__.py
"""Feedback loop — infrastructure insight generation."""

# core/feedback/distiller.py
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

    def format_telegram(self, briefing: dict) -> str:
        """Format briefing as a Telegram-friendly message (<4096 chars)."""
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_feedback_distiller.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/feedback/__init__.py core/feedback/distiller.py tests/test_feedback_distiller.py
git commit -m "feat: add distiller — nightly infrastructure data compilation into structured briefings"
```

---

## Task 2: Synthesizer — Pattern Detection

**Files:**
- Create: `core/feedback/synthesizer.py`
- Test: `tests/test_feedback_synthesizer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_feedback_synthesizer.py
from __future__ import annotations
import json
import time
import pytest
from pathlib import Path


@pytest.fixture
def synth_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "proposals").mkdir()

    # Create sample metrics with repetitive commands
    metrics = []
    for i in range(6):
        metrics.append({"type": "unhandled_request", "request": "docker logs jellyfin", "timestamp": time.time() - i * 3600})
    # Create alert fatigue
    for i in range(12):
        metrics.append({"type": "check", "check": "swap_usage", "status": "warn", "timestamp": time.time() - i * 900})

    (data_dir / "metrics.jsonl").write_text("\n".join(json.dumps(m) for m in metrics))
    return data_dir


def test_detect_repetitive_commands(synth_env):
    from core.feedback.synthesizer import Synthesizer
    s = Synthesizer(data_dir=str(synth_env))
    patterns = s.detect_patterns()
    repetitive = [p for p in patterns if p["type"] == "repetitive_command"]
    assert len(repetitive) > 0
    assert "docker logs" in repetitive[0]["detail"]


def test_detect_alert_fatigue(synth_env):
    from core.feedback.synthesizer import Synthesizer
    s = Synthesizer(data_dir=str(synth_env))
    patterns = s.detect_patterns()
    fatigue = [p for p in patterns if p["type"] == "alert_fatigue"]
    assert len(fatigue) > 0
    assert "swap_usage" in fatigue[0]["detail"]


def test_create_proposals_from_patterns(synth_env):
    from core.feedback.synthesizer import Synthesizer
    s = Synthesizer(data_dir=str(synth_env))
    proposals = s.propose()
    assert len(proposals) > 0
    assert all("reason" in p for p in proposals)


def test_no_patterns_no_proposals(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "proposals").mkdir()
    (data_dir / "metrics.jsonl").write_text("")
    from core.feedback.synthesizer import Synthesizer
    s = Synthesizer(data_dir=str(data_dir))
    proposals = s.propose()
    assert proposals == []
```

- [ ] **Step 2: Implement synthesizer**

```python
# core/feedback/synthesizer.py
"""Pattern detection — detect repetitive commands, alert fatigue, failed patterns."""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REPETITIVE_THRESHOLD = 5   # Same command 5+ times
ALERT_FATIGUE_THRESHOLD = 10  # Same alert 10+ times


class Synthesizer:
    """Watch operational patterns and propose new tools or adjustments."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)

    def detect_patterns(self) -> list[dict[str, Any]]:
        """Scan metrics for actionable patterns."""
        metrics = self._read_metrics()
        patterns = []
        patterns.extend(self._detect_repetitive(metrics))
        patterns.extend(self._detect_alert_fatigue(metrics))
        patterns.extend(self._detect_failures(metrics))
        return patterns

    def propose(self) -> list[dict[str, Any]]:
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

    def _detect_repetitive(self, metrics: list[dict]) -> list[dict]:
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

    def _detect_alert_fatigue(self, metrics: list[dict]) -> list[dict]:
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

    def _detect_failures(self, metrics: list[dict]) -> list[dict]:
        """Find tools that fail repeatedly."""
        failures = [m.get("tool", "") for m in metrics
                    if m.get("type") == "tool_call" and not m.get("success")]
        counts = Counter(failures)
        patterns = []
        for tool, count in counts.items():
            if count >= 3:
                patterns.append({
                    "type": "repeated_failure",
                    "detail": f"'{tool}' failed {count} times — investigate root cause",
                    "count": count,
                    "proposal_type": "tool_execution",
                })
        return patterns

    def _read_metrics(self) -> list[dict]:
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
```

- [ ] **Step 3: Run tests, commit**

Run: `python3 -m pytest tests/test_feedback_synthesizer.py -v`
Commit: "feat: add synthesizer — detect repetitive commands, alert fatigue, failure patterns"

---

## Task 3: Preference Model

**Files:**
- Create: `core/feedback/preference.py`
- Test: `tests/test_feedback_preference.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_feedback_preference.py
from __future__ import annotations
import pytest


@pytest.fixture
def pref_dir(tmp_path):
    return tmp_path


def test_record_approval(pref_dir):
    from core.feedback.preference import PreferenceModel
    pm = PreferenceModel(data_dir=str(pref_dir))
    pm.record("container_restart", "approved")
    pm.record("container_restart", "approved")
    history = pm.get_history("container_restart")
    assert history["approved"] == 2


def test_record_rejection(pref_dir):
    from core.feedback.preference import PreferenceModel
    pm = PreferenceModel(data_dir=str(pref_dir))
    pm.record("container_restart", "rejected")
    pm.record("container_restart", "rejected")
    pm.record("container_restart", "rejected")
    history = pm.get_history("container_restart")
    assert history["rejected"] == 3


def test_suggest_suppression_after_threshold(pref_dir):
    from core.feedback.preference import PreferenceModel
    pm = PreferenceModel(data_dir=str(pref_dir), min_data_points=5)
    for _ in range(5):
        pm.record("auto_restart_jellyfin", "rejected")
    suggestion = pm.get_suggestion("auto_restart_jellyfin")
    assert suggestion is not None
    assert suggestion["action"] == "suppress"


def test_no_suggestion_below_threshold(pref_dir):
    from core.feedback.preference import PreferenceModel
    pm = PreferenceModel(data_dir=str(pref_dir), min_data_points=5)
    pm.record("cleanup", "approved")
    pm.record("cleanup", "approved")
    suggestion = pm.get_suggestion("cleanup")
    assert suggestion is None  # Only 2 data points, need 5


def test_reset_clears_all(pref_dir):
    from core.feedback.preference import PreferenceModel
    pm = PreferenceModel(data_dir=str(pref_dir))
    pm.record("test", "approved")
    pm.reset()
    history = pm.get_history("test")
    assert history["approved"] == 0


def test_persistence(pref_dir):
    from core.feedback.preference import PreferenceModel
    pm1 = PreferenceModel(data_dir=str(pref_dir))
    pm1.record("test", "approved")
    pm2 = PreferenceModel(data_dir=str(pref_dir))
    assert pm2.get_history("test")["approved"] == 1
```

- [ ] **Step 2: Implement preference model**

```python
# core/feedback/preference.py
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
        data = safe_read_json(self.data_file, default={})
        return data.get(pattern_key, {"approved": 0, "rejected": 0, "total": 0})

    def get_suggestion(self, pattern_key: str) -> Optional[dict[str, Any]]:
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
```

- [ ] **Step 3: Run tests, commit**

Run: `python3 -m pytest tests/test_feedback_preference.py -v`
Commit: "feat: add preference model — advisory approval/rejection pattern tracking"

---

## Task 4: Feedback Bridge

**Files:**
- Create: `core/feedback/bridge.py`
- Test: `tests/test_feedback_bridge.py`

**Depends on:** Phase C Task 7 (core/agents/base.py) and Task 8 (core/agents/chaguli.py)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_feedback_bridge.py
from __future__ import annotations
import json
import pytest
from pathlib import Path


@pytest.fixture
def bridge_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    (bridge_dir / "briefings").mkdir()
    (bridge_dir / "insights_inbox").mkdir()
    return data_dir, bridge_dir


def test_push_briefing(bridge_env):
    from core.feedback.bridge import FeedbackBridge
    data_dir, bridge_dir = bridge_env
    fb = FeedbackBridge(data_dir=str(data_dir), bridge_dir=str(bridge_dir))
    fb.push_briefing({"date": "2026-04-07", "health": {"checks_run": 10}})
    files = list((bridge_dir / "briefings").glob("*.json"))
    assert len(files) == 1


def test_push_insight(bridge_env):
    from core.feedback.bridge import FeedbackBridge
    data_dir, bridge_dir = bridge_env
    fb = FeedbackBridge(data_dir=str(data_dir), bridge_dir=str(bridge_dir))
    fb.push_insight("llm_instability", "LLM server crashed 3x this week", "operational_pattern")
    files = list((bridge_dir / "insights_inbox").glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["key"] == "llm_instability"


def test_push_does_not_crash_on_missing_dir(tmp_path):
    from core.feedback.bridge import FeedbackBridge
    fb = FeedbackBridge(data_dir=str(tmp_path), bridge_dir=str(tmp_path / "nonexistent"))
    # Should not crash, just log warning
    fb.push_briefing({"date": "2026-04-07"})
```

- [ ] **Step 2: Implement feedback bridge**

```python
# core/feedback/bridge.py
"""Push infrastructure insights to the agent via file-based communication."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class FeedbackBridge:
    """Push briefings and insights to the agent's file-based inbox."""

    def __init__(self, data_dir: str, bridge_dir: str):
        self.data_dir = Path(data_dir)
        self.bridge_dir = Path(bridge_dir)

    def push_briefing(self, briefing: dict[str, Any]) -> None:
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
```

- [ ] **Step 3: Run tests, commit**

Run: `python3 -m pytest tests/test_feedback_bridge.py -v`
Commit: "feat: add feedback bridge — push briefings and insights to agent inbox"

---

## Task 5: Optimization Scout

**Files:**
- Create: `core/optimize/__init__.py`
- Create: `core/optimize/scout.py`
- Test: `tests/test_optimize_scout.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_optimize_scout.py
from __future__ import annotations
import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


@pytest.fixture
def scout_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # Write hardware info
    state = {"hardware": {"total_ram_gb": 36, "cpu_model": "Ryzen 4700U", "has_amd_gpu": False, "has_npu": False}}
    (data_dir / "state.json").write_text(json.dumps(state))
    return data_dir


def test_scout_returns_findings(scout_env):
    from core.optimize.scout import Scout
    s = Scout(data_dir=str(scout_env))
    # Mock the HTTP calls
    with patch("core.optimize.scout.httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"tag_name": "v1.0.0", "name": "New release", "body": "Faster inference", "html_url": "https://github.com/test/repo/releases/tag/v1.0.0"}
        ]
        mock_get.return_value = mock_resp
        findings = s.search_github_releases(["test/repo"])
    assert len(findings) > 0
    assert "tag" in findings[0]


def test_scout_handles_network_error(scout_env):
    from core.optimize.scout import Scout
    s = Scout(data_dir=str(scout_env))
    with patch("core.optimize.scout.httpx.get", side_effect=Exception("Network down")):
        findings = s.search_github_releases(["test/repo"])
    assert findings == []


def test_scout_search_all(scout_env):
    from core.optimize.scout import Scout
    s = Scout(data_dir=str(scout_env))
    with patch.object(s, "search_github_releases", return_value=[]):
        with patch.object(s, "search_huggingface", return_value=[]):
            results = s.search_all()
    assert isinstance(results, list)
```

- [ ] **Step 2: Implement scout**

```python
# core/optimize/__init__.py
"""Optimization — discover and evaluate new techniques, models, tools."""

# core/optimize/scout.py
"""Search external sources for new models, techniques, and tools."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from core.resilience.atomic_json import safe_read_json

log = logging.getLogger(__name__)

DEFAULT_GITHUB_REPOS = [
    "ggml-org/llama.cpp",
    "ikawrakow/ik_llama.cpp",
]


class Scout:
    """Search for optimization opportunities."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.state = safe_read_json(self.data_dir / "state.json", default={})

    def search_all(self) -> list[dict[str, Any]]:
        """Run all search sources."""
        findings = []
        findings.extend(self.search_github_releases(DEFAULT_GITHUB_REPOS))
        findings.extend(self.search_huggingface())
        return findings

    def search_github_releases(self, repos: list[str]) -> list[dict[str, Any]]:
        """Check GitHub repos for new releases."""
        findings = []
        for repo in repos:
            try:
                resp = httpx.get(
                    f"https://api.github.com/repos/{repo}/releases",
                    params={"per_page": 3},
                    timeout=10,
                    headers={"Accept": "application/vnd.github.v3+json"},
                )
                if resp.status_code != 200:
                    continue
                for release in resp.json()[:3]:
                    findings.append({
                        "source": "github",
                        "repo": repo,
                        "tag": release.get("tag_name", ""),
                        "name": release.get("name", ""),
                        "body": (release.get("body", "") or "")[:500],
                        "url": release.get("html_url", ""),
                    })
            except Exception as e:
                log.warning(f"GitHub search failed for {repo}: {e}")
        return findings

    def search_huggingface(self) -> list[dict[str, Any]]:
        """Search HuggingFace for new models matching hardware."""
        hw = self.state.get("hardware", {})
        ram_gb = hw.get("total_ram_gb", 0)
        if ram_gb <= 0:
            return []

        max_size_gb = ram_gb * 0.7  # Leave room for OS
        findings = []
        try:
            resp = httpx.get(
                "https://huggingface.co/api/models",
                params={"sort": "lastModified", "direction": -1, "limit": 5,
                        "filter": "gguf"},
                timeout=15,
            )
            if resp.status_code == 200:
                for model in resp.json()[:5]:
                    findings.append({
                        "source": "huggingface",
                        "model_id": model.get("modelId", ""),
                        "last_modified": model.get("lastModified", ""),
                        "tags": model.get("tags", [])[:10],
                    })
        except Exception as e:
            log.warning(f"HuggingFace search failed: {e}")
        return findings
```

- [ ] **Step 3: Run tests, commit**

Run: `python3 -m pytest tests/test_optimize_scout.py -v`
Commit: "feat: add optimization scout — search GitHub releases and HuggingFace for new models"

---

## Task 6: Evaluator + Tracker

**Files:**
- Create: `core/optimize/evaluator.py`
- Create: `core/optimize/tracker.py`
- Test: `tests/test_optimize_evaluator.py`
- Test: `tests/test_optimize_tracker.py`

- [ ] **Step 1: Write failing tests for evaluator**

```python
# tests/test_optimize_evaluator.py
from __future__ import annotations
import pytest


def test_evaluate_applicable_now():
    from core.optimize.evaluator import evaluate_finding
    hw = {"total_ram_gb": 36, "has_amd_gpu": False, "has_npu": False}
    finding = {"source": "github", "repo": "ggml-org/llama.cpp", "tag": "v1.0", "body": "Faster quantization"}
    result = evaluate_finding(finding, hw)
    assert result["applicable_now"] is True


def test_evaluate_not_applicable_npu():
    from core.optimize.evaluator import evaluate_finding
    hw = {"total_ram_gb": 36, "has_amd_gpu": False, "has_npu": False}
    finding = {"source": "github", "repo": "amd/lemonade", "tag": "v10.1", "body": "NPU acceleration"}
    result = evaluate_finding(finding, hw)
    assert result["applicable_now"] is False


def test_evaluate_future_hardware():
    from core.optimize.evaluator import evaluate_finding
    hw = {"total_ram_gb": 36, "has_npu": False}
    planned = {"has_npu": True, "has_amd_gpu": True}
    finding = {"source": "github", "repo": "amd/lemonade", "tag": "v10.1", "body": "NPU support"}
    result = evaluate_finding(finding, hw, planned_hardware=planned)
    assert result["applicable_future"] is True
```

- [ ] **Step 2: Write failing tests for tracker**

```python
# tests/test_optimize_tracker.py
from __future__ import annotations
import pytest


def test_record_finding(tmp_path):
    from core.optimize.tracker import OptimizationTracker
    t = OptimizationTracker(data_dir=str(tmp_path))
    t.record_finding({"source": "github", "repo": "test/repo", "tag": "v1.0"}, "bookmarked")
    history = t.get_history()
    assert len(history) == 1


def test_record_source_reliability(tmp_path):
    from core.optimize.tracker import OptimizationTracker
    t = OptimizationTracker(data_dir=str(tmp_path))
    t.record_source_result("github:ggml-org/llama.cpp", useful=True)
    t.record_source_result("github:ggml-org/llama.cpp", useful=True)
    t.record_source_result("github:ggml-org/llama.cpp", useful=False)
    score = t.get_source_reliability("github:ggml-org/llama.cpp")
    assert 0.5 < score < 1.0


def test_is_already_seen(tmp_path):
    from core.optimize.tracker import OptimizationTracker
    t = OptimizationTracker(data_dir=str(tmp_path))
    t.record_finding({"source": "github", "repo": "test/repo", "tag": "v1.0"}, "applied")
    assert t.is_seen("github", "test/repo", "v1.0") is True
    assert t.is_seen("github", "test/repo", "v2.0") is False
```

- [ ] **Step 3: Implement evaluator**

```python
# core/optimize/evaluator.py
"""Score finding applicability against current and future hardware."""
from __future__ import annotations
from typing import Any, Optional

NPU_KEYWORDS = ["npu", "xdna", "neural processing", "ai accelerator"]
GPU_KEYWORDS = ["gpu", "cuda", "rocm", "vulkan", "opencl", "radeon"]


def evaluate_finding(
    finding: dict[str, Any],
    hardware: dict[str, Any],
    planned_hardware: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Evaluate whether a finding is applicable to current/future hardware."""
    body = (finding.get("body", "") + " " + finding.get("name", "")).lower()
    tags = [t.lower() for t in finding.get("tags", [])]
    all_text = body + " " + " ".join(tags)

    needs_npu = any(kw in all_text for kw in NPU_KEYWORDS)
    needs_gpu = any(kw in all_text for kw in GPU_KEYWORDS)

    applicable_now = True
    if needs_npu and not hardware.get("has_npu", False):
        applicable_now = False
    if needs_gpu and not hardware.get("has_amd_gpu", False) and not hardware.get("has_nvidia", False):
        applicable_now = False

    applicable_future = applicable_now
    if not applicable_now and planned_hardware:
        if needs_npu and planned_hardware.get("has_npu", False):
            applicable_future = True
        if needs_gpu and (planned_hardware.get("has_amd_gpu", False) or planned_hardware.get("has_nvidia", False)):
            applicable_future = True

    return {
        "finding": finding,
        "applicable_now": applicable_now,
        "applicable_future": applicable_future,
        "needs_npu": needs_npu,
        "needs_gpu": needs_gpu,
        "action": "apply" if applicable_now else ("bookmark" if applicable_future else "skip"),
    }
```

- [ ] **Step 4: Implement tracker**

```python
# core/optimize/tracker.py
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
```

- [ ] **Step 5: Run tests, commit**

Run: `python3 -m pytest tests/test_optimize_evaluator.py tests/test_optimize_tracker.py -v`
Commit: "feat: add evaluator + tracker — score findings against hardware, track source reliability"

---

## Task 7: Dashboard (Optional)

**Files:**
- Create: `core/observe/__init__.py`
- Create: `core/observe/dashboard.py`
- Test: `tests/test_observe_dashboard.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_observe_dashboard.py
from __future__ import annotations
import json
import pytest
from pathlib import Path


@pytest.fixture
def dash_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "briefings").mkdir()
    (data_dir / "proposals").mkdir()
    # Write a sample briefing
    briefing = {"date": "2026-04-07", "health": {"checks_run": 10, "checks_passed": 9, "checks_failed": 1}}
    (data_dir / "briefings" / "2026-04-07.json").write_text(json.dumps(briefing))
    return data_dir


def test_dashboard_creates_app(dash_env):
    from core.observe.dashboard import create_app
    app = create_app(data_dir=str(dash_env))
    assert app is not None


def test_health_endpoint(dash_env):
    from core.observe.dashboard import create_app
    from starlette.testclient import TestClient
    app = create_app(data_dir=str(dash_env))
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


def test_briefings_endpoint(dash_env):
    from core.observe.dashboard import create_app
    from starlette.testclient import TestClient
    app = create_app(data_dir=str(dash_env))
    client = TestClient(app)
    resp = client.get("/api/briefings")
    assert resp.status_code == 200
```

- [ ] **Step 2: Implement dashboard**

```python
# core/observe/__init__.py
"""Observability — metrics, dashboard, heartbeat."""

# core/observe/dashboard.py
"""Optional FastAPI web dashboard for AgentHarness observability.

Single file. Server-rendered HTML. No JS framework. LAN-only by default.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


def create_app(data_dir: str, auth_token: str = "") -> Any:
    """Create the FastAPI dashboard app."""
    if not HAS_FASTAPI:
        raise ImportError("FastAPI not installed. Run: pip install fastapi uvicorn")

    app = FastAPI(title="AgentHarness Dashboard")
    data_path = Path(data_dir)

    @app.get("/api/health")
    def api_health():
        heartbeat_file = data_path / "heartbeat.json"
        if heartbeat_file.exists():
            try:
                hb = json.loads(heartbeat_file.read_text())
                return JSONResponse({"status": "ok", "heartbeat": hb})
            except Exception:
                pass
        return JSONResponse({"status": "unknown"})

    @app.get("/api/briefings")
    def api_briefings():
        briefings_dir = data_path / "briefings"
        briefings = []
        if briefings_dir.is_dir():
            for f in sorted(briefings_dir.glob("*.json"), reverse=True)[:7]:
                try:
                    briefings.append(json.loads(f.read_text()))
                except Exception:
                    continue
        return JSONResponse(briefings)

    @app.get("/api/budget")
    def api_budget():
        budget_file = data_path / "llm_budget.json"
        if budget_file.exists():
            try:
                return JSONResponse(json.loads(budget_file.read_text()))
            except Exception:
                pass
        return JSONResponse({})

    @app.get("/api/proposals")
    def api_proposals():
        proposals_dir = data_path / "proposals"
        proposals = []
        if proposals_dir.is_dir():
            for f in sorted(proposals_dir.glob("*.json"), reverse=True)[:20]:
                try:
                    proposals.append(json.loads(f.read_text()))
                except Exception:
                    continue
        return JSONResponse(proposals)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return """<!DOCTYPE html>
<html><head><title>AgentHarness</title>
<style>body{font-family:monospace;max-width:800px;margin:40px auto;padding:0 20px}
h1{border-bottom:2px solid #333}a{color:#0066cc}</style></head>
<body><h1>AgentHarness Dashboard</h1>
<ul>
<li><a href="/api/health">Health</a></li>
<li><a href="/api/briefings">Briefings</a></li>
<li><a href="/api/budget">LLM Budget</a></li>
<li><a href="/api/proposals">Proposals</a></li>
</ul></body></html>"""

    return app
```

- [ ] **Step 3: Run tests, commit**

Run: `pip3 install fastapi httpx starlette && python3 -m pytest tests/test_observe_dashboard.py -v`
Commit: "feat: add optional dashboard — FastAPI endpoints for health, briefings, budget, proposals"

---

## Task 8: CLI Briefing Command

**Files:**
- Modify: `cli.py`

- [ ] **Step 1: Add briefing command**

Add `cmd_briefing(args)` that reads the latest briefing JSON and formats it for terminal output. Add to parser and dispatch.

- [ ] **Step 2: Commit**

Commit: "feat: add briefing CLI command"

---

## Task 9: Full Test Suite + Final Validation

- [ ] **Step 1: Run all tests**

```bash
python3 -m pytest tests/ -v
```

Expected: All tests pass (240+).

- [ ] **Step 2: End-to-end validation**

```bash
export AGENTHARNESS_HOME="$(pwd)" AH_DATA_DIR="$(pwd)/data"
python3 cli.py discover
python3 cli.py briefing
python3 cli.py budget
```

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat: Phase D complete — distiller, synthesizer, preference model, scout, dashboard

Closes the learning loop: nightly briefings, pattern detection, optimization
scouting, advisory preference model, optional web dashboard."
```

---

## Summary

**Phase D delivers:**
- Distiller — nightly infrastructure data compilation into structured JSON
- Synthesizer — detect repetitive commands, alert fatigue, failure patterns → proposals
- Preference Model — advisory approval/rejection tracking (never auto-suppresses)
- Feedback Bridge — push briefings and insights to agent via file inbox
- Optimization Scout — search GitHub releases, HuggingFace for new models
- Evaluator — score findings against current + future hardware
- Tracker — track what's been tried, source reliability scores
- Dashboard (optional) — FastAPI endpoints for health, briefings, budget, proposals
- CLI briefing command

**Phase D does NOT include:**
- LLM-based briefing formatting (agent's job)
- Auto-applying optimizations (always HITL)
- Reddit/arxiv search (future scout sources)

**Estimated tasks:** 9 tasks, ~35 steps
**Test coverage:** ~30 new tests across 8 test files
