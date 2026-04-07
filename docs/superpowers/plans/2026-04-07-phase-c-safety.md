# Phase C: Safety + Approval — HITL Gateway + Sandbox Runner + Agent Bridge

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the human-in-the-loop approval gateway so risky operations require explicit sign-off, wire sandbox execution (direct + containerized) into a unified runner, and create the agent bridge interface with a Chaguli reference implementation — enabling safe, auditable, approval-gated tool execution with file-based agent communication.

**Architecture:** A `Proposal` dataclass represents a pending action with tier, preconditions, and expiry. The `ApprovalGateway` manages the proposal lifecycle (create/approve/reject/expire) with JSON files in `proposals/`. A `SandboxRunner` dispatches tool execution to either `DirectRunner` (host, trusted) or `ContainerRunner` (Docker, untrusted) based on tool config. The `AgentBridge` abstract base class defines a file-based communication contract; `ChaguliBridge` implements it by writing to Chaguli's discovered directories. CLI commands provide local approval/rejection.

**Tech Stack:** Python 3.9+, `from __future__ import annotations` everywhere, existing core/ modules from Phase A (state, discovery, container_policy, sanitize, audit) and Phase B (scheduler)

**Spec:** `docs/superpowers/specs/2026-04-07-agentharness-v2-design.md` (Sections 3, 11)

**Depends on Phase A:** discovery engine, state manager, container_policy.py, sanitize.py, audit.py
**Depends on Phase B:** scheduler (executes approved proposals), budget tracker

---

## File Structure

### New files to create:
```
core/approval/__init__.py
core/approval/gateway.py            # Proposal creation, lifecycle, signing
core/approval/policies.py           # Tier definitions (auto/notify/approve)
core/approval/auth.py               # Approval validation (CLI + Chaguli path)
core/sandbox/__init__.py
core/sandbox/runner.py              # Dispatch to direct or containerized
core/sandbox/direct.py              # Host execution with timeout
core/sandbox/docker_sandbox.py      # Ephemeral Docker container execution
core/agents/__init__.py
core/agents/base.py                 # Abstract agent bridge interface
core/agents/chaguli.py              # Chaguli reference implementation
tests/test_approval_gateway.py
tests/test_approval_policies.py
tests/test_approval_auth.py
tests/test_sandbox_runner.py
tests/test_sandbox_direct.py
tests/test_sandbox_docker.py
tests/test_agents_base.py
tests/test_agents_chaguli.py
```

### Files to modify:
```
cli.py                               # Add proposals, approve, reject commands
```

---

## Task 1: Approval Policies — Tier Definitions

**Files:**
- Create: `core/approval/__init__.py`
- Create: `core/approval/policies.py`
- Test: `tests/test_approval_policies.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_approval_policies.py
from __future__ import annotations
import pytest


def test_approval_tier_enum_values():
    from core.approval.policies import ApprovalTier
    assert ApprovalTier.AUTO.value == "auto"
    assert ApprovalTier.NOTIFY.value == "notify"
    assert ApprovalTier.APPROVE.value == "approve"


def test_resolve_tier_auto_for_read_tools():
    from core.approval.policies import resolve_tier
    assert resolve_tier("check_disk").value == "auto"
    assert resolve_tier("read_logs").value == "auto"
    assert resolve_tier("list_containers").value == "auto"
    assert resolve_tier("status_check").value == "auto"


def test_resolve_tier_notify_for_benchmarks():
    from core.approval.policies import resolve_tier
    assert resolve_tier("run_benchmark").value == "notify"
    assert resolve_tier("run_security_audit").value == "notify"


def test_resolve_tier_approve_for_mutations():
    from core.approval.policies import resolve_tier
    assert resolve_tier("cleanup_system").value == "approve"
    assert resolve_tier("deploy_repo").value == "approve"


def test_self_modification_always_approve():
    from core.approval.policies import resolve_tier
    assert resolve_tier("tool_synthesis", is_self_modification=True).value == "approve"
    assert resolve_tier("config_change", is_self_modification=True).value == "approve"
    assert resolve_tier("optimization_apply", is_self_modification=True).value == "approve"
    assert resolve_tier("provider_switch", is_self_modification=True).value == "approve"


def test_community_bundle_always_approve():
    from core.approval.policies import resolve_tier
    assert resolve_tier("community_check", is_community=True).value == "approve"


def test_resolve_tier_with_explicit_override():
    from core.approval.policies import resolve_tier
    assert resolve_tier("check_disk", override_tier="approve").value == "approve"


def test_proposal_types():
    from core.approval.policies import ProposalType
    assert ProposalType.TOOL_EXECUTION.value == "tool_execution"
    assert ProposalType.TOOL_SYNTHESIS.value == "tool_synthesis"
    assert ProposalType.CONFIG_CHANGE.value == "config_change"
    assert ProposalType.OPTIMIZATION_APPLY.value == "optimization_apply"
    assert ProposalType.PROVIDER_SWITCH.value == "provider_switch"
    assert ProposalType.TRUST_PROMOTION.value == "trust_promotion"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_approval_policies.py -v`
Expected: FAIL -- `ModuleNotFoundError: No module named 'core.approval'`

- [ ] **Step 3: Implement approval policies**

```python
# core/approval/__init__.py
"""HITL approval gateway — proposal creation, lifecycle, and execution gating."""

# core/approval/policies.py
"""Approval tier definitions and resolution logic.

Three tiers:
- auto: runs immediately, no human needed (read-only operations)
- notify: runs immediately, reports what it did via Telegram
- approve: creates a proposal, waits for human sign-off

Self-modification and community bundles ALWAYS require approve tier.
"""
from __future__ import annotations

import enum
import logging
import re
from typing import Optional

log = logging.getLogger("approval.policies")

# Patterns for auto-tier tools (read-only, informational)
AUTO_PATTERNS = [
    re.compile(r"^check_"),
    re.compile(r"^read_"),
    re.compile(r"^list_"),
    re.compile(r"^status_"),
    re.compile(r"^diagnose_"),
    re.compile(r"^get_"),
]

# Patterns for notify-tier tools (safe mutations, user wants to know)
NOTIFY_PATTERNS = [
    re.compile(r"^run_benchmark"),
    re.compile(r"^run_security_audit"),
    re.compile(r"^run_test"),
]


class ApprovalTier(enum.Enum):
    AUTO = "auto"
    NOTIFY = "notify"
    APPROVE = "approve"


class ProposalType(enum.Enum):
    TOOL_EXECUTION = "tool_execution"
    TOOL_SYNTHESIS = "tool_synthesis"
    CONFIG_CHANGE = "config_change"
    OPTIMIZATION_APPLY = "optimization_apply"
    PROVIDER_SWITCH = "provider_switch"
    TRUST_PROMOTION = "trust_promotion"


def resolve_tier(
    tool_name: str,
    is_self_modification: bool = False,
    is_community: bool = False,
    override_tier: Optional[str] = None,
) -> ApprovalTier:
    """Resolve the approval tier for a given tool invocation.

    Priority:
    1. Self-modification or community -> always APPROVE
    2. Explicit override from tool config -> use that
    3. Pattern matching on tool name
    4. Default -> APPROVE (safe default)
    """
    # Self-modification and community always require approval
    if is_self_modification or is_community:
        return ApprovalTier.APPROVE

    # Explicit override
    if override_tier:
        try:
            return ApprovalTier(override_tier)
        except ValueError:
            log.warning("Invalid override tier %r, defaulting to APPROVE", override_tier)
            return ApprovalTier.APPROVE

    # Pattern matching
    for pattern in AUTO_PATTERNS:
        if pattern.match(tool_name):
            return ApprovalTier.AUTO

    for pattern in NOTIFY_PATTERNS:
        if pattern.match(tool_name):
            return ApprovalTier.NOTIFY

    # Default: require approval (safe default)
    return ApprovalTier.APPROVE
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_approval_policies.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add core/approval/__init__.py core/approval/policies.py tests/test_approval_policies.py
git commit -m "Phase C Task 1: approval tier policies with auto/notify/approve resolution"
```

---

## Task 2: Proposal Dataclass + Gateway Lifecycle

**Files:**
- Create: `core/approval/gateway.py`
- Test: `tests/test_approval_gateway.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_approval_gateway.py
from __future__ import annotations
import json
import os
import time
import pytest


@pytest.fixture
def proposals_dir(tmp_path):
    d = tmp_path / "proposals"
    d.mkdir()
    return d


def test_create_proposal(proposals_dir):
    from core.approval.gateway import ApprovalGateway, Proposal
    gw = ApprovalGateway(proposals_dir=str(proposals_dir))
    p = gw.create(
        tool_name="cleanup_system",
        args={"max_age_days": 30},
        reason="Disk usage at 87%",
        proposal_type="tool_execution",
        preconditions={"disk_usage_pct": 87},
    )
    assert p.status == "pending"
    assert p.tool_name == "cleanup_system"
    assert p.proposal_id is not None
    assert len(p.proposal_id) == 6  # Short readable IDs


def test_proposal_persisted_as_json(proposals_dir):
    from core.approval.gateway import ApprovalGateway
    gw = ApprovalGateway(proposals_dir=str(proposals_dir))
    p = gw.create(
        tool_name="deploy_repo",
        args={"repo": "myapp"},
        reason="New version available",
        proposal_type="tool_execution",
    )
    json_file = proposals_dir / f"{p.proposal_id}.json"
    assert json_file.exists()
    data = json.loads(json_file.read_text())
    assert data["tool_name"] == "deploy_repo"
    assert data["status"] == "pending"


def test_approve_proposal(proposals_dir):
    from core.approval.gateway import ApprovalGateway
    gw = ApprovalGateway(proposals_dir=str(proposals_dir))
    p = gw.create(
        tool_name="cleanup_system",
        args={},
        reason="test",
        proposal_type="tool_execution",
    )
    result = gw.approve(p.proposal_id)
    assert result.status == "approved"
    # Verify persisted
    data = json.loads((proposals_dir / f"{p.proposal_id}.json").read_text())
    assert data["status"] == "approved"


def test_reject_proposal_with_reason(proposals_dir):
    from core.approval.gateway import ApprovalGateway
    gw = ApprovalGateway(proposals_dir=str(proposals_dir))
    p = gw.create(
        tool_name="cleanup_system",
        args={},
        reason="test",
        proposal_type="tool_execution",
    )
    result = gw.reject(p.proposal_id, reason="Not needed right now")
    assert result.status == "rejected"
    assert result.rejection_reason == "Not needed right now"


def test_approve_nonexistent_raises(proposals_dir):
    from core.approval.gateway import ApprovalGateway, ProposalNotFoundError
    gw = ApprovalGateway(proposals_dir=str(proposals_dir))
    with pytest.raises(ProposalNotFoundError):
        gw.approve("ZZZZZZ")


def test_approve_already_approved_raises(proposals_dir):
    from core.approval.gateway import ApprovalGateway, InvalidTransitionError
    gw = ApprovalGateway(proposals_dir=str(proposals_dir))
    p = gw.create(
        tool_name="cleanup_system",
        args={},
        reason="test",
        proposal_type="tool_execution",
    )
    gw.approve(p.proposal_id)
    with pytest.raises(InvalidTransitionError):
        gw.approve(p.proposal_id)


def test_expired_proposal_cannot_be_approved(proposals_dir):
    from core.approval.gateway import ApprovalGateway, InvalidTransitionError
    gw = ApprovalGateway(proposals_dir=str(proposals_dir), default_ttl_seconds=0)
    p = gw.create(
        tool_name="cleanup_system",
        args={},
        reason="test",
        proposal_type="tool_execution",
    )
    # Force expiry by setting created_at in the past
    import time
    p_data = json.loads((proposals_dir / f"{p.proposal_id}.json").read_text())
    p_data["created_at"] = time.time() - 10
    p_data["expires_at"] = time.time() - 5
    (proposals_dir / f"{p.proposal_id}.json").write_text(json.dumps(p_data))

    with pytest.raises(InvalidTransitionError, match="expired"):
        gw.approve(p.proposal_id)


def test_list_pending(proposals_dir):
    from core.approval.gateway import ApprovalGateway
    gw = ApprovalGateway(proposals_dir=str(proposals_dir))
    gw.create(tool_name="a", args={}, reason="r1", proposal_type="tool_execution")
    gw.create(tool_name="b", args={}, reason="r2", proposal_type="tool_execution")
    p3 = gw.create(tool_name="c", args={}, reason="r3", proposal_type="tool_execution")
    gw.approve(p3.proposal_id)
    pending = gw.list_pending()
    assert len(pending) == 2


def test_proposal_state_hash(proposals_dir):
    from core.approval.gateway import ApprovalGateway
    gw = ApprovalGateway(proposals_dir=str(proposals_dir))
    p = gw.create(
        tool_name="cleanup_system",
        args={"max_age_days": 30},
        reason="Disk at 87%",
        proposal_type="tool_execution",
        preconditions={"disk_usage_pct": 87},
    )
    assert p.state_hash is not None
    assert len(p.state_hash) == 64  # SHA-256 hex digest


def test_expire_stale_proposals(proposals_dir):
    from core.approval.gateway import ApprovalGateway
    gw = ApprovalGateway(proposals_dir=str(proposals_dir), default_ttl_seconds=0)
    p = gw.create(tool_name="a", args={}, reason="r", proposal_type="tool_execution")
    # Force expiry
    p_data = json.loads((proposals_dir / f"{p.proposal_id}.json").read_text())
    p_data["created_at"] = time.time() - 10
    p_data["expires_at"] = time.time() - 5
    (proposals_dir / f"{p.proposal_id}.json").write_text(json.dumps(p_data))

    expired = gw.expire_stale()
    assert len(expired) == 1
    assert expired[0] == p.proposal_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_approval_gateway.py -v`
Expected: FAIL

- [ ] **Step 3: Implement gateway**

```python
# core/approval/gateway.py
"""HITL Approval Gateway — proposal creation, lifecycle, and persistence.

Proposals are stored as individual JSON files in the proposals/ directory.
Each proposal has a unique 6-character ID, a lifecycle status
(pending/approved/rejected/expired/stale), and optional preconditions
that are revalidated before execution.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.approval.policies import ApprovalTier, ProposalType

log = logging.getLogger("approval.gateway")

DEFAULT_TTL_SECONDS = 3 * 24 * 3600  # 3 days


class ProposalNotFoundError(Exception):
    pass


class InvalidTransitionError(Exception):
    pass


def _generate_id() -> str:
    """Generate a short, readable proposal ID (6 hex chars)."""
    return secrets.token_hex(3).upper()


def _compute_state_hash(tool_name: str, args: dict, preconditions: Optional[dict]) -> str:
    """SHA-256 hash of proposal content for tamper detection."""
    content = json.dumps(
        {"tool_name": tool_name, "args": args, "preconditions": preconditions},
        sort_keys=True,
    )
    return hashlib.sha256(content.encode()).hexdigest()


@dataclass
class Proposal:
    proposal_id: str
    tool_name: str
    args: Dict[str, Any]
    reason: str
    proposal_type: str
    status: str = "pending"
    preconditions: Optional[Dict[str, Any]] = None
    state_hash: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    approved_at: Optional[float] = None
    rejected_at: Optional[float] = None
    rejection_reason: Optional[str] = None
    sandbox_mode: str = "direct"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Proposal:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class ApprovalGateway:
    """Manages the full proposal lifecycle.

    Proposals are persisted as JSON files in proposals_dir.
    Status transitions: pending -> approved | rejected | expired | stale
    """

    def __init__(
        self,
        proposals_dir: str,
        default_ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ):
        self.proposals_dir = Path(proposals_dir)
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        self.default_ttl_seconds = default_ttl_seconds

    def _proposal_path(self, proposal_id: str) -> Path:
        return self.proposals_dir / f"{proposal_id}.json"

    def _load(self, proposal_id: str) -> Proposal:
        path = self._proposal_path(proposal_id)
        if not path.exists():
            raise ProposalNotFoundError(f"Proposal {proposal_id} not found")
        data = json.loads(path.read_text())
        return Proposal.from_dict(data)

    def _save(self, proposal: Proposal) -> None:
        path = self._proposal_path(proposal.proposal_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(proposal.to_dict(), indent=2))
        os.rename(tmp, path)

    def _is_expired(self, proposal: Proposal) -> bool:
        if proposal.expires_at is None:
            return False
        return time.time() > proposal.expires_at

    def create(
        self,
        tool_name: str,
        args: dict,
        reason: str,
        proposal_type: str,
        preconditions: Optional[dict] = None,
        sandbox_mode: str = "direct",
    ) -> Proposal:
        """Create a new proposal and persist it."""
        now = time.time()
        proposal_id = _generate_id()

        # Ensure unique ID
        while self._proposal_path(proposal_id).exists():
            proposal_id = _generate_id()

        proposal = Proposal(
            proposal_id=proposal_id,
            tool_name=tool_name,
            args=args,
            reason=reason,
            proposal_type=proposal_type,
            preconditions=preconditions,
            state_hash=_compute_state_hash(tool_name, args, preconditions),
            created_at=now,
            expires_at=now + self.default_ttl_seconds,
            sandbox_mode=sandbox_mode,
        )
        self._save(proposal)
        log.info("Created proposal %s for %s: %s", proposal_id, tool_name, reason)
        return proposal

    def approve(self, proposal_id: str) -> Proposal:
        """Approve a pending proposal."""
        proposal = self._load(proposal_id)

        if self._is_expired(proposal):
            proposal.status = "expired"
            self._save(proposal)
            raise InvalidTransitionError(
                f"Proposal {proposal_id} has expired"
            )

        if proposal.status != "pending":
            raise InvalidTransitionError(
                f"Cannot approve proposal {proposal_id} in status {proposal.status!r}"
            )

        proposal.status = "approved"
        proposal.approved_at = time.time()
        self._save(proposal)
        log.info("Approved proposal %s (%s)", proposal_id, proposal.tool_name)
        return proposal

    def reject(self, proposal_id: str, reason: str = "") -> Proposal:
        """Reject a pending proposal with an optional reason."""
        proposal = self._load(proposal_id)

        if proposal.status != "pending":
            raise InvalidTransitionError(
                f"Cannot reject proposal {proposal_id} in status {proposal.status!r}"
            )

        proposal.status = "rejected"
        proposal.rejected_at = time.time()
        proposal.rejection_reason = reason
        self._save(proposal)
        log.info("Rejected proposal %s: %s", proposal_id, reason)
        return proposal

    def list_pending(self) -> List[Proposal]:
        """List all proposals with status 'pending' (not expired)."""
        pending = []
        for path in sorted(self.proposals_dir.glob("*.json")):
            if path.name.endswith(".tmp"):
                continue
            try:
                proposal = Proposal.from_dict(json.loads(path.read_text()))
                if proposal.status == "pending" and not self._is_expired(proposal):
                    pending.append(proposal)
            except (json.JSONDecodeError, KeyError):
                log.warning("Skipping corrupt proposal file: %s", path)
        return pending

    def get(self, proposal_id: str) -> Proposal:
        """Get a proposal by ID."""
        return self._load(proposal_id)

    def expire_stale(self) -> List[str]:
        """Find and mark all expired proposals. Returns list of expired IDs."""
        expired_ids = []
        for path in self.proposals_dir.glob("*.json"):
            if path.name.endswith(".tmp"):
                continue
            try:
                proposal = Proposal.from_dict(json.loads(path.read_text()))
                if proposal.status == "pending" and self._is_expired(proposal):
                    proposal.status = "expired"
                    self._save(proposal)
                    expired_ids.append(proposal.proposal_id)
                    log.info("Expired proposal %s (%s)", proposal.proposal_id, proposal.tool_name)
            except (json.JSONDecodeError, KeyError):
                continue
        return expired_ids

    def mark_stale(self, proposal_id: str, reason: str = "") -> Proposal:
        """Mark a proposal as stale (preconditions changed)."""
        proposal = self._load(proposal_id)
        proposal.status = "stale"
        proposal.rejection_reason = reason or "Preconditions changed since creation"
        self._save(proposal)
        log.info("Marked proposal %s as stale: %s", proposal_id, reason)
        return proposal
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_approval_gateway.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add core/approval/gateway.py tests/test_approval_gateway.py
git commit -m "Phase C Task 2: approval gateway with proposal lifecycle and JSON persistence"
```

---

## Task 3: Approval Authentication

**Files:**
- Create: `core/approval/auth.py`
- Test: `tests/test_approval_auth.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_approval_auth.py
from __future__ import annotations
import json
import time
import pytest


@pytest.fixture
def proposals_dir(tmp_path):
    d = tmp_path / "proposals"
    d.mkdir()
    return d


@pytest.fixture
def gateway(proposals_dir):
    from core.approval.gateway import ApprovalGateway
    return ApprovalGateway(proposals_dir=str(proposals_dir))


def test_cli_approve_valid(gateway):
    from core.approval.auth import validate_and_approve
    p = gateway.create(
        tool_name="cleanup_system", args={}, reason="test",
        proposal_type="tool_execution",
    )
    result = validate_and_approve(gateway, p.proposal_id, source="cli")
    assert result.status == "approved"


def test_cli_reject_valid(gateway):
    from core.approval.auth import validate_and_reject
    p = gateway.create(
        tool_name="cleanup_system", args={}, reason="test",
        proposal_type="tool_execution",
    )
    result = validate_and_reject(
        gateway, p.proposal_id, reason="Not needed", source="cli",
    )
    assert result.status == "rejected"


def test_agent_approve_valid(gateway):
    from core.approval.auth import validate_and_approve
    p = gateway.create(
        tool_name="cleanup_system", args={}, reason="test",
        proposal_type="tool_execution",
    )
    result = validate_and_approve(gateway, p.proposal_id, source="agent:chaguli")
    assert result.status == "approved"


def test_approve_expired_raises(gateway):
    from core.approval.auth import validate_and_approve, ApprovalValidationError
    p = gateway.create(
        tool_name="a", args={}, reason="test",
        proposal_type="tool_execution",
    )
    # Force expiry
    path = gateway._proposal_path(p.proposal_id)
    data = json.loads(path.read_text())
    data["created_at"] = time.time() - 1000
    data["expires_at"] = time.time() - 500
    path.write_text(json.dumps(data))

    with pytest.raises(ApprovalValidationError, match="expired"):
        validate_and_approve(gateway, p.proposal_id, source="cli")


def test_approve_nonexistent_raises(gateway):
    from core.approval.auth import validate_and_approve, ApprovalValidationError
    with pytest.raises(ApprovalValidationError):
        validate_and_approve(gateway, "ZZZZZZ", source="cli")


def test_state_hash_mismatch_raises(gateway):
    from core.approval.auth import validate_and_approve, ApprovalValidationError
    p = gateway.create(
        tool_name="cleanup_system", args={"x": 1}, reason="test",
        proposal_type="tool_execution",
    )
    # Tamper with the proposal args on disk
    path = gateway._proposal_path(p.proposal_id)
    data = json.loads(path.read_text())
    data["args"] = {"x": 999}
    path.write_text(json.dumps(data))

    with pytest.raises(ApprovalValidationError, match="hash"):
        validate_and_approve(gateway, p.proposal_id, source="cli")


def test_precondition_revalidation(gateway):
    from core.approval.auth import revalidate_preconditions
    # Precondition: disk_usage_pct was 87, evaluator says now 72
    preconditions = {"disk_usage_pct": 87}
    # Mock evaluator returns current values
    current_values = {"disk_usage_pct": 72}
    result = revalidate_preconditions(preconditions, current_values, threshold_pct=10)
    assert result.still_valid is False
    assert "disk_usage_pct" in result.changed_keys


def test_precondition_revalidation_still_valid(gateway):
    from core.approval.auth import revalidate_preconditions
    preconditions = {"disk_usage_pct": 87}
    current_values = {"disk_usage_pct": 86}
    result = revalidate_preconditions(preconditions, current_values, threshold_pct=10)
    assert result.still_valid is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_approval_auth.py -v`
Expected: FAIL

- [ ] **Step 3: Implement approval auth**

```python
# core/approval/auth.py
"""Approval validation and authentication.

Two approval paths:
1. CLI: user is SSH'd into the box, no additional auth needed
2. Agent: Chaguli calls approve_proposal tool — Chaguli's Telegram
   allowFrom already gates authentication. We validate proposal state.

Both paths validate:
- Proposal exists and is pending
- Proposal hasn't expired
- State hash hasn't changed (tamper detection)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.approval.gateway import (
    ApprovalGateway,
    Proposal,
    ProposalNotFoundError,
    InvalidTransitionError,
    _compute_state_hash,
)

log = logging.getLogger("approval.auth")


class ApprovalValidationError(Exception):
    pass


@dataclass
class PreconditionResult:
    still_valid: bool
    changed_keys: List[str] = field(default_factory=list)
    details: Dict[str, str] = field(default_factory=dict)


def _validate_proposal(gateway: ApprovalGateway, proposal_id: str) -> Proposal:
    """Common validation for both approve and reject paths."""
    try:
        proposal = gateway.get(proposal_id)
    except ProposalNotFoundError:
        raise ApprovalValidationError(f"Proposal {proposal_id} not found")

    # Check expiry
    if gateway._is_expired(proposal):
        raise ApprovalValidationError(
            f"Proposal {proposal_id} has expired"
        )

    # Check status
    if proposal.status != "pending":
        raise ApprovalValidationError(
            f"Proposal {proposal_id} is {proposal.status}, not pending"
        )

    # Verify state hash (tamper detection)
    expected_hash = _compute_state_hash(
        proposal.tool_name, proposal.args, proposal.preconditions,
    )
    if proposal.state_hash and proposal.state_hash != expected_hash:
        raise ApprovalValidationError(
            f"Proposal {proposal_id} state hash mismatch — proposal may have been tampered with"
        )

    return proposal


def validate_and_approve(
    gateway: ApprovalGateway,
    proposal_id: str,
    source: str = "cli",
) -> Proposal:
    """Validate and approve a proposal.

    Args:
        gateway: The approval gateway instance
        proposal_id: The proposal to approve
        source: Who is approving ("cli" or "agent:<name>")
    """
    _validate_proposal(gateway, proposal_id)
    log.info("Approval validated for %s via %s", proposal_id, source)

    try:
        return gateway.approve(proposal_id)
    except InvalidTransitionError as e:
        raise ApprovalValidationError(str(e)) from e


def validate_and_reject(
    gateway: ApprovalGateway,
    proposal_id: str,
    reason: str = "",
    source: str = "cli",
) -> Proposal:
    """Validate and reject a proposal.

    Args:
        gateway: The approval gateway instance
        proposal_id: The proposal to reject
        reason: Why the proposal was rejected
        source: Who is rejecting ("cli" or "agent:<name>")
    """
    _validate_proposal(gateway, proposal_id)
    log.info("Rejection validated for %s via %s: %s", proposal_id, source, reason)

    try:
        return gateway.reject(proposal_id, reason=reason)
    except InvalidTransitionError as e:
        raise ApprovalValidationError(str(e)) from e


def revalidate_preconditions(
    preconditions: Dict,
    current_values: Dict,
    threshold_pct: float = 10,
) -> PreconditionResult:
    """Revalidate preconditions before executing an approved proposal.

    Compares original precondition values against current measurements.
    If any numeric value has changed by more than threshold_pct, the
    preconditions are considered no longer valid.

    For non-numeric values, exact match is required.
    """
    changed_keys = []
    details = {}

    for key, original in preconditions.items():
        if key not in current_values:
            # Can't validate — treat as changed
            changed_keys.append(key)
            details[key] = f"Key {key!r} not present in current values"
            continue

        current = current_values[key]

        if isinstance(original, (int, float)) and isinstance(current, (int, float)):
            if original == 0:
                if current != 0:
                    changed_keys.append(key)
                    details[key] = f"{key}: was {original}, now {current}"
            else:
                pct_change = abs(current - original) / abs(original) * 100
                if pct_change > threshold_pct:
                    changed_keys.append(key)
                    details[key] = f"{key}: was {original}, now {current} ({pct_change:.1f}% change)"
        else:
            if original != current:
                changed_keys.append(key)
                details[key] = f"{key}: was {original!r}, now {current!r}"

    return PreconditionResult(
        still_valid=len(changed_keys) == 0,
        changed_keys=changed_keys,
        details=details,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_approval_auth.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add core/approval/auth.py tests/test_approval_auth.py
git commit -m "Phase C Task 3: approval authentication with hash validation and precondition revalidation"
```

---

## Task 4: Direct Runner — Host Execution with Timeout

**Files:**
- Create: `core/sandbox/__init__.py`
- Create: `core/sandbox/direct.py`
- Test: `tests/test_sandbox_direct.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sandbox_direct.py
from __future__ import annotations
import os
import stat
import pytest


@pytest.fixture
def scripts_dir(tmp_path):
    d = tmp_path / "scripts"
    d.mkdir()
    return d


@pytest.fixture
def reports_dir(tmp_path):
    d = tmp_path / "reports"
    d.mkdir()
    return d


def _make_script(scripts_dir, name, content):
    """Create an executable script in the scripts directory."""
    script = scripts_dir / name
    script.write_text(content)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_direct_run_success(scripts_dir, reports_dir):
    from core.sandbox.direct import DirectRunner
    _make_script(scripts_dir, "hello.sh", "#!/bin/bash\necho 'hello world'")
    runner = DirectRunner()
    result = runner.run(
        script=str(scripts_dir / "hello.sh"),
        args=[],
        timeout=10,
    )
    assert result.success is True
    assert result.exit_code == 0
    assert "hello world" in result.stdout


def test_direct_run_captures_stderr(scripts_dir, reports_dir):
    from core.sandbox.direct import DirectRunner
    _make_script(scripts_dir, "warn.sh", "#!/bin/bash\necho 'warning' >&2\nexit 0")
    runner = DirectRunner()
    result = runner.run(
        script=str(scripts_dir / "warn.sh"),
        args=[],
        timeout=10,
    )
    assert result.success is True
    assert "warning" in result.stderr


def test_direct_run_nonzero_exit(scripts_dir):
    from core.sandbox.direct import DirectRunner
    _make_script(scripts_dir, "fail.sh", "#!/bin/bash\nexit 42")
    runner = DirectRunner()
    result = runner.run(
        script=str(scripts_dir / "fail.sh"),
        args=[],
        timeout=10,
    )
    assert result.success is False
    assert result.exit_code == 42


def test_direct_run_timeout(scripts_dir):
    from core.sandbox.direct import DirectRunner
    _make_script(scripts_dir, "slow.sh", "#!/bin/bash\nsleep 60")
    runner = DirectRunner()
    result = runner.run(
        script=str(scripts_dir / "slow.sh"),
        args=[],
        timeout=1,
    )
    assert result.success is False
    assert result.timed_out is True


def test_direct_run_passes_args(scripts_dir):
    from core.sandbox.direct import DirectRunner
    _make_script(scripts_dir, "args.sh", '#!/bin/bash\necho "arg1=$1 arg2=$2"')
    runner = DirectRunner()
    result = runner.run(
        script=str(scripts_dir / "args.sh"),
        args=["hello", "world"],
        timeout=10,
    )
    assert "arg1=hello arg2=world" in result.stdout


def test_direct_run_passes_env(scripts_dir):
    from core.sandbox.direct import DirectRunner
    _make_script(scripts_dir, "env.sh", '#!/bin/bash\necho "VAR=$MY_VAR"')
    runner = DirectRunner()
    result = runner.run(
        script=str(scripts_dir / "env.sh"),
        args=[],
        timeout=10,
        env={"MY_VAR": "test_value"},
    )
    assert "VAR=test_value" in result.stdout


def test_run_result_duration(scripts_dir):
    from core.sandbox.direct import DirectRunner
    _make_script(scripts_dir, "fast.sh", "#!/bin/bash\ntrue")
    runner = DirectRunner()
    result = runner.run(
        script=str(scripts_dir / "fast.sh"),
        args=[],
        timeout=10,
    )
    assert result.duration_ms >= 0
    assert result.duration_ms < 5000  # Should be fast
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_sandbox_direct.py -v`
Expected: FAIL

- [ ] **Step 3: Implement direct runner**

```python
# core/sandbox/__init__.py
"""Sandbox execution — direct and containerized tool runners."""

# core/sandbox/direct.py
"""Direct (host) execution runner for trusted scripts.

Runs scripts on the host with:
- Configurable timeout (kills on exceed)
- Stdout/stderr capture
- Duration tracking
- Environment variable passthrough

Used for shipped bundle scripts that we wrote and trust.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

log = logging.getLogger("sandbox.direct")

DEFAULT_TIMEOUT = 300  # seconds


@dataclass
class RunResult:
    """Result of a tool execution."""
    success: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_ms: int = 0
    sandbox_mode: str = "direct"


class DirectRunner:
    """Execute scripts directly on the host.

    Trusted scripts only. Enforces timeout but no other isolation.
    """

    def run(
        self,
        script: str,
        args: List[str],
        timeout: int = DEFAULT_TIMEOUT,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> RunResult:
        """Run a script directly on the host.

        Args:
            script: Absolute path to the script
            args: Arguments to pass to the script
            timeout: Maximum execution time in seconds
            env: Additional environment variables (merged with current env)
            cwd: Working directory for the script
        """
        cmd = [script] + args
        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=run_env,
                cwd=cwd,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            return RunResult(
                success=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - start) * 1000)
            log.warning("Script %s timed out after %ds", script, timeout)
            return RunResult(
                success=False,
                exit_code=-1,
                stderr=f"Timed out after {timeout}s",
                timed_out=True,
                duration_ms=duration_ms,
            )
        except OSError as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            log.error("Failed to execute %s: %s", script, e)
            return RunResult(
                success=False,
                exit_code=-1,
                stderr=str(e),
                duration_ms=duration_ms,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_sandbox_direct.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add core/sandbox/__init__.py core/sandbox/direct.py tests/test_sandbox_direct.py
git commit -m "Phase C Task 4: direct runner with timeout, capture, and env passthrough"
```

---

## Task 5: Docker Sandbox Runner

**Files:**
- Create: `core/sandbox/docker_sandbox.py`
- Test: `tests/test_sandbox_docker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sandbox_docker.py
from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock


def test_build_command_default_isolation():
    from core.sandbox.docker_sandbox import ContainerRunner
    runner = ContainerRunner(
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
    )
    cmd = runner._build_command("check.sh", args=[])
    joined = " ".join(cmd)
    assert "docker" in joined
    assert "--rm" in cmd
    assert "--network=none" in cmd
    assert any("--memory=" in c for c in cmd)
    assert any("/opt/scripts" in c and ":ro" in c for c in cmd)
    assert any("/opt/reports" in c and ":rw" in c for c in cmd)


def test_build_command_network_opt_in():
    from core.sandbox.docker_sandbox import ContainerRunner
    runner = ContainerRunner(
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
    )
    cmd = runner._build_command("check.sh", args=[], allow_network=True)
    assert "--network=none" not in cmd
    assert "--network=bridge" in cmd


def test_build_command_custom_resources():
    from core.sandbox.docker_sandbox import ContainerRunner
    runner = ContainerRunner(
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
    )
    cmd = runner._build_command(
        "heavy.sh", args=[], memory="1g", cpus="2",
    )
    assert "--memory=1g" in cmd
    assert "--cpus=2" in cmd


def test_build_command_includes_script_args():
    from core.sandbox.docker_sandbox import ContainerRunner
    runner = ContainerRunner(
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
    )
    cmd = runner._build_command("check.sh", args=["--verbose", "--dry-run"])
    # Script and args should be at the end
    assert cmd[-3] == "/scripts/check.sh"
    assert cmd[-2] == "--verbose"
    assert cmd[-1] == "--dry-run"


def test_build_command_no_docker_socket():
    from core.sandbox.docker_sandbox import ContainerRunner
    runner = ContainerRunner(
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
    )
    cmd = runner._build_command("check.sh", args=[])
    joined = " ".join(cmd)
    assert "docker.sock" not in joined


def test_build_command_no_env_leak():
    from core.sandbox.docker_sandbox import ContainerRunner
    runner = ContainerRunner(
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
    )
    cmd = runner._build_command("check.sh", args=[])
    joined = " ".join(cmd)
    assert "--env-file" not in joined
    assert "GROQ_API_KEY" not in joined


@patch("core.sandbox.docker_sandbox.subprocess")
def test_run_success(mock_subprocess):
    from core.sandbox.docker_sandbox import ContainerRunner
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "output"
    mock_proc.stderr = ""
    mock_subprocess.run.return_value = mock_proc

    runner = ContainerRunner(
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
    )
    result = runner.run(script="check.sh", args=[])
    assert result.success is True
    assert result.sandbox_mode == "containerized"
    assert result.stdout == "output"


@patch("core.sandbox.docker_sandbox.subprocess")
def test_run_timeout(mock_subprocess):
    from core.sandbox.docker_sandbox import ContainerRunner
    import subprocess as real_subprocess
    mock_subprocess.run.side_effect = real_subprocess.TimeoutExpired(
        cmd=["docker"], timeout=10,
    )
    mock_subprocess.TimeoutExpired = real_subprocess.TimeoutExpired

    runner = ContainerRunner(
        scripts_dir="/opt/scripts",
        reports_dir="/opt/reports",
    )
    result = runner.run(script="check.sh", args=[], timeout=10)
    assert result.success is False
    assert result.timed_out is True
    assert result.sandbox_mode == "containerized"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_sandbox_docker.py -v`
Expected: FAIL

- [ ] **Step 3: Implement container runner**

```python
# core/sandbox/docker_sandbox.py
"""Containerized execution runner for untrusted scripts.

Runs scripts in ephemeral Docker containers with:
- No Docker socket access
- No host environment leaks
- Network disabled by default
- Memory and CPU limits
- Scripts mounted read-only
- Reports mounted read-write
- Auto-removed after execution

Uses the container_policy module from Phase A for argument generation,
but adds the execution layer on top.
"""
from __future__ import annotations

import logging
import subprocess
import time
from typing import Dict, List, Optional

from core.sandbox.direct import RunResult

log = logging.getLogger("sandbox.docker")

DEFAULT_IMAGE = "agentharness/sandbox:latest"
DEFAULT_MEMORY = "512m"
DEFAULT_CPUS = "1"
DEFAULT_TIMEOUT = 300


class ContainerRunner:
    """Execute scripts in ephemeral Docker containers.

    For community bundles and untrusted code. Full isolation.
    """

    def __init__(
        self,
        scripts_dir: str,
        reports_dir: str,
        image: str = DEFAULT_IMAGE,
    ):
        self.scripts_dir = scripts_dir
        self.reports_dir = reports_dir
        self.image = image

    def _build_command(
        self,
        script: str,
        args: List[str],
        allow_network: bool = False,
        memory: str = DEFAULT_MEMORY,
        cpus: str = DEFAULT_CPUS,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        """Build the docker run command with proper isolation.

        Returns a list of arguments for subprocess (not a shell string).
        """
        cmd = [
            "docker", "run",
            "--rm",
            f"--memory={memory}",
            f"--cpus={cpus}",
            "--pids-limit=256",
            "--read-only",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=100m",
        ]

        # Network isolation
        if allow_network:
            cmd.append("--network=bridge")
        else:
            cmd.append("--network=none")

        # Mount scripts read-only, reports read-write
        cmd.extend([
            "-v", f"{self.scripts_dir}:/scripts:ro",
            "-v", f"{self.reports_dir}:/reports:rw",
        ])

        # Explicit env vars only (no host env leak)
        if extra_env:
            for key, value in extra_env.items():
                cmd.extend(["-e", f"{key}={value}"])

        # Image and script
        cmd.append(self.image)
        cmd.append(f"/scripts/{script}")
        cmd.extend(args)

        return cmd

    def run(
        self,
        script: str,
        args: List[str],
        timeout: int = DEFAULT_TIMEOUT,
        allow_network: bool = False,
        memory: str = DEFAULT_MEMORY,
        cpus: str = DEFAULT_CPUS,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> RunResult:
        """Run a script in an ephemeral Docker container.

        Args:
            script: Script filename (relative to scripts_dir)
            args: Arguments to pass to the script
            timeout: Maximum execution time in seconds
            allow_network: Allow network access (default: disabled)
            memory: Memory limit (e.g., "512m", "1g")
            cpus: CPU limit (e.g., "1", "2")
            extra_env: Explicit environment variables to pass in
        """
        cmd = self._build_command(
            script, args,
            allow_network=allow_network,
            memory=memory,
            cpus=cpus,
            extra_env=extra_env,
        )

        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            return RunResult(
                success=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=duration_ms,
                sandbox_mode="containerized",
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - start) * 1000)
            log.warning("Container script %s timed out after %ds", script, timeout)
            return RunResult(
                success=False,
                exit_code=-1,
                stderr=f"Container timed out after {timeout}s",
                timed_out=True,
                duration_ms=duration_ms,
                sandbox_mode="containerized",
            )
        except OSError as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            log.error("Failed to run container for %s: %s", script, e)
            return RunResult(
                success=False,
                exit_code=-1,
                stderr=str(e),
                duration_ms=duration_ms,
                sandbox_mode="containerized",
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_sandbox_docker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add core/sandbox/docker_sandbox.py tests/test_sandbox_docker.py
git commit -m "Phase C Task 5: containerized Docker runner with full isolation"
```

---

## Task 6: Sandbox Dispatcher — Unified Runner

**Files:**
- Create: `core/sandbox/runner.py`
- Test: `tests/test_sandbox_runner.py`

The `SandboxRunner` reads the tool's configured sandbox mode and dispatches to either `DirectRunner` or `ContainerRunner`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sandbox_runner.py
from __future__ import annotations
import os
import stat
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def scripts_dir(tmp_path):
    d = tmp_path / "scripts"
    d.mkdir()
    return d


@pytest.fixture
def reports_dir(tmp_path):
    d = tmp_path / "reports"
    d.mkdir()
    return d


def _make_script(scripts_dir, name, content):
    script = scripts_dir / name
    script.write_text(content)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_dispatch_direct(scripts_dir, reports_dir):
    from core.sandbox.runner import SandboxRunner
    _make_script(scripts_dir, "check.sh", "#!/bin/bash\necho ok")
    runner = SandboxRunner(
        scripts_dir=str(scripts_dir),
        reports_dir=str(reports_dir),
    )
    result = runner.execute(
        script="check.sh",
        args=[],
        sandbox_mode="direct",
    )
    assert result.success is True
    assert result.sandbox_mode == "direct"
    assert "ok" in result.stdout


def test_dispatch_containerized_builds_docker_command(scripts_dir, reports_dir):
    from core.sandbox.runner import SandboxRunner
    runner = SandboxRunner(
        scripts_dir=str(scripts_dir),
        reports_dir=str(reports_dir),
    )
    with patch("core.sandbox.docker_sandbox.subprocess") as mock_sub:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "container output"
        mock_proc.stderr = ""
        mock_sub.run.return_value = mock_proc

        result = runner.execute(
            script="check.sh",
            args=[],
            sandbox_mode="containerized",
        )
        assert result.success is True
        assert result.sandbox_mode == "containerized"
        # Verify docker was called
        mock_sub.run.assert_called_once()
        cmd = mock_sub.run.call_args[0][0]
        assert cmd[0] == "docker"


def test_invalid_sandbox_mode_raises(scripts_dir, reports_dir):
    from core.sandbox.runner import SandboxRunner, InvalidSandboxMode
    runner = SandboxRunner(
        scripts_dir=str(scripts_dir),
        reports_dir=str(reports_dir),
    )
    with pytest.raises(InvalidSandboxMode):
        runner.execute(script="x.sh", args=[], sandbox_mode="guarded")


def test_direct_uses_full_path(scripts_dir, reports_dir):
    from core.sandbox.runner import SandboxRunner
    _make_script(scripts_dir, "test.sh", "#!/bin/bash\necho ok")
    runner = SandboxRunner(
        scripts_dir=str(scripts_dir),
        reports_dir=str(reports_dir),
    )
    result = runner.execute(
        script="test.sh",
        args=[],
        sandbox_mode="direct",
    )
    assert result.success is True


def test_timeout_passthrough(scripts_dir, reports_dir):
    from core.sandbox.runner import SandboxRunner
    _make_script(scripts_dir, "slow.sh", "#!/bin/bash\nsleep 60")
    runner = SandboxRunner(
        scripts_dir=str(scripts_dir),
        reports_dir=str(reports_dir),
    )
    result = runner.execute(
        script="slow.sh",
        args=[],
        sandbox_mode="direct",
        timeout=1,
    )
    assert result.timed_out is True


def test_env_passthrough_direct(scripts_dir, reports_dir):
    from core.sandbox.runner import SandboxRunner
    _make_script(scripts_dir, "env.sh", '#!/bin/bash\necho "V=$MY_V"')
    runner = SandboxRunner(
        scripts_dir=str(scripts_dir),
        reports_dir=str(reports_dir),
    )
    result = runner.execute(
        script="env.sh",
        args=[],
        sandbox_mode="direct",
        env={"MY_V": "42"},
    )
    assert "V=42" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_sandbox_runner.py -v`
Expected: FAIL

- [ ] **Step 3: Implement sandbox dispatcher**

```python
# core/sandbox/runner.py
"""Unified sandbox runner — dispatches to direct or containerized execution.

Reads the tool's configured sandbox_mode and routes execution to the
appropriate runner. Only two modes exist:
- direct: host execution for trusted shipped bundles
- containerized: Docker execution for community/untrusted code

The old "guarded" mode (regex-blocking) was dropped as a fake sandbox.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from core.sandbox.direct import DirectRunner, RunResult
from core.sandbox.docker_sandbox import ContainerRunner

log = logging.getLogger("sandbox.runner")

VALID_MODES = {"direct", "containerized"}


class InvalidSandboxMode(Exception):
    pass


class SandboxRunner:
    """Dispatch tool execution to the correct sandbox mode.

    Usage:
        runner = SandboxRunner(scripts_dir="/opt/ah/scripts", reports_dir="/opt/ah/reports")
        result = runner.execute(script="check.sh", args=[], sandbox_mode="direct")
    """

    def __init__(
        self,
        scripts_dir: str,
        reports_dir: str,
        docker_image: str = "agentharness/sandbox:latest",
    ):
        self.scripts_dir = Path(scripts_dir)
        self.reports_dir = Path(reports_dir)
        self._direct = DirectRunner()
        self._container = ContainerRunner(
            scripts_dir=str(self.scripts_dir),
            reports_dir=str(self.reports_dir),
            image=docker_image,
        )

    def execute(
        self,
        script: str,
        args: List[str],
        sandbox_mode: str,
        timeout: int = 300,
        env: Optional[Dict[str, str]] = None,
        allow_network: bool = False,
        memory: str = "512m",
        cpus: str = "1",
    ) -> RunResult:
        """Execute a tool script in the configured sandbox mode.

        Args:
            script: Script filename (relative to scripts_dir)
            args: Arguments to pass to the script
            sandbox_mode: "direct" or "containerized"
            timeout: Maximum execution time in seconds
            env: Environment variables to pass through
            allow_network: (containerized only) Allow network access
            memory: (containerized only) Memory limit
            cpus: (containerized only) CPU limit
        """
        if sandbox_mode not in VALID_MODES:
            raise InvalidSandboxMode(
                f"Invalid sandbox mode {sandbox_mode!r}. Valid modes: {VALID_MODES}"
            )

        log.info(
            "Executing %s in %s mode (timeout=%ds)",
            script, sandbox_mode, timeout,
        )

        if sandbox_mode == "direct":
            script_path = str(self.scripts_dir / script)
            return self._direct.run(
                script=script_path,
                args=args,
                timeout=timeout,
                env=env,
            )
        else:
            return self._container.run(
                script=script,
                args=args,
                timeout=timeout,
                allow_network=allow_network,
                memory=memory,
                cpus=cpus,
                extra_env=env,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_sandbox_runner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add core/sandbox/runner.py tests/test_sandbox_runner.py
git commit -m "Phase C Task 6: unified sandbox dispatcher routing to direct or containerized"
```

---

## Task 7: Agent Bridge — Abstract Base Interface

**Files:**
- Create: `core/agents/__init__.py`
- Create: `core/agents/base.py`
- Test: `tests/test_agents_base.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_agents_base.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_agents_base.py -v`
Expected: FAIL

- [ ] **Step 3: Implement abstract bridge**

```python
# core/agents/__init__.py
"""Agent integration layer — bridge interfaces and implementations."""

# core/agents/base.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_agents_base.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add core/agents/__init__.py core/agents/base.py tests/test_agents_base.py
git commit -m "Phase C Task 7: abstract agent bridge interface with data types"
```

---

## Task 8: Chaguli Bridge — Reference Implementation

**Files:**
- Create: `core/agents/chaguli.py`
- Test: `tests/test_agents_chaguli.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_agents_chaguli.py
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

    files = list((tmp_path := bridge_dirs["briefings_dir"],) and
                 __import__("pathlib").Path(bridge_dirs["briefings_dir"]).glob("*.json"))
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_agents_chaguli.py -v`
Expected: FAIL

- [ ] **Step 3: Implement Chaguli bridge**

```python
# core/agents/chaguli.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_agents_chaguli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add core/agents/chaguli.py tests/test_agents_chaguli.py
git commit -m "Phase C Task 8: Chaguli bridge with file-based communication and capability probing"
```

---

## Task 9: CLI Commands — proposals, approve, reject

**Files:**
- Modify: `cli.py`
- (No separate test file — CLI tested via subprocess in the implementation step)

This task adds three CLI commands:
- `agentharness proposals` — list pending proposals
- `agentharness approve <id>` — approve a proposal
- `agentharness reject <id> --reason "..."` — reject a proposal

- [ ] **Step 1: Read existing cli.py to understand current structure**

Run: `cat cli.py` (in project root)

- [ ] **Step 2: Add approval CLI commands**

Append to `cli.py` (fitting the existing argument parser pattern):

```python
# Add to cli.py — approval gateway commands
# (Integrate into the existing argparse subcommands)

# --- Add these imports at the top ---
# from core.approval.gateway import ApprovalGateway
# from core.approval.auth import validate_and_approve, validate_and_reject, ApprovalValidationError

def cmd_proposals(args):
    """List pending proposals."""
    from core.approval.gateway import ApprovalGateway
    from core.discovery.state import StateManager

    sm = StateManager()
    proposals_dir = sm.resolve("proposals_dir", "proposals")

    gw = ApprovalGateway(proposals_dir=proposals_dir)
    pending = gw.list_pending()

    if not pending:
        print("No pending proposals.")
        return 0

    print(f"{'ID':<8} {'Tool':<25} {'Type':<20} {'Reason'}")
    print("-" * 80)
    for p in pending:
        print(f"{p.proposal_id:<8} {p.tool_name:<25} {p.proposal_type:<20} {p.reason}")

    print(f"\n{len(pending)} pending proposal(s)")
    return 0


def cmd_approve(args):
    """Approve a pending proposal."""
    from core.approval.gateway import ApprovalGateway
    from core.approval.auth import validate_and_approve, ApprovalValidationError
    from core.discovery.state import StateManager

    sm = StateManager()
    proposals_dir = sm.resolve("proposals_dir", "proposals")
    gw = ApprovalGateway(proposals_dir=proposals_dir)

    try:
        proposal = validate_and_approve(gw, args.proposal_id, source="cli")
        print(f"Approved proposal {proposal.proposal_id} ({proposal.tool_name})")
        print(f"Will execute in next scheduler tick.")
        return 0
    except ApprovalValidationError as e:
        print(f"Error: {e}", file=__import__("sys").stderr)
        return 1


def cmd_reject(args):
    """Reject a pending proposal."""
    from core.approval.gateway import ApprovalGateway
    from core.approval.auth import validate_and_reject, ApprovalValidationError
    from core.discovery.state import StateManager

    sm = StateManager()
    proposals_dir = sm.resolve("proposals_dir", "proposals")
    gw = ApprovalGateway(proposals_dir=proposals_dir)

    reason = args.reason or ""
    try:
        proposal = validate_and_reject(
            gw, args.proposal_id, reason=reason, source="cli",
        )
        print(f"Rejected proposal {proposal.proposal_id} ({proposal.tool_name})")
        if reason:
            print(f"Reason: {reason}")
        return 0
    except ApprovalValidationError as e:
        print(f"Error: {e}", file=__import__("sys").stderr)
        return 1


# --- Register subcommands (add to existing argparse setup) ---
# proposals_parser = subparsers.add_parser("proposals", help="List pending proposals")
# proposals_parser.set_defaults(func=cmd_proposals)
#
# approve_parser = subparsers.add_parser("approve", help="Approve a proposal")
# approve_parser.add_argument("proposal_id", help="Proposal ID to approve")
# approve_parser.set_defaults(func=cmd_approve)
#
# reject_parser = subparsers.add_parser("reject", help="Reject a proposal")
# reject_parser.add_argument("proposal_id", help="Proposal ID to reject")
# reject_parser.add_argument("--reason", "-r", default="", help="Rejection reason")
# reject_parser.set_defaults(func=cmd_reject)
```

- [ ] **Step 3: Verify CLI commands parse correctly**

Run: `python3 cli.py proposals --help`
Run: `python3 cli.py approve --help`
Run: `python3 cli.py reject --help`
Expected: Help text displayed without errors

- [ ] **Step 4: Commit**

```
git add cli.py
git commit -m "Phase C Task 9: CLI commands for proposals, approve, reject"
```

---

## Task 10: Integration Tests + Final Validation

**Files:**
- Test all modules together

- [ ] **Step 1: Run full Phase C test suite**

Run: `python3 -m pytest tests/test_approval_policies.py tests/test_approval_gateway.py tests/test_approval_auth.py tests/test_sandbox_direct.py tests/test_sandbox_docker.py tests/test_sandbox_runner.py tests/test_agents_base.py tests/test_agents_chaguli.py -v`
Expected: ALL PASS

- [ ] **Step 2: Verify import chain works**

```bash
python3 -c "
from core.approval.policies import ApprovalTier, resolve_tier
from core.approval.gateway import ApprovalGateway, Proposal
from core.approval.auth import validate_and_approve, revalidate_preconditions
from core.sandbox.direct import DirectRunner
from core.sandbox.docker_sandbox import ContainerRunner
from core.sandbox.runner import SandboxRunner
from core.agents.base import AgentBridge, Briefing, Insight, ToolUpdate, CapabilityReport
from core.agents.chaguli import ChaguliBridge
print('All Phase C imports successful')
"
```
Expected: "All Phase C imports successful"

- [ ] **Step 3: Run integration scenario**

```bash
python3 -c "
import tempfile, os, json
from pathlib import Path

# Setup
tmpdir = tempfile.mkdtemp()
proposals_dir = os.path.join(tmpdir, 'proposals')
briefings_dir = os.path.join(tmpdir, 'briefings')
insights_dir = os.path.join(tmpdir, 'insights')
updates_dir = os.path.join(tmpdir, 'updates')

# 1. Create a proposal
from core.approval.gateway import ApprovalGateway
from core.approval.policies import resolve_tier
from core.approval.auth import validate_and_approve

gw = ApprovalGateway(proposals_dir=proposals_dir)
tier = resolve_tier('cleanup_system')
assert tier.value == 'approve'

proposal = gw.create(
    tool_name='cleanup_system',
    args={'max_age_days': 30},
    reason='Disk at 87%',
    proposal_type='tool_execution',
    preconditions={'disk_usage_pct': 87},
)
print(f'Created proposal {proposal.proposal_id}')

# 2. List pending
pending = gw.list_pending()
assert len(pending) == 1

# 3. Approve it
approved = validate_and_approve(gw, proposal.proposal_id, source='cli')
assert approved.status == 'approved'
print(f'Approved proposal {proposal.proposal_id}')

# 4. Send briefing via Chaguli bridge
from core.agents.chaguli import ChaguliBridge
from core.agents.base import Briefing

bridge = ChaguliBridge(
    briefings_dir=briefings_dir,
    insights_dir=insights_dir,
    tool_updates_dir=updates_dir,
)
bridge.send_briefing(Briefing(
    date='2026-04-07',
    summary='Disk cleanup approved and queued',
    proposals_pending=0,
))
print('Sent briefing via bridge')

# 5. Generate capability report
report = bridge.generate_capability_report()
assert report.agent == 'chaguli'
print(f'Capability report: {report.tools_integration}')

print('Integration scenario passed!')
"
```
Expected: "Integration scenario passed!"

- [ ] **Step 4: Final commit**

```
git add -A
git commit -m "Phase C complete: HITL approval gateway, sandbox runner, agent bridge"
```

---

## Summary

**Phase C delivers:**
- HITL Approval Gateway
  - Tier policies (auto/notify/approve) with pattern-based resolution
  - Proposal lifecycle (create/approve/reject/expire/stale) with JSON persistence
  - Approval authentication with state hash validation and precondition revalidation
- Sandbox Execution
  - Direct runner (host execution with timeout, env, capture)
  - Container runner (Docker isolation with network-off, memory/CPU limits, no env leak)
  - Unified dispatcher routing to direct or containerized based on tool config
- Agent Bridge
  - Abstract base interface with Briefing, Insight, ToolUpdate, CapabilityReport data types
  - Chaguli reference implementation with file-based communication (briefings/, insights_inbox/, tool_updates/)
  - Capability probing and report generation
- CLI commands: `agentharness proposals`, `agentharness approve <id>`, `agentharness reject <id> --reason`

**Phase C does NOT include** (deferred to later phases):
- Distiller / synthesizer / scout (Phase D)
- Preference model learning from rejection patterns (Phase D)
- Optimization scout (Phase D)
- Dashboard (Phase D)

**Estimated tasks:** 10 tasks, ~40 steps
**Test coverage:** ~50 new tests across 8 test files
