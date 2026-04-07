"""Collect error context from logs, selftest, state — compress for LLM context window."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.resilience.atomic_json import safe_read_json

log = logging.getLogger(__name__)

ERROR_PATTERNS = [
    "ERROR",
    "FAIL",
    "error:",
    "failed:",
    "Permission denied",
    "command not found",
    "timed out",
    "Connection refused",
]


class DiagnosticCollector:
    """Collect and compress diagnostic context for LLM analysis."""

    def __init__(self, data_dir: str, max_chars: int = 12000):
        self.data_dir = Path(data_dir)
        self.max_chars = max_chars

    def collect(self) -> dict:
        """Gather diagnostic context from all sources."""
        context: dict = {}

        # 1. Selftest results
        selftest = safe_read_json(self.data_dir / "selftest_result.json", default={})
        if selftest:
            context["selftest"] = {
                "overall": selftest.get("overall", "unknown"),
                "failures": [
                    c
                    for c in selftest.get("checks", [])
                    if c.get("status") == "fail"
                ],
            }

        # 2. Recent errors from logs + selftest failure messages
        errors = self._extract_errors()
        for fail in context.get("selftest", {}).get("failures", []):
            err_msg = fail.get("error", "")
            if err_msg and err_msg not in errors:
                errors.append(err_msg)
        context["errors"] = errors

        # 3. Hardware summary
        state = safe_read_json(self.data_dir / "state.json", default={})
        context["hardware"] = state.get("hardware", {})
        context["paths"] = state.get("paths", {})

        # 4. Circuit breaker state (which checks are suppressed)
        cb_state = safe_read_json(
            self.data_dir / "circuit_breaker.json", default={}
        )
        open_circuits = [k for k, v in cb_state.items() if v.get("open")]
        if open_circuits:
            context["suppressed_checks"] = open_circuits

        return context

    def _extract_errors(self) -> list:
        """Extract error lines from recent logs."""
        logs_dir = self.data_dir / "logs"
        errors: list = []
        if not logs_dir.is_dir():
            return errors

        for log_file in sorted(logs_dir.glob("*.log"), reverse=True)[:3]:
            try:
                lines = log_file.read_text().splitlines()
                for line in lines[-200:]:  # Last 200 lines
                    if any(pat in line for pat in ERROR_PATTERNS):
                        errors.append(line.strip())
            except OSError:
                continue

        # Deduplicate and limit
        seen: set = set()
        unique: list = []
        for e in errors:
            if e not in seen:
                seen.add(e)
                unique.append(e)
        return unique[:20]  # Max 20 unique errors

    def format_prompt(self, context: dict) -> str:
        """Format context into an LLM prompt for diagnosis."""
        parts: list = []
        parts.append(
            "You are diagnosing issues with an AgentHarness installation."
        )
        parts.append(
            "Analyze the following diagnostic data and suggest specific fixes."
        )
        parts.append("Be concise. Give exact file paths and commands.")
        parts.append("")

        if context.get("selftest"):
            st = context["selftest"]
            parts.append("## Self-Test: %s" % st.get("overall", "?"))
            for f in st.get("failures", []):
                parts.append(
                    "  FAIL: %s — %s"
                    % (f.get("name", "?"), f.get("error", "unknown"))
                )
            parts.append("")

        if context.get("errors"):
            parts.append("## Recent Errors")
            for e in context["errors"][:10]:
                parts.append("  %s" % e)
            parts.append("")

        if context.get("hardware"):
            hw = context["hardware"]
            parts.append(
                "## Hardware: %s, %sGB RAM"
                % (hw.get("cpu_model", "?"), hw.get("total_ram_gb", "?"))
            )
            parts.append("")

        if context.get("suppressed_checks"):
            parts.append(
                "## Suppressed checks: %s"
                % ", ".join(context["suppressed_checks"])
            )
            parts.append("")

        parts.append("## What is wrong and how to fix it?")
        parts.append(
            "For each issue: 1) Root cause 2) Exact fix command or file edit "
            "3) How to verify"
        )

        prompt = "\n".join(parts)

        # Truncate if needed
        if len(prompt) > self.max_chars:
            prompt = (
                prompt[: self.max_chars - 50]
                + "\n\n[context truncated for LLM window]"
            )

        return prompt
