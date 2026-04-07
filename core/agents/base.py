"""Abstract agent bridge interface.

The bridge is a one-way data flow from AgentHarness to any agent.
AgentHarness writes data; the agent reads at its own pace.

Communication contract:
1. AgentHarness writes JSON files to known directories
2. Agent reads them at its own pace
3. Agent deletes files after processing (or AgentHarness cleans up after TTL)

Three communication channels:
- briefings/    — daily executive briefings (infra summary)
- insights_inbox/ — infrastructure pattern insights
- tool_updates/   — tool additions, removals, configuration changes
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Briefing:
    """Daily infrastructure briefing for the agent."""
    date: str
    summary: str
    sections: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    proposals_pending: int = 0
    alerts: List[str] = field(default_factory=list)


@dataclass
class Insight:
    """Infrastructure insight (pattern, anomaly, recommendation)."""
    insight_type: str  # "pattern", "anomaly", "recommendation"
    title: str
    description: str
    priority: str = "low"  # "low", "medium", "high", "critical"
    data: Dict[str, Any] = field(default_factory=dict)
    source: str = "agentharness"


@dataclass
class ToolUpdate:
    """Notification about tool changes."""
    action: str  # "added", "removed", "updated", "promoted", "demoted"
    tool_name: str
    description: str = ""
    bundle: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CapabilityReport:
    """Report of what integration level was achieved with an agent."""
    agent: str
    communication: Dict[str, Any] = field(default_factory=dict)
    tools_integration: str = "none"
    capabilities_detected: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class AgentBridge(abc.ABC):
    """Abstract base class for agent bridge implementations.

    Each agent type implements this interface. The reference
    implementation is ChaguliBridge (chaguli.py).
    """

    @abc.abstractmethod
    def send_briefing(self, briefing: Briefing) -> bool:
        """Send a daily briefing to the agent. Returns True on success."""
        ...

    @abc.abstractmethod
    def send_insight(self, insight: Insight) -> bool:
        """Send an infrastructure insight to the agent. Returns True on success."""
        ...

    @abc.abstractmethod
    def send_tool_update(self, update: ToolUpdate) -> bool:
        """Notify the agent about a tool change. Returns True on success."""
        ...

    @abc.abstractmethod
    def generate_capability_report(self) -> CapabilityReport:
        """Probe the agent and report what integration level was achieved."""
        ...
