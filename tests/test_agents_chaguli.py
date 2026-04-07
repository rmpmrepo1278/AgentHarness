from __future__ import annotations
import json
import os
import pytest


@pytest.fixture
def bridge_dirs(tmp_path):
    """Create the file-based communication directories."""
    briefings = tmp_path / "briefings"
    insights = tmp_path / "insights_inbox"
    tool_updates = tmp_path / "tool_updates"
    briefings.mkdir()
    insights.mkdir()
    tool_updates.mkdir()
    return {
        "briefings_dir": str(briefings),
        "insights_dir": str(insights),
        "tool_updates_dir": str(tool_updates),
        "agent_dir": str(tmp_path),
    }


def test_send_briefing_writes_json(bridge_dirs):
    from core.agents.chaguli import ChaguliBridge
    from core.agents.base import Briefing

    bridge = ChaguliBridge(**bridge_dirs)
    briefing = Briefing(
        date="2026-04-07",
        summary="All systems healthy",
        sections={"disk": "47% used", "ram": "12GB/32GB"},
        proposals_pending=2,
    )
    result = bridge.send_briefing(briefing)
    assert result is True

    files = list(
        __import__("pathlib").Path(bridge_dirs["briefings_dir"]).glob("*.json")
    )
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["date"] == "2026-04-07"
    assert data["summary"] == "All systems healthy"


def test_send_insight_writes_json(bridge_dirs):
    from core.agents.chaguli import ChaguliBridge
    from core.agents.base import Insight

    bridge = ChaguliBridge(**bridge_dirs)
    insight = Insight(
        insight_type="pattern",
        title="Disk trending up",
        description="Disk usage +5% over 7 days",
        priority="medium",
    )
    result = bridge.send_insight(insight)
    assert result is True

    from pathlib import Path
    files = list(Path(bridge_dirs["insights_dir"]).glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["title"] == "Disk trending up"


def test_send_tool_update_writes_json(bridge_dirs):
    from core.agents.chaguli import ChaguliBridge
    from core.agents.base import ToolUpdate

    bridge = ChaguliBridge(**bridge_dirs)
    update = ToolUpdate(
        action="added",
        tool_name="check_nvme",
        description="NVMe health check",
        bundle="homelab",
    )
    result = bridge.send_tool_update(update)
    assert result is True

    from pathlib import Path
    files = list(Path(bridge_dirs["tool_updates_dir"]).glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["tool_name"] == "check_nvme"


def test_generate_capability_report_no_agent(tmp_path):
    from core.agents.chaguli import ChaguliBridge
    bridge = ChaguliBridge(
        briefings_dir=str(tmp_path / "b"),
        insights_dir=str(tmp_path / "i"),
        tool_updates_dir=str(tmp_path / "t"),
        agent_dir=str(tmp_path / "nonexistent"),
    )
    report = bridge.generate_capability_report()
    assert report.agent == "chaguli"
    assert report.communication.get("file_inbox") is not None
    assert len(report.warnings) > 0  # Should warn agent dir not found


def test_generate_capability_report_with_agent(bridge_dirs):
    from core.agents.chaguli import ChaguliBridge
    from pathlib import Path

    # Simulate Chaguli files existing
    agent_dir = Path(bridge_dirs["agent_dir"])
    (agent_dir / "memory.py").write_text("# memory module")
    (agent_dir / "briefings.py").write_text("# briefings module")

    bridge = ChaguliBridge(**bridge_dirs)
    report = bridge.generate_capability_report()
    assert report.agent == "chaguli"
    assert "memory" in report.capabilities_detected
    assert "briefings" in report.capabilities_detected


def test_multiple_briefings_unique_filenames(bridge_dirs):
    from core.agents.chaguli import ChaguliBridge
    from core.agents.base import Briefing
    from pathlib import Path

    bridge = ChaguliBridge(**bridge_dirs)
    for i in range(3):
        bridge.send_briefing(Briefing(
            date=f"2026-04-0{i+1}",
            summary=f"Day {i+1}",
        ))

    files = list(Path(bridge_dirs["briefings_dir"]).glob("*.json"))
    assert len(files) == 3


def test_cleanup_old_files(bridge_dirs):
    from core.agents.chaguli import ChaguliBridge
    from pathlib import Path
    import time

    bridge = ChaguliBridge(**bridge_dirs, ttl_seconds=0)

    # Write a file and backdate it
    f = Path(bridge_dirs["briefings_dir"]) / "old.json"
    f.write_text('{"test": true}')
    # Set mtime to the past
    old_time = time.time() - 100
    os.utime(f, (old_time, old_time))

    cleaned = bridge.cleanup(bridge_dirs["briefings_dir"])
    assert cleaned >= 1
    assert not f.exists()
