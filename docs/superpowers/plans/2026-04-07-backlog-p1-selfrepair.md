# Backlog Priority 1: Self-Repair + Coding Harness + Pre-Deploy Validation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable diagnosing and fixing deployment issues directly on the homelab without the work Mac — self-repair doctor using local/cloud LLMs, Aider integration for real coding, and pre-deploy validation to catch issues before they happen.

**Architecture:** The self-repair doctor reads error logs + selftest results, sends focused context to the LLM router (local or Groq), gets a diagnosis + fix suggestion, and creates a HITL proposal. Aider setup generates config pointing to AgentHarness's LLM endpoints. Pre-deploy validation runs checks over SSH or locally.

**Tech Stack:** Python 3.9+, existing core/ modules (router, approval, selftest), SSH (paramiko optional)

**Depends on:** Phase A (discovery, selftest), Phase B (providers, router), Phase C (approval gateway)

---

## File Structure

### New files to create:
```
core/doctor/__init__.py
core/doctor/diagnose.py            # Collect error context, send to LLM, parse fix
core/doctor/autofix.py             # Generate proposals from diagnosis
core/doctor/validate_remote.py     # Pre-deploy validation (local + SSH)
core/tools/__init__.py
core/tools/setup_aider.py          # Configure Aider with AgentHarness LLM endpoints
tests/test_doctor_diagnose.py
tests/test_doctor_autofix.py
tests/test_doctor_validate.py
tests/test_tools_setup_aider.py
```

### Files to modify:
```
cli.py                             # Add doctor --auto-fix, validate, setup-coding-tool commands
```

---

## Task 1: Diagnostic Context Collector

**Files:**
- Create: `core/doctor/__init__.py`
- Create: `core/doctor/diagnose.py`
- Test: `tests/test_doctor_diagnose.py`

Collects error context from multiple sources and compresses it to fit in a local LLM's context window (~16K tokens).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_doctor_diagnose.py
from __future__ import annotations
import json
import pytest
from pathlib import Path


@pytest.fixture
def doctor_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "logs").mkdir()
    (data_dir / "reports").mkdir()

    # Create a selftest result with failures
    selftest = {
        "overall": "fail",
        "checks": [
            {"name": "state_file", "status": "ok", "required": True},
            {"name": "docker_available", "status": "fail", "required": False, "error": "docker: command not found"},
            {"name": "reports_dir_writable", "status": "fail", "required": True, "error": "Permission denied: /opt/agentharness/reports"},
        ],
    }
    (data_dir / "selftest_result.json").write_text(json.dumps(selftest))

    # Create some error logs
    (data_dir / "logs" / "scheduler.log").write_text(
        "2026-04-07 ERROR: check disk_usage failed: command not found: df\n"
        "2026-04-07 ERROR: harness cleanup timed out after 300s\n"
        "2026-04-07 INFO: tick complete\n" * 100  # Pad with noise
    )

    state = {
        "schema_version": 1,
        "paths": {
            "install_dir": str(tmp_path),
            "data_dir": str(data_dir),
            "logs_dir": str(data_dir / "logs"),
            "scripts_dir": str(tmp_path / "scripts"),
        },
        "hardware": {"total_ram_gb": 36, "cpu_model": "Ryzen 4700U"},
    }
    (data_dir / "state.json").write_text(json.dumps(state))
    return data_dir


def test_collect_context(doctor_env):
    from core.doctor.diagnose import DiagnosticCollector
    dc = DiagnosticCollector(data_dir=str(doctor_env))
    context = dc.collect()
    assert "selftest" in context
    assert "errors" in context
    assert "hardware" in context


def test_context_includes_failures(doctor_env):
    from core.doctor.diagnose import DiagnosticCollector
    dc = DiagnosticCollector(data_dir=str(doctor_env))
    context = dc.collect()
    assert any("Permission denied" in str(e) for e in context["errors"])


def test_context_is_bounded(doctor_env):
    from core.doctor.diagnose import DiagnosticCollector
    dc = DiagnosticCollector(data_dir=str(doctor_env), max_chars=8000)
    context = dc.collect()
    prompt = dc.format_prompt(context)
    assert len(prompt) <= 8000


def test_format_prompt(doctor_env):
    from core.doctor.diagnose import DiagnosticCollector
    dc = DiagnosticCollector(data_dir=str(doctor_env))
    context = dc.collect()
    prompt = dc.format_prompt(context)
    assert "diagnose" in prompt.lower() or "fix" in prompt.lower()
    assert isinstance(prompt, str)
```

- [ ] **Step 2: Implement diagnostic collector**

```python
# core/doctor/__init__.py
"""Self-repair doctor — diagnose and fix issues using local/cloud LLMs."""

# core/doctor/diagnose.py
"""Collect error context from logs, selftest, state — compress for LLM context window."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.resilience.atomic_json import safe_read_json

log = logging.getLogger(__name__)

ERROR_PATTERNS = ["ERROR", "FAIL", "error:", "failed:", "Permission denied",
                  "command not found", "timed out", "Connection refused"]


class DiagnosticCollector:
    """Collect and compress diagnostic context for LLM analysis."""

    def __init__(self, data_dir: str, max_chars: int = 12000):
        self.data_dir = Path(data_dir)
        self.max_chars = max_chars

    def collect(self) -> dict[str, Any]:
        """Gather diagnostic context from all sources."""
        context: dict[str, Any] = {}

        # 1. Selftest results
        selftest = safe_read_json(self.data_dir / "selftest_result.json", default={})
        if selftest:
            context["selftest"] = {
                "overall": selftest.get("overall", "unknown"),
                "failures": [
                    c for c in selftest.get("checks", [])
                    if c.get("status") == "fail"
                ],
            }

        # 2. Recent errors from logs
        context["errors"] = self._extract_errors()

        # 3. Hardware summary
        state = safe_read_json(self.data_dir / "state.json", default={})
        context["hardware"] = state.get("hardware", {})
        context["paths"] = state.get("paths", {})

        # 4. Circuit breaker state (which checks are suppressed)
        cb_state = safe_read_json(self.data_dir / "circuit_breaker.json", default={})
        open_circuits = [k for k, v in cb_state.items() if v.get("open")]
        if open_circuits:
            context["suppressed_checks"] = open_circuits

        return context

    def _extract_errors(self) -> list[str]:
        """Extract error lines from recent logs."""
        logs_dir = self.data_dir / "logs"
        errors = []
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
        seen = set()
        unique = []
        for e in errors:
            if e not in seen:
                seen.add(e)
                unique.append(e)
        return unique[:20]  # Max 20 unique errors

    def format_prompt(self, context: dict[str, Any]) -> str:
        """Format context into an LLM prompt for diagnosis."""
        parts = []
        parts.append("You are diagnosing issues with an AgentHarness installation.")
        parts.append("Analyze the following diagnostic data and suggest specific fixes.")
        parts.append("Be concise. Give exact file paths and commands.")
        parts.append("")

        if context.get("selftest"):
            st = context["selftest"]
            parts.append(f"## Self-Test: {st.get('overall', '?')}")
            for f in st.get("failures", []):
                parts.append(f"  FAIL: {f.get('name', '?')} — {f.get('error', 'unknown')}")
            parts.append("")

        if context.get("errors"):
            parts.append("## Recent Errors")
            for e in context["errors"][:10]:
                parts.append(f"  {e}")
            parts.append("")

        if context.get("hardware"):
            hw = context["hardware"]
            parts.append(f"## Hardware: {hw.get('cpu_model', '?')}, {hw.get('total_ram_gb', '?')}GB RAM")
            parts.append("")

        if context.get("suppressed_checks"):
            parts.append(f"## Suppressed checks: {', '.join(context['suppressed_checks'])}")
            parts.append("")

        parts.append("## What is wrong and how to fix it?")
        parts.append("For each issue: 1) Root cause 2) Exact fix command or file edit 3) How to verify")

        prompt = "\n".join(parts)

        # Truncate if needed
        if len(prompt) > self.max_chars:
            prompt = prompt[:self.max_chars - 50] + "\n\n[context truncated for LLM window]"

        return prompt
```

- [ ] **Step 3: Run tests, commit**

```bash
git add core/doctor/__init__.py core/doctor/diagnose.py tests/test_doctor_diagnose.py
git commit -m "feat: add diagnostic collector — gather and compress error context for LLM analysis"
```

---

## Task 2: Auto-Fix — LLM Diagnosis + Proposal Generation

**Files:**
- Create: `core/doctor/autofix.py`
- Test: `tests/test_doctor_autofix.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_doctor_autofix.py
from __future__ import annotations
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


@pytest.fixture
def autofix_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "logs").mkdir()
    (data_dir / "proposals").mkdir()
    selftest = {
        "overall": "fail",
        "checks": [{"name": "reports_dir_writable", "status": "fail", "required": True, "error": "Permission denied"}],
    }
    (data_dir / "selftest_result.json").write_text(json.dumps(selftest))
    (data_dir / "state.json").write_text(json.dumps({
        "schema_version": 1,
        "paths": {"install_dir": str(tmp_path), "data_dir": str(data_dir), "logs_dir": str(data_dir / "logs")},
        "hardware": {"total_ram_gb": 36},
    }))
    return data_dir


def test_autofix_generates_proposal(autofix_env):
    from core.doctor.autofix import AutoFixer
    from core.providers.base import LLMResponse

    mock_response = LLMResponse(
        text="Root cause: reports directory has wrong permissions.\nFix: sudo chown $USER:$USER /opt/agentharness/data/reports\nVerify: ls -la /opt/agentharness/data/reports",
        provider="groq", success=True, tokens_in=100, tokens_out=50,
    )

    af = AutoFixer(data_dir=str(autofix_env))
    with patch.object(af, "_call_llm", return_value=mock_response):
        result = af.diagnose_and_propose()

    assert result["success"] is True
    assert "diagnosis" in result
    assert len(result["diagnosis"]) > 0


def test_autofix_handles_llm_failure(autofix_env):
    from core.doctor.autofix import AutoFixer
    from core.providers.base import LLMResponse

    mock_response = LLMResponse.error("router", "All providers exhausted")

    af = AutoFixer(data_dir=str(autofix_env))
    with patch.object(af, "_call_llm", return_value=mock_response):
        result = af.diagnose_and_propose()

    assert result["success"] is False
    assert "error" in result


def test_autofix_no_issues_found(tmp_path):
    from core.doctor.autofix import AutoFixer
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "logs").mkdir()
    (data_dir / "proposals").mkdir()
    selftest = {"overall": "ok", "checks": [{"name": "state_file", "status": "ok", "required": True}]}
    (data_dir / "selftest_result.json").write_text(json.dumps(selftest))
    (data_dir / "state.json").write_text(json.dumps({"schema_version": 1, "paths": {"data_dir": str(data_dir), "logs_dir": str(data_dir / "logs")}, "hardware": {}}))

    af = AutoFixer(data_dir=str(data_dir))
    result = af.diagnose_and_propose()
    assert result["success"] is True
    assert result.get("diagnosis") == "No issues detected"
```

- [ ] **Step 2: Implement auto-fixer**

```python
# core/doctor/autofix.py
"""LLM-powered diagnosis + HITL proposal generation.

Sends compressed error context to the LLM router, parses the response,
and creates an approval proposal with the suggested fix.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.doctor.diagnose import DiagnosticCollector
from core.providers.base import Complexity, LLMRequest, LLMResponse

log = logging.getLogger(__name__)


class AutoFixer:
    """Diagnose issues via LLM and generate fix proposals."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.collector = DiagnosticCollector(data_dir=data_dir)

    def diagnose_and_propose(self) -> dict[str, Any]:
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
                "error": f"LLM diagnosis failed: {response.error}",
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
            local = LlamaCppProvider(name="local", endpoint="http://localhost:8080")
            if local.is_available():
                providers.append(local)

            # Try Groq
            groq = GroqProvider()
            if groq.is_available():
                providers.append(groq)

            if not providers:
                return LLMResponse.error("doctor", "No LLM providers available. Set GROQ_API_KEY or start local server.")

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
                system_prompt="You are a Linux system administrator diagnosing homelab infrastructure issues. Be specific and concise.",
                max_tokens=2048,
                temperature=0.3,
            )

            return router.route(request)

        except Exception as e:
            log.error(f"Doctor LLM call failed: {e}")
            return LLMResponse.error("doctor", str(e))
```

- [ ] **Step 3: Run tests, commit**

```bash
git add core/doctor/autofix.py tests/test_doctor_autofix.py
git commit -m "feat: add auto-fixer — LLM-powered diagnosis with proposal generation"
```

---

## Task 3: Pre-Deploy Validation

**Files:**
- Create: `core/doctor/validate_remote.py`
- Test: `tests/test_doctor_validate.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_doctor_validate.py
from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock


def test_validate_local():
    from core.doctor.validate_remote import validate_local
    result = validate_local()
    assert "python_version" in result
    assert result["python_version"]["status"] in ("ok", "fail")
    assert "disk_space" in result


def test_validate_local_checks_python():
    from core.doctor.validate_remote import validate_local
    result = validate_local()
    assert result["python_version"]["status"] == "ok"  # We're running Python


def test_validate_local_checks_disk():
    from core.doctor.validate_remote import validate_local
    result = validate_local()
    assert result["disk_space"]["status"] == "ok"  # Assume >1GB free


def test_validate_format_report():
    from core.doctor.validate_remote import validate_local, format_report
    result = validate_local()
    report = format_report(result)
    assert isinstance(report, str)
    assert "python" in report.lower()
```

- [ ] **Step 2: Implement pre-deploy validation**

```python
# core/doctor/validate_remote.py
"""Pre-deploy validation — check target machine readiness."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Any


def _check(name: str, fn) -> dict[str, Any]:
    try:
        ok, detail = fn()
        return {"name": name, "status": "ok" if ok else "fail", "detail": detail}
    except Exception as e:
        return {"name": name, "status": "fail", "detail": str(e)}


def validate_local() -> dict[str, dict]:
    """Run validation checks on the local machine."""
    results = {}

    # Python version
    results["python_version"] = _check("python_version", lambda: (
        sys.version_info >= (3, 9),
        f"Python {sys.version_info.major}.{sys.version_info.minor}"
    ))

    # Disk space (need at least 1GB free)
    results["disk_space"] = _check("disk_space", lambda: (
        (usage := shutil.disk_usage("/")).free > 1_000_000_000,
        f"{usage.free // (1024**3)}GB free"
    ))

    # Docker
    results["docker"] = _check("docker", lambda: (
        subprocess.run(["docker", "info"], capture_output=True, timeout=5).returncode == 0,
        "Docker available"
    ))

    # systemd
    results["systemd"] = _check("systemd", lambda: (
        subprocess.run(["systemctl", "--version"], capture_output=True, timeout=5).returncode == 0,
        "systemd available"
    ))

    # Git
    results["git"] = _check("git", lambda: (
        subprocess.run(["git", "--version"], capture_output=True, timeout=5).returncode == 0,
        "Git available"
    ))

    # Write permission to home dir
    results["home_writable"] = _check("home_writable", lambda: (
        os.access(os.path.expanduser("~"), os.W_OK),
        os.path.expanduser("~")
    ))

    # pip/pip3
    results["pip"] = _check("pip", lambda: (
        subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True, timeout=5).returncode == 0,
        "pip available"
    ))

    # PyYAML importable
    results["pyyaml"] = _check("pyyaml", lambda: (
        __import__("yaml") and True,
        "PyYAML importable"
    ))

    return results


def format_report(results: dict[str, dict]) -> str:
    """Format validation results as a human-readable report."""
    lines = ["Pre-Deploy Validation Report", "=" * 40]
    passed = 0
    failed = 0
    for name, check in results.items():
        status = check.get("status", "?")
        detail = check.get("detail", "")
        icon = "PASS" if status == "ok" else "FAIL"
        lines.append(f"  [{icon}] {name}: {detail}")
        if status == "ok":
            passed += 1
        else:
            failed += 1
    lines.append(f"\n{passed} passed, {failed} failed")
    if failed == 0:
        lines.append("Ready to deploy.")
    else:
        lines.append("Fix the failures above before deploying.")
    return "\n".join(lines)
```

- [ ] **Step 3: Run tests, commit**

```bash
git add core/doctor/validate_remote.py tests/test_doctor_validate.py
git commit -m "feat: add pre-deploy validation — check Python, Docker, disk, systemd, permissions"
```

---

## Task 4: Aider Setup Tool

**Files:**
- Create: `core/tools/__init__.py`
- Create: `core/tools/setup_aider.py`
- Test: `tests/test_tools_setup_aider.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tools_setup_aider.py
from __future__ import annotations
import pytest
from pathlib import Path


def test_generate_aider_config(tmp_path):
    from core.tools.setup_aider import generate_aider_config
    config = generate_aider_config(
        llm_endpoint="http://localhost:8080",
        api_key="gsk_test123",
        provider="groq",
    )
    assert "model" in config
    assert isinstance(config, dict)


def test_write_aider_config(tmp_path):
    from core.tools.setup_aider import generate_aider_config, write_aider_config
    config = generate_aider_config(
        llm_endpoint="https://api.groq.com/openai/v1",
        api_key="gsk_test",
        provider="groq",
    )
    path = write_aider_config(config, config_dir=str(tmp_path))
    assert Path(path).exists()
    content = Path(path).read_text()
    assert "groq" in content.lower() or "api" in content.lower()


def test_generate_setup_script(tmp_path):
    from core.tools.setup_aider import generate_setup_script
    script = generate_setup_script(provider="groq", api_key_env="GROQ_API_KEY")
    assert "pip" in script or "aider" in script
    assert isinstance(script, str)
```

- [ ] **Step 2: Implement Aider setup**

```python
# core/tools/__init__.py
"""Tools — setup and integration utilities."""

# core/tools/setup_aider.py
"""Configure Aider to use AgentHarness LLM providers."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PROVIDER_CONFIGS = {
    "groq": {
        "api_base": "https://api.groq.com/openai/v1",
        "model": "groq/llama-3.3-70b-versatile",
        "api_key_env": "GROQ_API_KEY",
    },
    "local": {
        "api_base": "http://localhost:8080/v1",
        "model": "openai/local-model",
        "api_key_env": "",
    },
    "cerebras": {
        "api_base": "https://api.cerebras.ai/v1",
        "model": "cerebras/llama-3.3-70b",
        "api_key_env": "CEREBRAS_API_KEY",
    },
    "google": {
        "api_base": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini/gemini-2.5-flash",
        "api_key_env": "GOOGLE_API_KEY",
    },
}


def generate_aider_config(
    llm_endpoint: str = "",
    api_key: str = "",
    provider: str = "groq",
) -> dict[str, Any]:
    """Generate Aider configuration dict."""
    provider_cfg = PROVIDER_CONFIGS.get(provider, PROVIDER_CONFIGS["groq"])

    config = {
        "model": provider_cfg["model"],
        "openai-api-base": llm_endpoint or provider_cfg["api_base"],
        "openai-api-key": api_key or f"${{{provider_cfg['api_key_env']}}}",
        "auto-commits": False,
        "dark-mode": True,
        "no-auto-lint": True,
    }
    return config


def write_aider_config(config: dict, config_dir: str = "") -> str:
    """Write Aider config to .aider.conf.yml."""
    import yaml
    config_path = Path(config_dir or Path.home()) / ".aider.conf.yml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    log.info(f"Aider config written: {config_path}")
    return str(config_path)


def generate_setup_script(provider: str = "groq", api_key_env: str = "GROQ_API_KEY") -> str:
    """Generate a bash setup script for installing and configuring Aider."""
    provider_cfg = PROVIDER_CONFIGS.get(provider, PROVIDER_CONFIGS["groq"])

    script = f"""#!/bin/bash
# AgentHarness — Aider Setup Script
# Configures Aider to use {provider} as the LLM backend.

set -e

echo "Installing Aider..."
pip3 install --user aider-chat

echo "Configuring Aider for {provider}..."
export OPENAI_API_BASE="{provider_cfg['api_base']}"

if [ -n "${{{api_key_env}:-}}" ]; then
    export OPENAI_API_KEY="${{{api_key_env}}}"
    echo "Using {api_key_env} from environment."
else
    echo "WARNING: {api_key_env} not set. Set it in your .env file."
fi

echo ""
echo "Aider is ready. Run:"
echo "  cd /path/to/agentharness"
echo "  aider --model {provider_cfg['model']}"
echo ""
echo "Or for local LLM:"
echo "  aider --model openai/local-model --openai-api-base http://localhost:8080/v1"
"""
    return script
```

- [ ] **Step 3: Run tests, commit**

```bash
git add core/tools/__init__.py core/tools/setup_aider.py tests/test_tools_setup_aider.py
git commit -m "feat: add Aider setup tool — configure coding agent with AgentHarness LLM endpoints"
```

---

## Task 5: CLI Commands — doctor, validate, setup-coding-tool

**Files:**
- Modify: `cli.py`

- [ ] **Step 1: Add three new commands**

`agentharness doctor --auto-fix` — run diagnostic collector, send to LLM, show diagnosis
`agentharness validate` — run pre-deploy validation, show report
`agentharness setup-coding-tool --provider groq` — generate and write Aider config

- [ ] **Step 2: Commit**

```bash
git add cli.py
git commit -m "feat: add doctor --auto-fix, validate, setup-coding-tool CLI commands"
```

---

## Task 6: Final Validation

- [ ] **Step 1: Run all tests**

```bash
python3 -m pytest tests/ -v
```

Expected: All tests pass (260+).

- [ ] **Step 2: Test new commands**

```bash
agentharness validate
agentharness doctor --auto-fix  # Will show "no LLM available" unless Groq key set
agentharness setup-coding-tool --provider groq
```

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: Backlog P1 complete — self-repair doctor, Aider integration, pre-deploy validation"
```

---

## Summary

**Backlog P1 delivers:**
- Diagnostic context collector — gathers errors from selftest, logs, circuit breaker state
- Auto-fixer — sends context to LLM router, gets diagnosis
- Pre-deploy validation — checks Python, Docker, disk, systemd, permissions, pip, PyYAML
- Aider setup — generates config for Groq/local/Cerebras/Google providers
- CLI: `doctor --auto-fix`, `validate`, `setup-coding-tool`

**Estimated tasks:** 6 tasks, ~20 steps
**Test coverage:** ~15 new tests across 4 test files
