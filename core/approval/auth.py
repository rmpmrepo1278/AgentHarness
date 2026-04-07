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
