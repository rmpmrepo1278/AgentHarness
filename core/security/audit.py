"""Exec audit trail — JSONL log of every tool execution with secret redaction."""
from __future__ import annotations

import getpass
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

MAX_OUTPUT_LENGTH: int = 2000

# Patterns that look like secrets in free text:
# - sk-<8+ alphanum>
# - key/token/password/secret/api_key followed by separator then 8+ alphanum
# - any standalone 32+ character alphanumeric string (likely a token/hash)
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"(?:api_key|token|password|secret)[=:\s]+[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{32,}(?![A-Za-z0-9])"),
]

# Keys whose values should always be redacted in args dicts.
_SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "api_key", "token", "password", "secret", "key", "credential",
)


class AuditLogger:
    """Append-only JSONL audit logger for tool executions."""

    def __init__(self, log_dir: str) -> None:
        self.log_dir = log_dir
        self.log_file = os.path.join(log_dir, "exec_audit.jsonl")

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def log_execution(
        self,
        tool: str,
        trigger: str,
        args: dict[str, object],
        exit_code: int,
        output: str,
        approval_id: str | None = None,
        sandbox_mode: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Append a single audit entry to the JSONL log."""
        now = datetime.now(timezone.utc)
        entry: dict[str, object] = {
            "timestamp": now.isoformat(),
            "epoch": now.timestamp(),
            "tool": tool,
            "trigger": trigger,
            "args": self._redact_args(args),
            "exit_code": exit_code,
            "output": self._redact(self._truncate(output)),
            "approval_id": approval_id,
            "sandbox_mode": sandbox_mode,
            "duration_ms": duration_ms,
            "pid": os.getpid(),
            "user": getpass.getuser(),
        }
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)
        with open(self.log_file, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _redact(text: str) -> str:
        """Regex-replace secret-looking patterns in free text."""
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub("***REDACTED***", text)
        return text

    @staticmethod
    def _redact_args(args: dict[str, object]) -> dict[str, object]:
        """Return a copy of *args* with sensitive values redacted."""
        redacted: dict[str, object] = {}
        for key, value in args.items():
            if any(frag in key.lower() for frag in _SENSITIVE_KEY_FRAGMENTS):
                redacted[key] = "***REDACTED***"
            elif isinstance(value, str):
                redacted[key] = AuditLogger._redact(value)
            else:
                redacted[key] = value
        return redacted

    @staticmethod
    def _truncate(text: str) -> str:
        """Truncate text exceeding MAX_OUTPUT_LENGTH."""
        if len(text) > MAX_OUTPUT_LENGTH:
            return text[:MAX_OUTPUT_LENGTH] + "...[truncated]"
        return text
