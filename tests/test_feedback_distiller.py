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
