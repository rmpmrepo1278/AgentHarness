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
