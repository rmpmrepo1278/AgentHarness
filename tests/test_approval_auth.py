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
