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
