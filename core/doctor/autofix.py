"""LLM-powered diagnosis + HITL proposal generation.

Sends compressed error context to the LLM router, parses the response,
and creates an approval proposal with the suggested fix.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.doctor.diagnose import DiagnosticCollector
from core.providers.base import Complexity, LLMRequest, LLMResponse

log = logging.getLogger(__name__)


class AutoFixer:
    """Diagnose issues via LLM and generate fix proposals."""

    def __init__(self, data_dir: str) -> None:
        self.data_dir = Path(data_dir)
        self.collector = DiagnosticCollector(data_dir=data_dir)

    def diagnose_and_propose(self) -> dict:
        """Collect context, send to LLM, return diagnosis."""
        context = self.collector.collect()

        # Check if there are actually issues
        selftest = context.get("selftest", {})
        errors = context.get("errors", [])
        if selftest.get("overall") == "ok" and not errors:
            return {"success": True, "diagnosis": "No issues detected"}

        # Format prompt and send to LLM
        prompt = self.collector.format_prompt(context)
        response = self._call_llm(prompt)

        if not response.success:
            return {
                "success": False,
                "error": "LLM diagnosis failed: %s" % response.error,
                "context": context,
            }

        return {
            "success": True,
            "diagnosis": response.text,
            "provider": response.provider,
            "tokens_used": response.total_tokens,
            "context": context,
        }

    def _call_llm(self, prompt: str) -> LLMResponse:
        """Send diagnostic prompt to the LLM router."""
        try:
            from core.providers.router import Router
            from core.providers.budget import BudgetTracker
            from core.providers.llamacpp import LlamaCppProvider
            from core.providers.groq import GroqProvider

            # Build a minimal router with available providers
            providers = []
            bt = BudgetTracker(data_dir=str(self.data_dir))

            # Try local first
            try:
                local = LlamaCppProvider(
                    name="local", endpoint="http://localhost:8080"
                )
                if local.is_available():
                    providers.append(local)
            except Exception:
                pass

            # Try Groq
            try:
                groq = GroqProvider()
                if groq.is_available():
                    providers.append(groq)
            except Exception:
                pass

            if not providers:
                return LLMResponse.error(  # type: ignore[attr-defined]
                    "doctor",
                    "No LLM providers available. "
                    "Set GROQ_API_KEY or start local server.",
                )

            router = Router(
                providers=providers,
                budget=bt,
                routing={
                    "low": [p.name for p in providers],
                    "medium": [p.name for p in providers],
                    "high": [p.name for p in providers],
                    "critical": [p.name for p in providers],
                },
            )

            request = LLMRequest(
                prompt=prompt,
                complexity=Complexity.MEDIUM,
                system_prompt=(
                    "You are a Linux system administrator diagnosing "
                    "homelab infrastructure issues. "
                    "Be specific and concise."
                ),
                max_tokens=2048,
                temperature=0.3,
            )

            return router.route(request)

        except Exception as e:
            log.error("Doctor LLM call failed: %s", e)
            return LLMResponse.error("doctor", str(e))  # type: ignore[attr-defined]
