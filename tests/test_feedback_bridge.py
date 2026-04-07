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
