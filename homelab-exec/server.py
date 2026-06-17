"""Homelab Exec MCP server.
Wraps existing homelab automation scripts as MCP tools for Claude Code.

Tiers:
  1. Health/Status — read-only, safe
  2. Service Control — write, reversible
  3. Remediation — write, guarded (confirm required)
  4. Maintenance — write, long-running (confirm required)
"""
from __future__ import annotations
import os, sys, json, subprocess, logging, shutil

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("homelab-exec")

MAX_OUTPUT = 4000  # cap tool output to avoid context flooding


def _run(cmd: list[str], timeout: int = 30, env: dict | None = None) -> dict:
    """Run a subprocess, return {status, output, errors}."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            env=env or os.environ,
        )
        stdout = r.stdout.strip()
        stderr = r.stderr.strip()
        if len(stdout) > MAX_OUTPUT:
            stdout = stdout[:MAX_OUTPUT] + "\n... (truncated)"
        if len(stderr) > MAX_OUTPUT:
            stderr = stderr[:MAX_OUTPUT] + "\n... (truncated)"
        return {
            "status": "ok" if r.returncode == 0 else "failed",
            "exit_code": r.returncode,
            "output": stdout,
            "errors": stderr,
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": f"Command timed out after {timeout}s"}
    except FileNotFoundError as e:
        return {"status": "not_found", "error": str(e)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _require_confirm(args: dict) -> bool:
    """Check that confirm=true is set for guarded tools."""
    return args.get("confirm") is True


def _docker_env() -> dict:
    """Return env for docker CLI — use mounted socket, not TCP."""
    env = os.environ.copy()
    # Remove DOCKER_HOST so docker CLI uses /var/run/docker.sock
    env.pop("DOCKER_HOST", None)
    return env


# ── Tier 1: Health & Status ──────────────────────────────────────────────────

def health_score(args: dict) -> dict:
    """Quick health check — returns health_score (0-100), overall_status, and checks."""
    result = _run(["python3", "/scripts/health_dashboard.py", "--quick"], timeout=15)
    if result["status"] == "ok":
        try:
            data = json.loads(result["output"])
            # Extract non-healthy checks for summary
            issues = []
            for name, check in data.get("checks", {}).items():
                if isinstance(check, dict) and check.get("status") not in ("healthy", "ok"):
                    issues.append(f"{name}: {check.get('status')} - {check.get('message', '')}")
            return {
                "health_score": data.get("health_score", "?"),
                "overall_status": data.get("overall_status", "?"),
                "elapsed_seconds": data.get("elapsed_seconds", "?"),
                "issues": issues,
            }
        except json.JSONDecodeError:
            return {"output": result["output"]}
    return result


def health_full(args: dict) -> dict:
    """Full health dashboard — all checks, slower."""
    result = _run(["python3", "/scripts/health_dashboard.py", "--text"], timeout=60)
    return result


def list_issues(args: dict) -> dict:
    """List detected issues from the autonomous fixer (dry-run scan)."""
    result = _run(
        ["python3", "/scripts/autonomous_fixer.py", "--dry-run", "--json"],
        timeout=30,
    )
    if result["status"] == "ok" and result["output"]:
        try:
            return {"issues": json.loads(result["output"])}
        except json.JSONDecodeError:
            return {"output": result["output"]}
    return {"issues": [], "message": "No issues detected" if result["status"] == "ok" else result}


def cost_status(args: dict) -> dict:
    """Check cost guard status — verifies all providers are free-tier."""
    env = os.environ.copy()
    env["PYTHONPATH"] = env.get("PYTHONPATH", "") + ":/home/rohit/.hermes/lib"
    env["HOME"] = "/home/rohit"
    # Ensure writable tmp for logs
    env["TMPDIR"] = "/tmp"
    try:
        r = subprocess.run(
            ["python3", "/scripts-hm/unified_cost_guard.py", "check"],
            capture_output=True, text=True, timeout=15, env=env,
            cwd="/tmp",
        )
        stdout = r.stdout.strip()
        stderr = r.stderr.strip()
        if len(stdout) > MAX_OUTPUT:
            stdout = stdout[:MAX_OUTPUT] + "\n... (truncated)"
        if r.returncode == 0:
            return {"status": "all_ok", "details": stdout}
        return {"status": "failed", "exit_code": r.returncode, "output": stdout, "errors": stderr}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": "Command timed out after 15s"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def capsule_stats(args: dict) -> dict:
    """Get Gene/Capsule strategy success rates."""
    env = os.environ.copy()
    env["HOME"] = "/home/rohit"
    try:
        r = subprocess.run(
            ["python3", "/scripts-hm/capsule_tracker.py", "stats"],
            capture_output=True, text=True, timeout=10, env=env,
        )
        stdout = r.stdout.strip()
        if len(stdout) > MAX_OUTPUT:
            stdout = stdout[:MAX_OUTPUT] + "\n... (truncated)"
        return {"status": "ok" if r.returncode == 0 else "failed", "output": stdout}
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Tier 2: Service Control ──────────────────────────────────────────────────

def _systemd_cmd(cmd: list[str], timeout: int = 10) -> dict:
    """Run a systemd user command via --machine=rohit@.host --user."""
    # Use machinectl to access the host's user session
    full_cmd = ["systemctl", "--machine=rohit@.host", "--user"] + cmd
    return _run(full_cmd, timeout=timeout)


def service_status(args: dict) -> dict:
    """Get systemd service status."""
    name = args.get("name", "")
    if not name:
        return {"error": "name required"}
    return _systemd_cmd(["status", name], timeout=10)


def service_restart(args: dict) -> dict:
    """Restart a systemd user service."""
    name = args.get("name", "")
    if not name:
        return {"error": "name required"}
    return _systemd_cmd(["restart", name], timeout=30)


def service_list(args: dict) -> dict:
    """List all user systemd services and their states."""
    result = _systemd_cmd(
        ["list-units", "--type=service", "--all", "--no-pager"],
        timeout=10,
    )
    return result


def docker_restart(args: dict) -> dict:
    """Restart a Docker container."""
    name = args.get("name", "")
    if not name:
        return {"error": "name required"}
    return _run(["docker", "restart", name], timeout=30, env=_docker_env())


def docker_logs(args: dict) -> dict:
    """Get recent Docker container logs."""
    name = args.get("name", "")
    if not name:
        return {"error": "name required"}
    tail = args.get("tail", 50)
    return _run(["docker", "logs", "--tail", str(tail), name], timeout=10, env=_docker_env())


def docker_list(args: dict) -> dict:
    """List all Docker containers with status."""
    result = _run(
        ["docker", "ps", "--all", "--format", "{{.Names}}\t{{.Status}}\t{{.Ports}}"],
        timeout=10,
        env=_docker_env(),
    )
    if result["status"] == "ok" and result["output"]:
        containers = []
        for line in result["output"].split("\n"):
            parts = line.split("\t")
            if len(parts) >= 2:
                containers.append({"name": parts[0], "status": parts[1]})
        return {"containers": containers, "count": len(containers)}
    return result


# ── Tier 3: Remediation (guarded) ────────────────────────────────────────────

def fix_issue(args: dict) -> dict:
    """Run the autonomous fixer for a specific issue type. Requires confirm=true."""
    if not _require_confirm(args):
        return {"error": "confirm=true required for remediation tools"}
    issue_type = args.get("issue_type", "")
    min_sev = args.get("min_severity", "medium")
    if issue_type:
        cmd = [
            "python3", "/scripts/autonomous_fixer.py",
            "--dry-run", "--json",
            "--min-severity", min_sev,
        ]
    else:
        cmd = [
            "python3", "/scripts/autonomous_fixer.py",
            "--dry-run", "--json",
            "--min-severity", min_sev,
        ]
    return _run(cmd, timeout=120)


def ghost_check(args: dict) -> dict:
    """Scan for and clean ghost Docker containers. Requires confirm=true."""
    if not _require_confirm(args):
        return {"error": "confirm=true required for remediation tools"}
    return _run(["bash", "/scripts/docker_ghost_check.sh"], timeout=30)


def run_audit(args: dict) -> dict:
    """Run daily audit. Requires confirm=true."""
    if not _require_confirm(args):
        return {"error": "confirm=true required for remediation tools"}
    result = _run(["python3", "/scripts/daily_audit.py"], timeout=60)
    # Try to read the latest report
    reports_dir = "/home/rohit/agentharness/data/reports"
    if os.path.isdir(reports_dir):
        reports = sorted(
            [f for f in os.listdir(reports_dir) if f.startswith("audit_")],
            reverse=True,
        )
        if reports:
            report_path = os.path.join(reports_dir, reports[0])
            try:
                with open(report_path) as f:
                    report_text = f.read()
                if len(report_text) > MAX_OUTPUT:
                    report_text = report_text[:MAX_OUTPUT] + "\n... (truncated)"
                result["latest_report"] = report_text
                result["report_file"] = reports[0]
            except Exception:
                pass
    return result


# ── Tier 4: Maintenance (long-running, guarded) ─────────────────────────────

def backup_status(args: dict) -> dict:
    """Check latest Kopia backup status."""
    result = _run(
        ["kopia", "snapshot", "list", "--limit", "5"],
        timeout=30,
    )
    return result


def backup_verify(args: dict) -> dict:
    """Verify backup integrity. Requires confirm=true."""
    if not _require_confirm(args):
        return {"error": "confirm=true required for maintenance tools"}
    return _run(["bash", "/scripts/verify_backups.sh"], timeout=300)


def run_optimize(args: dict) -> dict:
    """Run weekly optimization. Requires confirm=true."""
    if not _require_confirm(args):
        return {"error": "confirm=true required for maintenance tools"}
    return _run(["bash", "/scripts/weekly_optimize.sh"], timeout=300)


# ── Tool schemas ─────────────────────────────────────────────────────────────

TOOL_SCHEMAS = [
    # Tier 1 — Health & Status
    {
        "name": "health_score",
        "description": "Quick homelab health check. Returns health_score (0-100), overall_status, and per-service checks. Fast (~1s).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "health_full",
        "description": "Full health dashboard with all checks. Slower but comprehensive.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_issues",
        "description": "List detected issues from the autonomous fixer (dry-run scan). Returns issue types, targets, and severities.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "cost_status",
        "description": "Check cost guard — verifies all LLM providers are free-tier and no unexpected charges.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "capsule_stats",
        "description": "Get Gene/Capsule strategy success rates — which fix strategies work best historically.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    # Tier 2 — Service Control
    {
        "name": "service_status",
        "description": "Get systemd user service status (active, loaded, sub-state).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Service name, e.g. hermes-gateway"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "service_restart",
        "description": "Restart a systemd user service.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Service name, e.g. hermes-gateway"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "service_list",
        "description": "List all user systemd services and their states.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "docker_restart",
        "description": "Restart a Docker container by name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Container name"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "docker_logs",
        "description": "Get recent logs from a Docker container.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Container name"},
                "tail": {"type": "integer", "description": "Number of lines (default: 50)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "docker_list",
        "description": "List all Docker containers with name and status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    # Tier 3 — Remediation (guarded)
    {
        "name": "fix_issue",
        "description": "Run the autonomous fixer dry-run scan to detect issues. Requires confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "description": "Must be true to execute"},
                "min_severity": {"type": "string", "description": "low/medium/high/critical (default: medium)"},
            },
            "required": ["confirm"],
        },
    },
    {
        "name": "ghost_check",
        "description": "Scan for and clean ghost Docker containers. Requires confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "description": "Must be true to execute"},
            },
            "required": ["confirm"],
        },
    },
    {
        "name": "run_audit",
        "description": "Run the daily audit. Returns findings and latest report. Requires confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "description": "Must be true to execute"},
            },
            "required": ["confirm"],
        },
    },
    # Tier 4 — Maintenance (long-running, guarded)
    {
        "name": "backup_status",
        "description": "Check latest Kopia backup snapshots.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "backup_verify",
        "description": "Verify backup integrity (can take minutes). Requires confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "description": "Must be true to execute"},
            },
            "required": ["confirm"],
        },
    },
    {
        "name": "run_optimize",
        "description": "Run weekly optimization scripts. Requires confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "description": "Must be true to execute"},
            },
            "required": ["confirm"],
        },
    },
]


def main():
    port = int(os.environ.get("MCP_PORT", "8108"))
    s = MCPServer(name="homelab-exec", port=port, tools=TOOL_SCHEMAS)
    # Tier 1
    for name, fn in [
        ("health_score", health_score),
        ("health_full", health_full),
        ("list_issues", list_issues),
        ("cost_status", cost_status),
        ("capsule_stats", capsule_stats),
    ]:
        s.register_handler(name, fn)
    # Tier 2
    for name, fn in [
        ("service_status", service_status),
        ("service_restart", service_restart),
        ("service_list", service_list),
        ("docker_restart", docker_restart),
        ("docker_logs", docker_logs),
        ("docker_list", docker_list),
    ]:
        s.register_handler(name, fn)
    # Tier 3
    for name, fn in [
        ("fix_issue", fix_issue),
        ("ghost_check", ghost_check),
        ("run_audit", run_audit),
    ]:
        s.register_handler(name, fn)
    # Tier 4
    for name, fn in [
        ("backup_status", backup_status),
        ("backup_verify", backup_verify),
        ("run_optimize", run_optimize),
    ]:
        s.register_handler(name, fn)
    log.info("Homelab Exec MCP starting on :%d with %d tools", port, len(TOOL_SCHEMAS))
    s.start()


if __name__ == "__main__":
    main()
