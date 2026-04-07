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
