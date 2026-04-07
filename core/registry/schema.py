"""Schema validation for registry entries (checks, tools, harnesses)."""
from __future__ import annotations

import re
from typing import Any, Dict, List

VALID_CHECK_TYPES = frozenset(
    {"threshold", "command_exit", "command_output", "regex_match", "http_probe"}
)
VALID_APPROVAL_TIERS = frozenset({"auto", "notify", "approve"})
VALID_SANDBOX_MODES = frozenset({"direct", "containerized"})
VALID_WINDOWS = frozenset({"online", "offline", "offline_lan", "any"})

_FREQUENCY_RE = re.compile(r"^(\d+[mhd]|daily|weekly|monthly|on_boot)$")


def validate_check(name: str, check: Dict[str, Any]) -> List[str]:
    """Validate a check definition. Returns a list of error strings (empty = valid)."""
    errors: List[str] = []

    if "command" not in check:
        errors.append(f"{name}: 'command' is required")

    ctype = check.get("type")
    if ctype is not None and ctype not in VALID_CHECK_TYPES:
        errors.append(
            f"{name}: invalid type '{ctype}', must be one of {sorted(VALID_CHECK_TYPES)}"
        )

    if ctype == "threshold" and "warn" not in check and "critical" not in check:
        errors.append(f"{name}: threshold checks require 'warn' or 'critical'")

    return errors


def validate_tool(name: str, tool: Dict[str, Any]) -> List[str]:
    """Validate a tool definition. Returns a list of error strings (empty = valid)."""
    errors: List[str] = []

    if "description" not in tool:
        errors.append(f"{name}: 'description' is required")

    tier = tool.get("approval_tier")
    if tier is not None and tier not in VALID_APPROVAL_TIERS:
        errors.append(
            f"{name}: invalid approval_tier '{tier}', must be one of {sorted(VALID_APPROVAL_TIERS)}"
        )

    mode = tool.get("sandbox_mode")
    if mode is not None and mode not in VALID_SANDBOX_MODES:
        errors.append(
            f"{name}: invalid sandbox_mode '{mode}', must be one of {sorted(VALID_SANDBOX_MODES)}"
        )

    return errors


def validate_harness(name: str, harness: Dict[str, Any]) -> List[str]:
    """Validate a harness definition. Returns a list of error strings (empty = valid)."""
    errors: List[str] = []

    if "script" not in harness:
        errors.append(f"{name}: 'script' is required")

    freq = harness.get("frequency")
    if freq is not None and not _FREQUENCY_RE.match(str(freq)):
        errors.append(
            f"{name}: invalid frequency '{freq}', must match pattern "
            "'<N>m|<N>h|<N>d|daily|weekly|monthly|on_boot'"
        )

    window = harness.get("window")
    if window is not None and window not in VALID_WINDOWS:
        errors.append(
            f"{name}: invalid window '{window}', must be one of {sorted(VALID_WINDOWS)}"
        )

    return errors
