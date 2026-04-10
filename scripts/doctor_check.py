#!/usr/bin/env python3
"""doctor_check.py — Standalone health-check script for Chaguli /doctor command.

Usage:
    python3 scripts/doctor_check.py              # full status report
    python3 scripts/doctor_check.py --fix NAME   # run a specific runbook
    python3 scripts/doctor_check.py --json        # machine-readable output

Designed to be called via subprocess from Chaguli or any MCP tool.
Output is plain text (no HTML, no markdown) suitable for Telegram.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve project root (works from ~/agentharness or the repo checkout)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = Path(os.environ.get("AH_DATA_DIR", str(PROJECT_ROOT / "data")))
RUNBOOKS_DIR = PROJECT_ROOT / "core" / "doctor" / "runbooks"
LOG_FILE = DATA_DIR / "doctor_log.jsonl"
COOLDOWN_FILE = DATA_DIR / "doctor_cooldowns.json"

# Services to probe: (label, host, port, path)
SERVICES = [
    ("LLM Proxy", "127.0.0.1", 8080, "/health"),
    ("Local LLM", "127.0.0.1", 8081, "/health"),
    ("MCP Gateway", "127.0.0.1", 8096, "/health"),
]

CHAGULI_CONTAINER = "chaguli"


# ---------------------------------------------------------------------------
# Health probes
# ---------------------------------------------------------------------------

def _http_ok(host: str, port: int, path: str, timeout: int = 5) -> bool:
    """Return True if an HTTP GET returns 2xx."""
    try:
        result = subprocess.run(
            ["curl", "-sf", "-o", "/dev/null", "-w", "%{http_code}",
             f"http://{host}:{port}{path}", "--max-time", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 2,
        )
        code = result.stdout.strip()
        return code.startswith("2")
    except Exception:
        return False


def _docker_running(container: str) -> bool:
    """Return True if a Docker container is running."""
    if not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip().lower() == "true"
    except Exception:
        return False


def check_services() -> list[dict]:
    """Probe HTTP services and Docker containers."""
    results = []
    for label, host, port, path in SERVICES:
        ok = _http_ok(host, port, path)
        results.append({
            "name": f"{label} ({port})",
            "ok": ok,
        })
    # Chaguli container
    ok = _docker_running(CHAGULI_CONTAINER)
    results.append({
        "name": f"{CHAGULI_CONTAINER} (container)",
        "ok": ok,
    })
    return results


# ---------------------------------------------------------------------------
# Resource usage
# ---------------------------------------------------------------------------

def _disk_usage() -> dict:
    """Return disk usage for the root filesystem."""
    try:
        st = shutil.disk_usage("/")
        total_gb = st.total / (1024 ** 3)
        free_gb = st.free / (1024 ** 3)
        pct = int((st.used / st.total) * 100)
        return {"total_gb": round(total_gb, 1), "free_gb": round(free_gb, 1), "pct": pct}
    except Exception:
        return {"total_gb": 0, "free_gb": 0, "pct": 0}


def _mem_usage() -> dict:
    """Return RAM and swap usage. Works on Linux; degrades on macOS."""
    mem = {"total_gb": 0.0, "used_gb": 0.0, "pct": 0, "swap_mb": 0}
    try:
        result = subprocess.run(
            ["free", "-b"], capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if parts and parts[0] == "Mem:":
                total = int(parts[1])
                used = int(parts[2])
                mem["total_gb"] = round(total / (1024 ** 3), 1)
                mem["used_gb"] = round(used / (1024 ** 3), 1)
                mem["pct"] = int((used / total) * 100) if total else 0
            elif parts and parts[0] == "Swap:":
                swap_used = int(parts[2])
                mem["swap_mb"] = round(swap_used / (1024 ** 2))
    except FileNotFoundError:
        # macOS: use vm_stat + sysctl
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5,
            )
            total = int(result.stdout.strip())
            mem["total_gb"] = round(total / (1024 ** 3), 1)

            result = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=5,
            )
            pages: dict[str, int] = {}
            page_size = 4096  # default on Apple Silicon and Intel
            for line in result.stdout.splitlines():
                if "page size of" in line:
                    page_size = int(line.split()[-2])
                for key in ("free", "active", "inactive", "wired", "compressor"):
                    if key in line.lower() and ":" in line:
                        val = line.split(":")[1].strip().rstrip(".")
                        try:
                            pages[key] = int(val)
                        except ValueError:
                            pass
            used_pages = pages.get("active", 0) + pages.get("wired", 0) + pages.get("compressor", 0)
            used = used_pages * page_size
            mem["used_gb"] = round(used / (1024 ** 3), 1)
            mem["pct"] = int((used / total) * 100) if total else 0

            result = subprocess.run(
                ["sysctl", "-n", "vm.swapusage"],
                capture_output=True, text=True, timeout=5,
            )
            # format: "total = 2048.00M  used = 123.45M  free = ..."
            for part in result.stdout.split():
                if part.endswith("M") and "used" in result.stdout.split("=")[0]:
                    pass
            # Simpler parse
            chunks = result.stdout.replace("=", " ").split()
            for i, tok in enumerate(chunks):
                if tok == "used" and i + 1 < len(chunks):
                    val = chunks[i + 1].rstrip("M").rstrip("G")
                    try:
                        mem["swap_mb"] = int(float(val))
                    except ValueError:
                        pass
        except Exception:
            pass
    except Exception:
        pass
    return mem


def check_resources() -> dict:
    return {"disk": _disk_usage(), "mem": _mem_usage()}


# ---------------------------------------------------------------------------
# Runbooks, log, cooldowns
# ---------------------------------------------------------------------------

def list_runbooks() -> list[str]:
    """Return names of available runbooks."""
    if not RUNBOOKS_DIR.is_dir():
        return []
    return sorted(p.stem for p in RUNBOOKS_DIR.glob("*.yaml"))


def last_log_entry() -> dict | None:
    """Read the last line of doctor_log.jsonl."""
    if not LOG_FILE.is_file():
        return None
    try:
        # Read last non-empty line efficiently
        with LOG_FILE.open("rb") as fh:
            fh.seek(0, 2)  # end
            size = fh.tell()
            if size == 0:
                return None
            # Read last 4KB at most
            fh.seek(max(0, size - 4096))
            lines = fh.read().decode("utf-8", errors="replace").strip().splitlines()
            if lines:
                return json.loads(lines[-1])
    except Exception:
        pass
    return None


def active_cooldowns() -> list[dict]:
    """Return list of active cooldowns from doctor_cooldowns.json."""
    if not COOLDOWN_FILE.is_file():
        return []
    try:
        data = json.loads(COOLDOWN_FILE.read_text(encoding="utf-8"))
        now = time.time()
        active = []
        for name, info in data.items():
            attempts = info.get("attempts", [])
            # Keep only attempts within 10-min window
            recent = [a for a in attempts if now - a < 600]
            if len(recent) >= 3:
                active.append({"name": name, "attempts": len(recent)})
        return active
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Fix mode: delegate to RunbookExecutor
# ---------------------------------------------------------------------------

def run_fix(runbook_name: str) -> str:
    """Execute a runbook and return a text summary."""
    # Add project root to sys.path so core.doctor is importable
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    try:
        from core.doctor.engine import RunbookExecutor

        executor = RunbookExecutor(
            data_dir=str(DATA_DIR),
            runbooks_dir=str(RUNBOOKS_DIR),
            alert_script=str(SCRIPT_DIR / "alert.sh"),
        )
        result = executor.execute(runbook_name, trigger_context="doctor_check_cli")
    except Exception as exc:
        return f"ERROR: could not run runbook '{runbook_name}': {exc}"

    lines = [
        f"Runbook: {result.runbook}",
        f"Result: {result.result}",
        f"Steps: {result.steps_executed} run, {result.steps_passed} passed, {result.steps_failed} failed",
        f"Fix applied: {result.fix_applied}",
        f"Duration: {result.duration_seconds:.1f}s",
        "",
    ]
    for sr in result.step_results:
        tag = "SKIP" if sr.skipped else ("OK" if sr.success else "FAIL")
        detail = sr.output[:120] if sr.output else sr.error[:120]
        lines.append(f"  [{tag}] {sr.name}: {detail}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def _ago(ts_str: str) -> str:
    """Convert ISO timestamp to 'Xm ago' or 'Xh ago'."""
    try:
        dt = datetime.fromisoformat(ts_str)
        # Make offset-aware if needed
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        secs = int(delta.total_seconds())
        if secs < 0:
            return "just now"
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return "unknown"


def build_report(as_json: bool = False) -> str:
    """Build the full status report."""
    services = check_services()
    resources = check_resources()
    runbooks = list_runbooks()
    last = last_log_entry()
    cooldowns = active_cooldowns()

    if as_json:
        return json.dumps({
            "services": services,
            "resources": resources,
            "runbooks": runbooks,
            "last_log": last,
            "cooldowns": cooldowns,
        }, indent=2)

    lines = ["Homelab Doctor -- Status Report", ""]

    # Services
    lines.append("Services:")
    for svc in services:
        tag = "OK" if svc["ok"] else "FAIL"
        lines.append(f"  [{tag}] {svc['name']}")

    # Resources
    lines.append("")
    lines.append("Resources:")
    d = resources["disk"]
    lines.append(f"  Disk: {d['pct']}% ({d['free_gb']}GB free)")
    m = resources["mem"]
    lines.append(f"  RAM: {m['used_gb']}GB/{m['total_gb']}GB ({m['pct']}%)")
    lines.append(f"  Swap: {m['swap_mb']}MB")

    # Runbooks
    lines.append("")
    lines.append(f"Runbooks: {len(runbooks)} available")

    # Last log entry
    if last:
        result_str = last.get("result", "?")
        rb = last.get("runbook", "?")
        ts = last.get("timestamp", "")
        ago = _ago(ts) if ts else "unknown"
        fix = " -- fixed" if last.get("fix_applied") else ""
        lines.append(f"Last run: {rb} -- {result_str}{fix} ({ago})")
    else:
        lines.append("Last run: none")

    # Cooldowns
    if cooldowns:
        cd_parts = [f"{c['name']}({c['attempts']})" for c in cooldowns]
        lines.append(f"Cooldowns: {', '.join(cd_parts)}")
    else:
        lines.append("Cooldowns: none active")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Homelab Doctor — health checks and runbook execution",
    )
    parser.add_argument(
        "--fix", metavar="RUNBOOK",
        help="Run a specific runbook by name (e.g. llm-server-offline)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Output machine-readable JSON instead of plain text",
    )
    args = parser.parse_args()

    if args.fix:
        print(run_fix(args.fix))
    else:
        print(build_report(as_json=args.as_json))


if __name__ == "__main__":
    main()
