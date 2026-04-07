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
