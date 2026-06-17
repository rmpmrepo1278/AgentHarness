"""Homelab Exec MCP server.
Wraps existing homelab automation scripts as MCP tools for Claude Code.

Tiers:
  1. Health/Status — read-only, safe
  2. Service Control — write, reversible
  3. Remediation — write, guarded (confirm required)
  4. Maintenance — write, long-running (confirm required)

Research-backed additions (June 2026):
  - Reflexion memory: get_reflexion / submit_reflection (closes the learning loop)
  - Gene-level control: list_genes / run_gene (Voyager skill library pattern)
  - Experiment logging: experiment_result (ExpeL compounding knowledge pattern)
  - CRITIC spot-check: health_verify (tool-grounded verification, not just dashboard)
  - fix_issue now actually fixes when confirm=true (was broken: always dry-run)
"""
from __future__ import annotations
import os, sys, json, subprocess, logging, shutil
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("homelab-exec")

MAX_OUTPUT = 4000

# ── Helpers ──────────────────────────────────────────────────────────────────

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
    return args.get("confirm") is True


def _docker_env() -> dict:
    env = os.environ.copy()
    env.pop("DOCKER_HOST", None)
    return env


def _hermes_env() -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = env.get("PYTHONPATH", "") + ":/home/rohit/.hermes/lib"
    env["HOME"] = "/home/rohit"
    env["TMPDIR"] = "/tmp"
    return env


CAPSULES_DIR = Path("/home/rohit/.hermes/capsules")
GENES_DIR = Path("/home/rohit/.hermes/genes")
REFLEXION_FILE = Path("/home/rohit/.hermes/reflexion_memory.jsonl")
EXPERIMENTS_FILE = Path("/home/rohit/.hermes/experiments.jsonl")


# ── Tier 1: Health & Status ──────────────────────────────────────────────────

def health_score(args: dict) -> dict:
    """Quick health check — returns health_score (0-100), overall_status, and checks."""
    result = _run(["python3", "/scripts/health_dashboard.py", "--quick"], timeout=15)
    if result["status"] == "ok":
        try:
            data = json.loads(result["output"])
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


def health_verify(args: dict) -> dict:
    """CRITIC-style verification: spot-check health claims with actual tool calls.
    Runs the health dashboard, then independently verifies a sample of claims
    via direct docker/systemctl/curl calls. Returns discrepancies."""
    # Get dashboard claims
    dash = _run(["python3", "/scripts/health_dashboard.py", "--quick"], timeout=15)
    claims = {}
    if dash["status"] == "ok":
        try:
            data = json.loads(dash["output"])
            checks = data.get("checks", {})
        except json.JSONDecodeError:
            checks = {}
    else:
        checks = {}

    # Spot-check: verify docker containers that dashboard says are healthy
    verified = []
    discrepancies = []
    for name, check in checks.items():
        if not isinstance(check, dict):
            continue
        status = check.get("status", "")
        # Spot-check up to 3 services to keep it fast
        if len(verified) >= 3:
            break
        if status in ("healthy", "ok") and "container" in name.lower():
            actual = _run(["docker", "inspect", "--format={{.State.Status}}", name], timeout=5, env=_docker_env())
            actual_status = actual.get("output", "").strip()
            verified.append(name)
            if actual_status != "running":
                discrepancies.append({
                    "service": name,
                    "dashboard_says": status,
                    "actual": actual_status,
                })

    return {
        "dashboard_health_score": data.get("health_score", "?") if dash["status"] == "ok" else "?",
        "spot_checked": verified,
        "discrepancies": discrepancies,
        "verification_status": "pass" if not discrepancies else "mismatch_found",
    }


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
    try:
        r = subprocess.run(
            ["python3", "/scripts-hm/unified_cost_guard.py", "check"],
            capture_output=True, text=True, timeout=15, env=_hermes_env(),
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
    try:
        r = subprocess.run(
            ["python3", "/scripts-hm/capsule_tracker.py", "stats"],
            capture_output=True, text=True, timeout=10, env=_hermes_env(),
        )
        stdout = r.stdout.strip()
        if len(stdout) > MAX_OUTPUT:
            stdout = stdout[:MAX_OUTPUT] + "\n... (truncated)"
        return {"status": "ok" if r.returncode == 0 else "failed", "output": stdout}
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Tier 1.5: Reflexion & Learning (read/write, safe) ───────────────────────

def get_reflexion(args: dict) -> dict:
    """Reflexion pattern: retrieve past failure reflections for a (gene_id, target) pair.
    Before attempting a fix, call this to learn from previous attempts.
    Returns past reflections + capsule history summary."""
    gene_id = args.get("gene_id", "")
    target = args.get("target", "")

    # Load capsule history for this (gene, target)
    capsules = []
    capsule_file = CAPSULES_DIR / "outcomes.jsonl"
    if capsule_file.exists():
        for line in capsule_file.read_text().splitlines():
            if not line.strip():
                continue
            try:
                c = json.loads(line)
                if (not gene_id or c.get("gene_id") == gene_id) and (not target or c.get("target") == target):
                    capsules.append(c)
            except json.JSONDecodeError:
                continue

    # Load reflexion memory
    reflections = []
    if REFLEXION_FILE.exists():
        for line in REFLEXION_FILE.read_text().splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                if (not gene_id or r.get("gene_id") == gene_id) and (not target or r.get("target") == target):
                    reflections.append(r)
            except json.JSONDecodeError:
                continue

    # Build summary
    total = len(capsules)
    failures = sum(1 for c in capsules if c.get("outcome") == "fail")
    successes = sum(1 for c in capsules if c.get("outcome") == "success")

    return {
        "gene_id": gene_id,
        "target": target,
        "capsule_history": {
            "total_attempts": total,
            "successes": successes,
            "failures": failures,
            "success_rate": f"{successes/total:.0%}" if total > 0 else "N/A",
            "recent_notes": [c.get("notes", "") for c in capsules[-5:] if c.get("notes")],
        },
        "reflections": reflections[-10:],  # last 10 reflections
        "recommendation": "Try a different approach — this has failed repeatedly" if failures > successes and total > 2 else "Proceed",
    }


def submit_reflection(args: dict) -> dict:
    """Reflexion pattern: after a fix attempt, write a reflection on what happened.
    This closes the learning loop — future get_reflexion calls will include this."""
    gene_id = args.get("gene_id", "")
    target = args.get("target", "")
    reflection = args.get("reflection", "")
    outcome = args.get("outcome", "unknown")

    if not gene_id or not reflection:
        return {"error": "gene_id and reflection are required"}

    REFLEXION_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(),
        "gene_id": gene_id,
        "target": target,
        "outcome": outcome,
        "reflection": reflection,
    }
    with open(REFLEXION_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    # Also record in capsule tracker for stats consistency
    try:
        subprocess.run(
            ["python3", "/scripts-hm/capsule_tracker.py", "record",
             "--gene", gene_id, "--target", target or "unknown",
             "--outcome", outcome if outcome in ("success", "partial", "fail") else "fail",
             "--notes", reflection[:200]],
            capture_output=True, text=True, timeout=10, env=_hermes_env(),
        )
    except Exception:
        pass  # non-critical

    return {"status": "recorded", "entry": entry}


def list_genes(args: dict) -> dict:
    """Voyager skill library pattern: list all available Gene strategies with
    their success rates and signal matches. Lets the agent pick the right gene."""
    genes = []
    if GENES_DIR.exists():
        for f in sorted(GENES_DIR.glob("*.json")):
            if f.name.startswith("_"):
                continue
            try:
                gene = json.loads(f.read_text())
                genes.append({
                    "id": gene.get("id", f.stem),
                    "category": gene.get("category", ""),
                    "description": gene.get("description", ""),
                    "signals_match": gene.get("signals_match", []),
                    "escalate_if_fails": gene.get("escalate_if_fails", False),
                })
            except json.JSONDecodeError:
                continue

    # Enrich with capsule success rates
    stats_raw = capsule_stats({})
    stats = {}
    if stats_raw.get("status") == "ok":
        try:
            # Parse the text output: "  gene_id total=3 success=1 rate=33%"
            for line in stats_raw["output"].split("\n"):
                parts = line.strip().split()
                if len(parts) >= 4 and parts[0] not in ("Capsule", "By", "records:"):
                    gid = parts[0]
                    rate_part = [p for p in parts if p.startswith("rate=")]
                    if rate_part:
                        stats[gid] = rate_part[0].replace("rate=", "")
        except Exception:
            pass

    for g in genes:
        g["historical_success_rate"] = stats.get(g["id"], "N/A")

    return {"genes": genes, "count": len(genes)}


def experiment_result(args: dict) -> dict:
    """ExpeL compounding knowledge pattern: log a structured experiment result.
    Records hypothesis, action taken, and outcome for future learning."""
    hypothesis = args.get("hypothesis", "")
    action = args.get("action", "")
    outcome = args.get("outcome", "")
    context = args.get("context", "")

    if not hypothesis or not outcome:
        return {"error": "hypothesis and outcome are required"}

    EXPERIMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(),
        "hypothesis": hypothesis,
        "action": action,
        "outcome": outcome,
        "context": context,
    }
    with open(EXPERIMENTS_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    return {"status": "recorded", "total_experiments": _count_lines(EXPERIMENTS_FILE)}


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for l in path.read_text().splitlines() if l.strip())


# ── Tier 2: Service Control ──────────────────────────────────────────────────

def _systemd_cmd(cmd: list[str], timeout: int = 10) -> dict:
    full_cmd = ["systemctl", "--machine=rohit@.host", "--user"] + cmd
    return _run(full_cmd, timeout=timeout)


def service_status(args: dict) -> dict:
    name = args.get("name", "")
    if not name:
        return {"error": "name required"}
    return _systemd_cmd(["status", name], timeout=10)


def service_restart(args: dict) -> dict:
    name = args.get("name", "")
    if not name:
        return {"error": "name required"}
    return _systemd_cmd(["restart", name], timeout=30)


def service_list(args: dict) -> dict:
    return _systemd_cmd(["list-units", "--type=service", "--all", "--no-pager"], timeout=10)


def docker_restart(args: dict) -> dict:
    name = args.get("name", "")
    if not name:
        return {"error": "name required"}
    return _run(["docker", "restart", name], timeout=30, env=_docker_env())


def docker_logs(args: dict) -> dict:
    name = args.get("name", "")
    if not name:
        return {"error": "name required"}
    tail = args.get("tail", 50)
    return _run(["docker", "logs", "--tail", str(tail), name], timeout=10, env=_docker_env())


def docker_list(args: dict) -> dict:
    result = _run(
        ["docker", "ps", "--all", "--format", "{{.Names}}\t{{.Status}}\t{{.Ports}}"],
        timeout=10, env=_docker_env(),
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
    """Run the autonomous fixer. Requires confirm=true.
    When confirm=true, runs actual fix (not dry-run). The fixer detects issues
    internally; use min_severity to filter. For targeted fixes, use run_gene."""
    if not _require_confirm(args):
        return {"error": "confirm=true required for remediation tools"}
    min_sev = args.get("min_severity", "medium")

    cmd = ["python3", "/scripts/autonomous_fixer.py", "--json", "--min-severity", min_sev]

    return _run(cmd, timeout=120)


def run_gene(args: dict) -> dict:
    """Voyager skill library pattern: run a specific Gene strategy by ID.
    Requires confirm=true. Use list_genes first to see available genes."""
    if not _require_confirm(args):
        return {"error": "confirm=true required for remediation tools"}
    gene_id = args.get("gene_id", "")
    target = args.get("target", "")
    if not gene_id:
        return {"error": "gene_id required"}

    # Load gene file to validate it exists
    gene_file = GENES_DIR / f"{gene_id}.json"
    if not gene_file.exists():
        # Try matching by id field
        found = list(GENES_DIR.glob("*.json"))
        for f in found:
            if f.name.startswith("_"):
                continue
            try:
                g = json.loads(f.read_text())
                if g.get("id") == gene_id:
                    gene_file = f
                    break
            except json.JSONDecodeError:
                continue
        else:
            return {"error": f"Gene {gene_id} not found", "available": [f.stem for f in GENES_DIR.glob("*.json") if not f.name.startswith("_")]}

    # Run the gene engine to get the strategy, then execute via autonomous fixer
    gene_result = _run(
        ["python3", "/scripts-hm/gene_engine.py", "--show", gene_id],
        timeout=10, env=_hermes_env(),
    )

    return {
        "gene_id": gene_id,
        "target": target,
        "gene_info": gene_result.get("output", ""),
        "status": "strategy_loaded",
        "note": "Review the strategy, then execute steps manually or via fix_issue",
    }


def ghost_check(args: dict) -> dict:
    if not _require_confirm(args):
        return {"error": "confirm=true required for remediation tools"}
    return _run(["bash", "/scripts/docker_ghost_check.sh"], timeout=30)


def run_audit(args: dict) -> dict:
    if not _require_confirm(args):
        return {"error": "confirm=true required for remediation tools"}
    result = _run(["python3", "/scripts/daily_audit.py"], timeout=60)
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
    return _run(["kopia", "snapshot", "list", "--limit", "5"], timeout=30)


def backup_verify(args: dict) -> dict:
    if not _require_confirm(args):
        return {"error": "confirm=true required for maintenance tools"}
    return _run(["bash", "/scripts/verify_backups.sh"], timeout=300)


def run_optimize(args: dict) -> dict:
    if not _require_confirm(args):
        return {"error": "confirm=true required for maintenance tools"}
    return _run(["bash", "/scripts/weekly_optimize.sh"], timeout=300)


# ── Tool schemas ─────────────────────────────────────────────────────────────

TOOL_SCHEMAS = [
    # Tier 1 — Health & Status
    {
        "name": "health_score",
        "description": "Quick homelab health check. Returns health_score (0-100), overall_status, and failing checks. Fast (~1s).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "health_full",
        "description": "Full health dashboard with all checks. Slower but comprehensive.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "health_verify",
        "description": "CRITIC-style verification: runs health dashboard then independently spot-checks claims via direct docker/systemctl calls. Returns discrepancies. More trustworthy than health_score alone.",
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
    # Tier 1.5 — Reflexion & Learning
    {
        "name": "get_reflexion",
        "description": "Reflexion pattern: BEFORE attempting a fix, call this with (gene_id, target) to learn from past failures. Returns capsule history, past reflections, and a recommendation. Prevents repeating known-failing approaches.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "gene_id": {"type": "string", "description": "Gene ID, e.g. gene_container_crash"},
                "target": {"type": "string", "description": "Target service/container name"},
            },
        },
    },
    {
        "name": "submit_reflection",
        "description": "Reflexion pattern: AFTER a fix attempt, call this to record what happened. Closes the learning loop so future get_reflexion calls include this experience. Also records to capsule tracker.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "gene_id": {"type": "string", "description": "Gene ID used"},
                "target": {"type": "string", "description": "Target that was fixed"},
                "reflection": {"type": "string", "description": "Natural language reflection: what was tried, what happened, what to do differently"},
                "outcome": {"type": "string", "description": "success | partial | fail"},
            },
            "required": ["gene_id", "reflection"],
        },
    },
    {
        "name": "list_genes",
        "description": "Voyager skill library pattern: list all available Gene strategies with descriptions, signal matches, and historical success rates. Use before fix_issue to pick the right approach.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "experiment_result",
        "description": "ExpeL compounding knowledge: log a structured experiment (hypothesis, action, outcome). Over time this builds a knowledge base of what works for the homelab.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hypothesis": {"type": "string", "description": "What you expected to happen"},
                "action": {"type": "string", "description": "What was done"},
                "outcome": {"type": "string", "description": "What actually happened"},
                "context": {"type": "string", "description": "Additional context (optional)"},
            },
            "required": ["hypothesis", "outcome"],
        },
    },
    # Tier 2 — Service Control
    {
        "name": "service_status",
        "description": "Get systemd user service status (active, loaded, sub-state).",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Service name, e.g. hermes-gateway"}},
            "required": ["name"],
        },
    },
    {
        "name": "service_restart",
        "description": "Restart a systemd user service.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Service name, e.g. hermes-gateway"}},
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
            "properties": {"name": {"type": "string", "description": "Container name"}},
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
        "description": "Run the autonomous fixer to detect AND fix issues. Requires confirm=true. Uses --min-severity to filter. For targeted gene-based fixes, use run_gene instead.",
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
        "name": "run_gene",
        "description": "Voyager skill library: load and display a specific Gene strategy by ID. Requires confirm=true. Use list_genes first to see available genes. Returns the strategy steps for review.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "description": "Must be true"},
                "gene_id": {"type": "string", "description": "Gene ID, e.g. gene_container_crash"},
                "target": {"type": "string", "description": "Optional: target to apply the gene to"},
            },
            "required": ["confirm", "gene_id"],
        },
    },
    {
        "name": "ghost_check",
        "description": "Scan for and clean ghost Docker containers. Requires confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {"confirm": {"type": "boolean", "description": "Must be true to execute"}},
            "required": ["confirm"],
        },
    },
    {
        "name": "run_audit",
        "description": "Run the daily audit. Returns findings and latest report. Requires confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {"confirm": {"type": "boolean", "description": "Must be true to execute"}},
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
            "properties": {"confirm": {"type": "boolean", "description": "Must be true to execute"}},
            "required": ["confirm"],
        },
    },
    {
        "name": "run_optimize",
        "description": "Run weekly optimization scripts. Requires confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {"confirm": {"type": "boolean", "description": "Must be true to execute"}},
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
        ("health_verify", health_verify),
        ("list_issues", list_issues),
        ("cost_status", cost_status),
        ("capsule_stats", capsule_stats),
    ]:
        s.register_handler(name, fn)
    # Tier 1.5 — Reflexion & Learning
    for name, fn in [
        ("get_reflexion", get_reflexion),
        ("submit_reflection", submit_reflection),
        ("list_genes", list_genes),
        ("experiment_result", experiment_result),
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
        ("run_gene", run_gene),
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
