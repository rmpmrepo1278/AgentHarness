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
