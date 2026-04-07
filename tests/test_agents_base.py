from __future__ import annotations
import pytest


def test_agent_bridge_is_abstract():
    from core.agents.base import AgentBridge
    with pytest.raises(TypeError):
        AgentBridge()


def test_bridge_requires_send_briefing():
    from core.agents.base import AgentBridge
    # Must implement send_briefing
    class Incomplete(AgentBridge):
        def send_insight(self, insight): ...
        def send_tool_update(self, update): ...
        def generate_capability_report(self): ...
    with pytest.raises(TypeError):
        Incomplete()


def test_bridge_requires_send_insight():
    from core.agents.base import AgentBridge
    class Incomplete(AgentBridge):
        def send_briefing(self, briefing): ...
        def send_tool_update(self, update): ...
        def generate_capability_report(self): ...
    with pytest.raises(TypeError):
        Incomplete()


def test_bridge_requires_send_tool_update():
    from core.agents.base import AgentBridge
    class Incomplete(AgentBridge):
        def send_briefing(self, briefing): ...
        def send_insight(self, insight): ...
        def generate_capability_report(self): ...
    with pytest.raises(TypeError):
        Incomplete()


def test_bridge_requires_generate_capability_report():
    from core.agents.base import AgentBridge
    class Incomplete(AgentBridge):
        def send_briefing(self, briefing): ...
        def send_insight(self, insight): ...
        def send_tool_update(self, update): ...
    with pytest.raises(TypeError):
        Incomplete()


def test_concrete_bridge_works():
    from core.agents.base import AgentBridge
    class TestBridge(AgentBridge):
        def send_briefing(self, briefing): return True
        def send_insight(self, insight): return True
        def send_tool_update(self, update): return True
        def generate_capability_report(self): return {}
    bridge = TestBridge()
    assert bridge.send_briefing({"summary": "test"}) is True


def test_briefing_dataclass():
    from core.agents.base import Briefing
    b = Briefing(
        date="2026-04-07",
        summary="All systems healthy",
        sections={"disk": "OK", "ram": "OK"},
    )
    assert b.date == "2026-04-07"
    assert b.summary == "All systems healthy"


def test_insight_dataclass():
    from core.agents.base import Insight
    i = Insight(
        insight_type="pattern",
        title="Disk usage trending up",
        description="Disk usage increased 5% over 7 days",
        priority="medium",
    )
    assert i.insight_type == "pattern"
    assert i.priority == "medium"


def test_tool_update_dataclass():
    from core.agents.base import ToolUpdate
    t = ToolUpdate(
        action="added",
        tool_name="check_nvme_health",
        description="NVMe health monitoring",
    )
    assert t.action == "added"


def test_capability_report_dataclass():
    from core.agents.base import CapabilityReport
    r = CapabilityReport(
        agent="chaguli",
        communication={"file_inbox": "/opt/chaguli/inbox/", "webhook": None},
        tools_integration="file_based",
        capabilities_detected=["heartbeat", "briefings"],
    )
    assert r.agent == "chaguli"
    assert "heartbeat" in r.capabilities_detected
