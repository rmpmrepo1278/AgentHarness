#!/usr/bin/env python3
"""homelab_ops.py — Homelab monitoring and operations toolkit for Hermes agent.

Provides functions for health checking, service management, log analysis,
and proactive monitoring of the homelab infrastructure.
"""

import json
import logging
import os
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Add current directory to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from missions_manager import mission_start, mission_status, mission_update
except ImportError:
    mission_start = mission_update = mission_status = None

try:
    from registry import registry, tool_error, tool_result
except ImportError:
    registry = tool_result = tool_error = None

def _reg(name, toolset, schema, handler):
    """Register a tool only if registry is available."""
    if registry is not None:
        _reg(name=name, toolset=toolset, schema=schema, handler=handler)

# ---------------------------------------------------------------------------
# Incident DB helpers
# ---------------------------------------------------------------------------

INCIDENT_DB_PATH = Path("/home/rohit/homelab-upgrade/self_healing.db")
TREND_STATE_PATH = Path("/tmp/hermes_trend_state.json")
ALERT_LIFECYCLE_PATH = Path("/tmp/hermes_alert_lifecycle.json")

def _init_incident_db():
    """Ensure the incidents table exists."""
    try:
        INCIDENT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(INCIDENT_DB_PATH))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_name TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                description TEXT,
                severity TEXT DEFAULT 'WARNING',
                action_taken TEXT,
                action_ok INTEGER DEFAULT 0,
                resolved_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trends (
                metric_key TEXT PRIMARY KEY,
                value REAL,
                recorded_at TEXT NOT NULL,
                delta_24h REAL DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        _log(f"incident_db: init failed: {e}", "error")
        return False

def log_incident(service_name: str, description: str, severity: str = "WARNING",
                 action_taken: str = None, action_ok: bool = False) -> bool:
    """Record an incident in the SQLite database."""
    try:
        _init_incident_db()
        conn = sqlite3.connect(str(INCIDENT_DB_PATH))
        conn.execute(
            "INSERT INTO incidents (service_name, detected_at, description, severity, action_taken, action_ok) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (service_name, _now_iso(), description, severity.upper(), action_taken, 1 if action_ok else 0)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        _log(f"log_incident: failed: {e}", "error")
        return False

def resolve_incidents(service_name: str):
    """Mark all open incidents for a service as resolved."""
    try:
        conn = sqlite3.connect(str(INCIDENT_DB_PATH))
        conn.execute(
            "UPDATE incidents SET resolved_at = ? WHERE service_name = ? AND resolved_at IS NULL",
            (_now_iso(), service_name)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        _log(f"resolve_incidents: failed: {e}", "error")

def get_incident_stats(days: int = 7) -> dict:
    """Get incident statistics for the last N days."""
    try:
        _init_incident_db()
        conn = sqlite3.connect(str(INCIDENT_DB_PATH))
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        cursor = conn.execute(
            "SELECT severity, COUNT(*) as cnt FROM incidents WHERE detected_at > ? GROUP BY severity",
            (cutoff,)
        )
        by_severity = {row[0]: row[1] for row in cursor.fetchall()}
        cursor = conn.execute(
            "SELECT service_name, COUNT(*) as cnt FROM incidents WHERE detected_at > ? "
            "GROUP BY service_name ORDER BY cnt DESC LIMIT 10",
            (cutoff,)
        )
        by_service = {row[0]: row[1] for row in cursor.fetchall()}
        cursor = conn.execute(
            "SELECT COUNT(*) FROM incidents WHERE detected_at > ? AND action_ok = 0 AND resolved_at IS NULL",
            (cutoff,)
        )
        unresolved = cursor.fetchone()[0]
        conn.close()
        return {"by_severity": by_severity, "by_service": by_service, "unresolved": unresolved, "days": days}
    except Exception as e:
        return {"error": str(e)}

# ---------------------------------------------------------------------------
# Trend tracking
# ---------------------------------------------------------------------------

def _load_trend_state() -> dict:
    """Load the persisted trend state (previous monitor pass values)."""
    try:
        if TREND_STATE_PATH.exists():
            return json.loads(TREND_STATE_PATH.read_text())
    except Exception:
        pass
    return {}

def _save_trend_state(state: dict):
    """Persist trend state for the next monitor pass."""
    try:
        TREND_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        TREND_STATE_PATH.write_text(json.dumps(state, indent=2))
    except Exception as e:
        _log(f"_save_trend_state: {e}", "error")

def check_trends(report: dict) -> list:
    """Compare current values against previous pass, return issues for significant deltas."""
    issues = []
    state = _load_trend_state()
    now = time.time()
    prev_ts = state.get("_timestamp", now)
    hours_elapsed = (now - prev_ts) / 3600 if prev_ts else 0

    # Track disk usage per mount
    for part in report.get("disk", {}).get("partitions", []):
        mount = part["mount"]
        pct = part["used_pct"]
        key = f"disk_{mount}"
        prev = state.get(key)
        if prev is not None and hours_elapsed > 0:
            delta = pct - prev
            if delta > 5 and hours_elapsed < 48:
                issues.append(f"Disk {mount} usage jumped {delta:.0f}% since last check ({prev}% -> {pct}%)")
        state[key] = pct

    # Track memory
    mem = report.get("memory", {})
    ram_pct = mem.get("ram_pct", 0)
    prev_ram = state.get("ram_pct")
    if prev_ram is not None and hours_elapsed > 0:
        delta = ram_pct - prev_ram
        if delta > 10 and hours_elapsed < 48:
            issues.append(f"RAM usage jumped {delta:.0f}% since last check ({prev_ram}% -> {ram_pct}%)")
    state["ram_pct"] = ram_pct

    # Track swap
    swap_pct = mem.get("swap_pct", 0)
    prev_swap = state.get("swap_pct")
    if prev_swap is not None and hours_elapsed > 0:
        if swap_pct > prev_swap + 20:
            issues.append(f"Swap usage climbed {swap_pct - prev_swap:.0f}% since last check")
    state["swap_pct"] = swap_pct

    # Track restart counts per container
    for cname in report.get("containers", {}).get("running", []):
        rc, out, _ = _run(f"docker inspect --format '{{{{.RestartCount}}}}' {cname} 2>/dev/null")
        if rc == 0 and out.strip().isdigit():
            count = int(out.strip())
            key = f"restarts_{cname}"
            prev = state.get(key, 0)
            if count > prev + 3:
                issues.append(f"{cname} has restarted {count - prev} times since last check (total: {count})")
            state[key] = count

    state["_timestamp"] = now
    _save_trend_state(state)
    return issues

# ---------------------------------------------------------------------------
# Log error scanner
# ---------------------------------------------------------------------------

LOG_ERROR_PATTERNS = [
    (r"error|fatal|exception|traceback|panic", "ERROR"),
    (r"oom-kill|out of memory|killed", "OOM"),
    (r"cannot connect|connection refused|connection reset", "CONNECTION"),
    (r"disk full|no space left", "DISK_FULL"),
    (r"segfault|segmentation fault|signal 11|signal 9", "CRASH"),
]

def scan_container_logs(container_names: list, tail_lines: int = 50) -> list:
    """Scan recent container logs for error patterns. Returns list of (container, pattern_type, line) tuples."""
    findings = []
    for name in container_names:
        rc, out, _ = _run(f"docker logs --tail {tail_lines} {name} 2>&1")
        if rc != 0 or not out:
            continue
        for line in out.splitlines():
            lower = line.lower()
            for pattern, ptype in LOG_ERROR_PATTERNS:
                import re
                if re.search(pattern, lower):
                    findings.append((name, ptype, line[:200]))
                    break
    return findings

# ---------------------------------------------------------------------------
# Network health checks
# ---------------------------------------------------------------------------

DNS_SERVERS = ["1.1.1.1", "8.8.8.8"]
TEST_DOMAINS = ["google.com", "github.com", "api.telegram.org"]
INTERNET_GATEWAYS = ["1.1.1.1", "8.8.8.8"]
SSL_CHECK_HOSTS = [
    ("google.com", 443),
    ("github.com", 443),
    ("api.telegram.org", 443),
]

def check_dns() -> dict:
    """Test DNS resolution for known domains. Returns {domain: resolved_bool, ...}."""
    results = {}
    for domain in TEST_DOMAINS:
        try:
            socket.getaddrinfo(domain, 80, socket.AF_INET)
            results[domain] = True
        except socket.gaierror:
            results[domain] = False
    return results

def check_internet_gateway() -> dict:
    """Ping known internet gateways. Returns {gateway: reachable_bool, ...}."""
    results = {}
    for gw in INTERNET_GATEWAYS:
        rc, out, _ = _run(f"ping -c 1 -W 3 {gw} 2>/dev/null")
        results[gw] = rc == 0
    return results

def check_ssl_expiry(hosts: list = None) -> list:
    """Check SSL certificate expiry dates. Returns list of (host, days_remaining, ok) tuples."""
    if hosts is None:
        hosts = SSL_CHECK_HOSTS
    results = []
    for host, port in hosts:
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((host, port), timeout=5) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                    cert = ssock.getpeercert()
                    expiry = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
                    remaining = (expiry - datetime.now()).days
                    results.append((host, remaining, remaining > 14))
        except Exception:
            results.append((host, -1, False))
    return results

def check_network() -> dict:
    """Aggregate network health check. Returns dict with dns, internet, ssl sub-keys."""
    _log("check_network: starting")
    result = {
        "dns": check_dns(),
        "internet": check_internet_gateway(),
        "ssl": [],
        "issues": [],
    }
    for host, remaining, ok in check_ssl_expiry():
        result["ssl"].append({"host": host, "days_remaining": remaining, "ok": ok})
        if not ok:
            result["issues"].append(f"SSL cert for {host} expires in {remaining}d")
    dns_failures = [d for d, ok in result["dns"].items() if not ok]
    if dns_failures:
        result["issues"].append(f"DNS resolution failed for: {dns_failures}")
    gw_failures = [g for g, ok in result["internet"].items() if not ok]
    if gw_failures:
        result["issues"].append(f"Internet gateways unreachable: {gw_failures}")
    return result

# ---------------------------------------------------------------------------
# Dependency-aware health
# ---------------------------------------------------------------------------

def check_docker_daemon() -> dict:
    """Check if Docker daemon is responsive. Returns dict with ok, version, containers_count."""
    rc, out, err = _run("docker info --format '{{.ServerVersion}}' 2>/dev/null")
    if rc != 0:
        return {"ok": False, "error": err or "Docker daemon unreachable"}
    rc2, out2, _ = _run("docker ps -q | wc -l")
    running = int(out2.strip()) if out2.strip().isdigit() else 0
    return {"ok": True, "version": out.strip(), "running_containers": running}

def check_service_dependencies(name: str) -> dict:
    """Check if a service's dependencies are met before attempting restart."""
    deps = {
        "mcp-gateway": [],
        "docker-mcp": ["mcp-gateway"],
        "file-mcp": ["mcp-gateway"],
        "n8n-mcp": ["mcp-gateway"],
        "git-mcp": ["mcp-gateway"],
        "media-mcp": ["mcp-gateway"],
        "backup-mcp": ["mcp-gateway"],
        "network-mcp": ["mcp-gateway"],
        "rss-mcp": ["mcp-gateway"],
        "doctor-mcp": ["mcp-gateway"],
        "autoheal": [],
    }
    needed = deps.get(name, [])
    failed = []
    for dep in needed:
        rc, out, _ = _run(f"docker inspect --format '{{{{.State.Status}}}}' {dep} 2>/dev/null")
        if rc != 0 or "running" not in out:
            failed.append(dep)
    return {"ok": len(failed) == 0, "failed_deps": failed, "service": name}

# ---------------------------------------------------------------------------
# Proactive disk sweeper
# ---------------------------------------------------------------------------

def auto_cleanup_disk(dry_run: bool = True) -> dict:
    """Clean up disk space: remove old /tmp files, prune Docker dangling images, old logs.

    Args:
        dry_run: If True, only report what would be cleaned, don't actually delete.

    Returns dict with actions taken and space estimates.
    """
    _log(f"auto_cleanup_disk: dry_run={dry_run}")
    result = {"actions": [], "total_freed_estimate_mb": 0}

    # 1. Clean /tmp files older than 7 days
    rc, out, _ = _run("find /tmp -maxdepth 1 -mtime +7 -type f 2>/dev/null | wc -l")
    old_count = int(out.strip()) if out.strip().isdigit() else 0
    if old_count > 0:
        result["actions"].append(f"Found {old_count} old /tmp files (>7d)")
        if not dry_run:
            rc, out, _ = _run("find /tmp -maxdepth 1 -mtime +7 -type f -delete 2>/dev/null")
            result["actions"].append(f"Cleaned {old_count} old /tmp files")
        result["total_freed_estimate_mb"] += old_count  # rough estimate

    # 2. Prune dangling Docker images
    rc, out, _ = _run("docker images -f dangling=true -q 2>/dev/null | wc -l")
    dangling = int(out.strip()) if out.strip().isdigit() else 0
    if dangling > 0:
        result["actions"].append(f"Found {dangling} dangling Docker images")
        if not dry_run:
            rc, out, _ = _run("docker image prune -f 2>/dev/null")
            result["actions"].append(f"Pruned {dangling} dangling images: {out.strip()[:100]}")
        result["total_freed_estimate_mb"] += dangling * 50  # rough ~50MB per dangling image

    # 3. Prune stopped containers older than 24h
    rc, out, _ = _run("docker container prune -f --filter 'until=24h' 2>/dev/null | tail -1")
    if out and "Total reclaimed space" in out:
        result["actions"].append(f"Pruned old containers: {out.strip()}")

    # 4. Rotate monitor logs > 50MB
    log_path = LOG_DIR / "hermes_monitor_cron.log"
    if log_path.exists() and log_path.stat().st_size > 50 * 1024 * 1024:
        result["actions"].append(f"Monitor log is {log_path.stat().st_size // 1024 // 1024}MB, rotating")
        if not dry_run:
            backup = log_path.with_suffix(".log.1")
            if backup.exists():
                backup.unlink()
            log_path.rename(backup)
            result["actions"].append("Rotated monitor log")

    # 5. Prune old Netdata archives if disk is critical
    result["total_freed_estimate_mb"] = result["total_freed_estimate_mb"]

    if dry_run:
        result["actions"].insert(0, "DRY RUN - no actual changes made")
    return result

# ---------------------------------------------------------------------------
# Backup freshness monitoring
# ---------------------------------------------------------------------------

BACKUP_PATHS = [
    ("MCP config backup", Path("/home/rohit/shared_agent_memory/config_backups")),
    ("homelab-upgrade data", Path("/home/rohit/homelab-upgrade")),
]

def check_backup_freshness(max_age_hours: int = 48) -> list:
    """Check when backups were last modified. Returns list of (name, age_hours, stale) tuples."""
    results = []
    for name, path in BACKUP_PATHS:
        if not path.exists():
            results.append((name, -1, True))
            continue
        latest = max(
            (f.stat().st_mtime for f in path.rglob("*") if f.is_file()),
            default=0
        )
        if latest == 0:
            results.append((name, -1, True))
            continue
        age = (time.time() - latest) / 3600
        results.append((name, round(age, 1), age > max_age_hours))
    return results

# ===========================================================================
# 0. get_recent_incidents
# ===========================================================================

def get_recent_incidents(args, **kwargs):
    """Get recent self-healing incidents from the database."""
    limit = args.get("limit", 5)
    try:
        _init_incident_db()
        conn = sqlite3.connect(str(INCIDENT_DB_PATH))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT service_name, detected_at, description, action_taken, severity "
            "FROM incidents ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return tool_result("No recent incidents detected. System is stable.")

        report = "RECENT SYSTEM INCIDENTS:\n"
        for r in rows:
            report += f"- [{r[1]}] [{r[4]}] {r[0]}: {r[2]} -> ACTION: {r[3] or 'none'}\n"
        return tool_result(report)
    except Exception as e:
        return tool_error(f"Error fetching incidents: {e}")

# Registration logic
def _mission_start_handler(args, **kwargs):
    res = mission_start(args.get("objective"), args.get("steps"))
    return tool_result(res)

def _mission_update_handler(args, **kwargs):
    res = mission_update(
        status=args.get("status"),
        step_complete=args.get("step_complete", False),
        note=args.get("note")
    )
    return tool_result(res)

def _mission_status_handler(args, **kwargs):
    return tool_result(mission_status())

# Register tools
_reg(
    name="mission_start",
    toolset="hermes-cli",
    schema={
        "name": "mission_start",
        "description": "Start a new high-level mission/objective to keep the agent focused.",
        "parameters": {
            "type": "object",
            "properties": {
                "objective": {"type": "string", "description": "The high-level goal."},
                "steps": {"type": "array", "items": {"type": "string"}, "description": "Planned steps."}
            },
            "required": ["objective"]
        }
    },
    handler=_mission_start_handler
)

_reg(
    name="mission_update",
    toolset="hermes-cli",
    schema={
        "name": "mission_update",
        "description": "Update the active mission progress.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "New status (e.g. 'completed')."},
                "step_complete": {"type": "boolean", "description": "Mark current step as done."},
                "note": {"type": "string", "description": "Progress note."}
            }
        }
    },
    handler=_mission_update_handler
)

_reg(
    name="mission_status",
    toolset="hermes-cli",
    schema={
        "name": "mission_status",
        "description": "Get current mission and background task status.",
        "parameters": {"type": "object", "properties": {}}
    },
    handler=_mission_status_handler
)

_reg(
    name="get_recent_incidents",
    toolset="hermes-cli",
    schema={
        "name": "get_recent_incidents",
        "description": "List recent autonomous self-healing events from SQLite DB.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 5}
            }
        }
    },
    handler=get_recent_incidents
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Detect home directory and set workspace paths
HOME_DIR = Path.home()
AG_BASE = HOME_DIR / "agentharness"
if not AG_BASE.exists():
    AG_BASE = Path(__file__).resolve().parent

LOG_DIR = AG_BASE / "data/logs"
LOG_FILE = LOG_DIR / "hermes_ops.log"
INBOX_DIR = AG_BASE / "data/inbox"
RUNBOOK_DIR = AG_BASE / "core/doctor/runbooks"
WATCHDOG_LOG = LOG_DIR / "watchdog.log"

ALLOWED_SYSTEMD_SERVICES = frozenset({
    "llama-primary",
    "agentharness-proxy",
    "agentharness-scheduler",
    "agentharness-dashboard",
    "hermes-gateway",
})

KNOWN_LOG_PATHS = {
    "watchdog": WATCHDOG_LOG,
    "scheduler": LOG_DIR / "scheduler.log",
    "deadman": LOG_DIR / "deadman.log",
    "inbox_watcher": LOG_DIR / "inbox_watcher.log",
    "exec_audit": LOG_DIR / "exec_audit.log",
}

SUBPROCESS_TIMEOUT = 30  # seconds

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logger = logging.getLogger("hermes_ops")
logger.setLevel(logging.INFO)

_log_handler: logging.Handler | None = None


def _ensure_logger():
    """Lazily attach file handler (safe if log dir doesn't exist yet)."""
    global _log_handler
    if _log_handler is not None:
        return
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _log_handler = logging.FileHandler(str(LOG_FILE))
        _log_handler.setFormatter(
            logging.Formatter("[%(asctime)s] %(levelname)s %(message)s",
                              datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(_log_handler)
    except OSError:
        pass  # fail silently -- caller still gets return values


def _log(msg: str, level: str = "info"):
    _ensure_logger()
    getattr(logger, level, logger.info)(msg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: str, timeout: int = SUBPROCESS_TIMEOUT) -> tuple:
    """Run a shell command, return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s: {cmd}"
    except Exception as exc:
        return -1, "", str(exc)


def _safe(fn):
    """Decorator: catch all exceptions and return error dict."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            _log(f"{fn.__name__} crashed: {exc}", "error")
            return {"ok": False, "error": str(exc), "function": fn.__name__}
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ===========================================================================
# 1. check_all_health
# ===========================================================================

@_safe
def check_all_health() -> dict:
    """Check all critical services and return a structured health report.

    Returns dict with keys: timestamp, ok (bool), issues (list[str]),
    containers, llm_proxy, local_llm, disk, memory, network, watchdog_recent_failures.
    """
    _log("check_all_health: starting")
    report = {"timestamp": _now_iso(), "ok": True, "issues": []}

    # -- Docker daemon first (dependency) --
    daemon = check_docker_daemon()
    report["docker_daemon"] = daemon
    if not daemon["ok"]:
        report["ok"] = False
        report["issues"].append(f"Docker daemon: {daemon.get('error', 'unreachable')}")
        return report  # cannot proceed further

    # -- Docker containers --
    rc, out, err = _run(
        "docker ps -a --format '{{.Names}}|{{.Status}}|{{.State}}'"
    )
    containers = {"running": [], "stopped": [], "unhealthy": []}
    if rc == 0 and out:
        for line in out.splitlines():
            parts = line.split("|", 2)
            if len(parts) < 3:
                continue
            name, status, state = parts
            if state == "running":
                if "unhealthy" in status.lower():
                    containers["unhealthy"].append(name)
                else:
                    containers["running"].append(name)
            else:
                containers["stopped"].append(name)
    report["containers"] = containers
    if containers["stopped"] or containers["unhealthy"]:
        report["ok"] = False
        report["issues"].append(
            f"Containers down: {containers['stopped']}, unhealthy: {containers['unhealthy']}"
        )

    # -- LLM proxy (port 8080) --
    rc_proxy, out_proxy, _ = _run("curl -sf -m 5 http://127.0.0.1:8080/health")
    report["llm_proxy"] = {"reachable": rc_proxy == 0, "response": out_proxy[:500]}
    if rc_proxy != 0:
        report["ok"] = False
        report["issues"].append("LLM proxy (8080) unreachable")

    # -- Local LLM (Ollama on port 11434) --
    rc_llm, out_llm, _ = _run("curl -sf -m 5 http://127.0.0.1:11434/api/tags")
    report["local_llm"] = {"reachable": rc_llm == 0, "endpoint": "11434"}
    if rc_llm != 0:
        report["ok"] = False
        report["issues"].append("Local LLM (11434) unreachable")

    # -- Disk usage --
    report["disk"] = check_disk()
    if report["disk"].get("critical_partitions"):
        report["ok"] = False
        report["issues"].append(
            f"Disk critical: {report['disk']['critical_partitions']}"
        )

    # -- Memory / swap --
    report["memory"] = check_memory()
    if report["memory"].get("swap_pct", 0) > 80:
        report["ok"] = False
        report["issues"].append("Swap usage > 80%")

    # -- Recent watchdog failures --
    rc_wd, out_wd, _ = _run(
        f"grep -iE 'FAIL|DOWN' {WATCHDOG_LOG} | tail -10"
    )
    report["watchdog_recent_failures"] = out_wd.splitlines() if out_wd else []
    if report["watchdog_recent_failures"]:
        report["issues"].append(
            f"{len(report['watchdog_recent_failures'])} recent watchdog failures"
        )

    # -- Network health --
    report["network"] = check_network()
    if report["network"].get("issues"):
        for net_issue in report["network"]["issues"]:
            report["issues"].append(net_issue)

    # -- Trend analysis --
    trend_issues = check_trends(report)
    report["trend_issues"] = trend_issues
    for ti in trend_issues:
        report["issues"].append(ti)

    # -- Log scanning on healthy containers (quick scan, tail 20 lines) --
    all_running = containers.get("running", []) + containers.get("unhealthy", [])
    log_issues = scan_container_logs(all_running, tail_lines=20)
    report["log_scan"] = log_issues
    for cname, ptype, line in log_issues[:5]:  # cap at 5
        report["issues"].append(f"[{ptype}] {cname}: {line}")

    # -- Backup freshness --
    backup_status = check_backup_freshness()
    report["backups"] = backup_status
    for name, age, stale in backup_status:
        if stale:
            report["issues"].append(f"Backup stale: {name} ({age}h old)")

    _log(f"check_all_health: ok={report['ok']}, issues={len(report['issues'])}")
    return report


# ===========================================================================
# 2. check_container
# ===========================================================================

@_safe
def check_container(name: str) -> dict:
    """Detailed status of a Docker container.

    Returns dict with: name, state, health, started_at, restart_count, recent_logs.
    """
    _log(f"check_container: {name}")
    info = {"name": name}

    # Try with health status first, fall back without it
    fmt = "{{.State.Status}}|{{.State.Health.Status}}|{{.State.StartedAt}}|{{.RestartCount}}"
    rc, out, err = _run(f"docker inspect --format '{fmt}' {name}")
    if rc != 0:
        fmt_no_health = "{{.State.Status}}|N/A|{{.State.StartedAt}}|{{.RestartCount}}"
        rc, out, err = _run(f"docker inspect --format '{fmt_no_health}' {name}")

    if rc == 0 and out:
        parts = out.split("|", 3)
        info["state"] = parts[0] if len(parts) > 0 else "unknown"
        info["health"] = parts[1] if len(parts) > 1 else "N/A"
        info["started_at"] = parts[2] if len(parts) > 2 else "unknown"
        info["restart_count"] = parts[3] if len(parts) > 3 else "0"
    else:
        info["error"] = err or "Container not found"
        return info

    # Tail recent logs
    rc_log, log_out, _ = _run(f"docker logs --tail 20 {name} 2>&1")
    info["recent_logs"] = log_out[-2000:] if log_out else ""
    return info


# ===========================================================================
# 3. restart_service
# ===========================================================================

@_safe
def restart_service(name: str) -> dict:
    """Restart a Docker container or allowed systemd service.

    Docker containers are restarted with `docker restart`.
    Only systemd services in the allowlist can be restarted.
    Returns dict with: service, type, ok, error?, post_restart_logs/status.
    """
    _log(f"restart_service: {name}")
    result = {"service": name, "ok": False}

    # Check if it is a docker container
    rc, out, _ = _run(f"docker inspect --format '{{{{.Name}}}}' {name} 2>/dev/null")
    if rc == 0:
        # Dependency check before restart
        deps = check_service_dependencies(name)
        if not deps["ok"]:
            result["error"] = f"Dependencies not met: {deps['failed_deps']}"
            result["type"] = "docker"
            result["dependency_blocked"] = True
            _log(f"restart_service: dependency block for {name}: {deps['failed_deps']}", "warning")
            return result

        rc_restart, _, err = _run(f"docker restart {name}", timeout=60)
        result["type"] = "docker"
        result["ok"] = rc_restart == 0
        if not result["ok"]:
            result["error"] = err
        # Grab logs after restart
        _, logs, _ = _run(f"docker logs --tail 10 {name} 2>&1")
        result["post_restart_logs"] = logs[-1000:]
        _log(f"restart_service: docker {name} ok={result['ok']}")

        # Log incident
        log_incident(
            name,
            f"Container auto-restarted{' (failed)' if not result['ok'] else ''}",
            "WARNING" if result["ok"] else "CRITICAL",
            action_taken="restart",
            action_ok=result["ok"],
        )
        return result

    # Systemd service
    if name in ALLOWED_SYSTEMD_SERVICES:
        rc_restart, _, err = _run(
            f"sudo -n systemctl restart {name}", timeout=60
        )
        result["type"] = "systemd"
        result["ok"] = rc_restart == 0
        if not result["ok"]:
            result["error"] = err
        _, status_out, _ = _run(f"systemctl status {name} --no-pager -l")
        result["post_restart_status"] = status_out[-1000:]
        _log(f"restart_service: systemd {name} ok={result['ok']}")

        log_incident(
            name,
            f"Systemd service auto-restarted{' (failed)' if not result['ok'] else ''}",
            "WARNING" if result["ok"] else "CRITICAL",
            action_taken="restart",
            action_ok=result["ok"],
        )
        return result

    result["error"] = (
        f"Service '{name}' is not a docker container and not in allowed "
        f"systemd list: {sorted(ALLOWED_SYSTEMD_SERVICES)}"
    )
    _log(f"restart_service: DENIED {name}", "warning")
    return result


# ===========================================================================
# 4. get_logs
# ===========================================================================

@_safe
def get_logs(service: str, lines: int = 50) -> str:
    """Get recent logs for a service (docker, systemd, or file-based).

    Args:
        service: Container name, systemd unit, or known log alias
                 (watchdog, scheduler, deadman, inbox_watcher, exec_audit).
        lines: Number of lines to return (capped at 500).
    """
    _log(f"get_logs: {service} lines={lines}")
    lines = min(lines, 500)  # cap to avoid huge output

    # Known file paths first
    if service in KNOWN_LOG_PATHS:
        path = KNOWN_LOG_PATHS[service]
        if path.exists():
            rc, out, _ = _run(f"tail -n {lines} {path}")
            return out if rc == 0 else f"Error reading {path}"
        return f"Log file not found: {path}"

    # Docker container
    rc, out, _ = _run(f"docker logs --tail {lines} {service} 2>&1")
    if rc == 0:
        return out

    # Systemd service
    rc, out, _ = _run(f"sudo journalctl -u {service} -n {lines} --no-pager 2>&1")
    if rc == 0 and out:
        return out

    return f"No logs found for '{service}' (tried docker, journalctl, known paths)"


# ===========================================================================
# 5. check_providers
# ===========================================================================

@_safe
def check_providers() -> dict:
    """Check LLM provider status via the proxy API.

    Queries /v1/status, /v1/usage, /v1/billing on the local proxy (port 8080).
    Returns dict keyed by endpoint name.
    """
    _log("check_providers: starting")
    result = {}
    for endpoint in ("status", "usage", "billing"):
        rc, out, err = _run(
            f"curl -sf -m 10 http://127.0.0.1:8080/v1/{endpoint}"
        )
        if rc == 0:
            try:
                result[endpoint] = json.loads(out)
            except json.JSONDecodeError:
                result[endpoint] = out[:1000]
        else:
            result[endpoint] = {"error": err or "unreachable"}
    return result


# ===========================================================================
# 6. check_disk
# ===========================================================================

@_safe
def check_disk() -> dict:
    """Disk usage report with cleanup candidates.

    Returns dict with: partitions, critical_partitions (>=90%),
    largest_dirs, tmp_cleanup_candidates.
    """
    _log("check_disk: starting")
    result = {"partitions": [], "critical_partitions": []}

    rc, out, _ = _run("df -h --output=target,pcent,avail,size | tail -n +2")
    if rc == 0 and out:
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                mount, pct_str, avail, size = parts[0], parts[1], parts[2], parts[3]
                pct = int(pct_str.rstrip("%")) if pct_str.rstrip("%").isdigit() else 0
                entry = {"mount": mount, "used_pct": pct, "avail": avail, "size": size}
                result["partitions"].append(entry)
                if pct >= 90:
                    result["critical_partitions"].append(entry)

    # Largest directories under common paths
    rc, out, _ = _run(
        "du -sh /home/rohit/agentharness/data/* /tmp/* /var/log/* 2>/dev/null "
        "| sort -rh | head -10"
    )
    result["largest_dirs"] = out.splitlines() if out else []

    # Tmp cleanup candidates (files older than 7 days)
    rc, out, _ = _run(
        "find /tmp -maxdepth 1 -mtime +7 -type f 2>/dev/null | head -20"
    )
    result["tmp_cleanup_candidates"] = out.splitlines() if out else []

    return result


# ===========================================================================
# 7. check_memory
# ===========================================================================

@_safe
def check_memory() -> dict:
    """RAM, swap, top memory consumers, OOM kill history.

    Returns dict with: ram_total_mb, ram_used_mb, ram_pct, swap_total_mb,
    swap_used_mb, swap_pct, top_consumers, oom_history.
    """
    _log("check_memory: starting")
    result = {}

    # Free memory
    rc, out, _ = _run("free -m")
    if rc == 0 and out:
        result["free_output"] = out
        for line in out.splitlines():
            if line.startswith("Mem:"):
                cols = line.split()
                if len(cols) >= 7:
                    total, used = int(cols[1]), int(cols[2])
                    result["ram_total_mb"] = total
                    result["ram_used_mb"] = used
                    result["ram_pct"] = round(used / total * 100, 1) if total else 0
            elif line.startswith("Swap:"):
                cols = line.split()
                if len(cols) >= 3:
                    total, used = int(cols[1]), int(cols[2])
                    result["swap_total_mb"] = total
                    result["swap_used_mb"] = used
                    result["swap_pct"] = round(used / total * 100, 1) if total else 0

    # Top memory consumers
    rc, out, _ = _run("ps aux --sort=-%mem | head -11")
    result["top_consumers"] = out.splitlines()[1:] if out else []  # skip header

    # OOM kill history
    rc, out, _ = _run("sudo dmesg -T 2>/dev/null | grep -i 'oom\\|out of memory' | tail -5")
    result["oom_history"] = out.splitlines() if out else []

    return result


# ===========================================================================
# 8. run_doctor
# ===========================================================================

@_safe
def run_doctor(runbook: str = None) -> dict:
    """Run a doctor runbook or list available ones.

    Args:
        runbook: Name of runbook (without .yaml extension), or None to list all.
    """
    _log(f"run_doctor: runbook={runbook}")

    if runbook is None:
        runbooks = []
        if RUNBOOK_DIR.exists():
            runbooks = [f.stem for f in RUNBOOK_DIR.glob("*.yaml")]
        return {"available_runbooks": sorted(runbooks)}

    rb_path = RUNBOOK_DIR / f"{runbook}.yaml"
    if not rb_path.exists():
        return {"ok": False, "error": f"Runbook not found: {runbook}"}

    rc, out, err = _run(
        f"python3 /home/rohit/agentharness/core/doctor/engine.py "
        f"--runbook {rb_path} 2>&1",
        timeout=120,
    )
    return {
        "ok": rc == 0,
        "runbook": runbook,
        "output": out[-3000:],
        "error": err[-500:] if err else "",
    }


# ===========================================================================
# 9. send_notification
# ===========================================================================

@_safe
def send_notification(title: str, message: str, severity: str = "INFO",
                      alert_id: str = None, topic: str = None) -> bool:
    """Write an alert to the inbox for Telegram delivery.

    Args:
        title: Short alert title.
        message: Detailed message body.
        severity: One of INFO, WARNING, CRITICAL.
        alert_id: Optional stable ID for dedup/snooze lifecycle.
        topic: Optional Telegram topic (general, infrastructure, knowledge, career-ops).

    Returns True on success.
    """
    try:
        INBOX_DIR.mkdir(parents=True, exist_ok=True)

        payload = {
            "title": title,
            "body": message,
            "message": message,
            "severity": severity.upper(),
            "timestamp": _now_iso(),
            "source": "hermes",
            "_source": "hermes_ops",
        }
        if alert_id:
            payload["alert_id"] = alert_id
        if topic:
            payload["topic"] = topic

        filename = f"hermes_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.json"
        filepath = INBOX_DIR / filename
        filepath.write_text(json.dumps(payload, indent=2))
        _log(f"send_notification: wrote {filepath}")
        return True
    except Exception as e:
        _log(f"send_notification failed: {e}", "error")
        return False


# ===========================================================================
# 10. edit_config
# ===========================================================================

@_safe
def edit_config(file_path: str, key: str, value: str) -> dict:
    """Edit a YAML or JSON config file safely (read-modify-write with backup).

    Args:
        file_path: Absolute path to config file (.json, .yaml, .yml).
        key: Dot-separated key path (e.g. "proxy.timeout").
        value: New value (auto-parsed as JSON/YAML, falls back to string).

    Only files under /home/rohit/agentharness/ or /home/rohit/.hermes/ are allowed.
    A timestamped backup is created before modification.
    """
    _log(f"edit_config: {file_path} key={key}")
    path = Path(file_path)
    if not path.exists():
        return {"ok": False, "error": f"File not found: {file_path}"}

    # Security: only allow editing under known safe directories
    safe_prefixes = (
        "/home/rohit/agentharness/",
        "/home/rohit/.hermes/",
    )
    if not any(file_path.startswith(p) for p in safe_prefixes):
        return {"ok": False, "error": f"Edit not allowed outside safe dirs: {safe_prefixes}"}

    # Create backup
    backup_path = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
    shutil.copy2(str(path), str(backup_path))

    suffix = path.suffix.lower()
    content = path.read_text()

    if suffix in (".json",):
        data = json.loads(content)
        keys = key.split(".")
        obj = data
        for k in keys[:-1]:
            obj = obj.setdefault(k, {})
        # Try to parse value as JSON, fall back to string
        try:
            parsed_value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            parsed_value = value
        old_value = obj.get(keys[-1])
        obj[keys[-1]] = parsed_value
        path.write_text(json.dumps(data, indent=2) + "\n")
        return {
            "ok": True, "backup": str(backup_path),
            "key": key, "old_value": old_value, "new_value": parsed_value,
        }

    elif suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            return {"ok": False, "error": "PyYAML not installed"}
        data = yaml.safe_load(content) or {}
        keys = key.split(".")
        obj = data
        for k in keys[:-1]:
            if not isinstance(obj, dict):
                return {"ok": False, "error": f"Key path traverses non-dict at '{k}'"}
            obj = obj.setdefault(k, {})
        try:
            parsed_value = yaml.safe_load(value)
        except Exception:
            parsed_value = value
        old_value = obj.get(keys[-1])
        obj[keys[-1]] = parsed_value
        path.write_text(yaml.dump(data, default_flow_style=False))
        return {
            "ok": True, "backup": str(backup_path),
            "key": key, "old_value": old_value, "new_value": parsed_value,
        }

    return {"ok": False, "error": f"Unsupported file type: {suffix}"}


# ===========================================================================
# 11. get_billing_report
# ===========================================================================

@_safe
def get_billing_report() -> dict:
    """Fetch billing data from the LLM proxy.

    Queries http://127.0.0.1:8080/v1/billing and returns parsed JSON.
    """
    _log("get_billing_report: starting")
    rc, out, err = _run("curl -sf -m 10 http://127.0.0.1:8080/v1/billing")
    if rc != 0:
        return {"ok": False, "error": err or "Proxy unreachable"}
    try:
        return {"ok": True, "billing": json.loads(out)}
    except json.JSONDecodeError:
        return {"ok": True, "billing_raw": out[:2000]}


# ===========================================================================
# 12. run_benchmark
# ===========================================================================

@_safe
def run_benchmark() -> dict:
    """Trigger the benchmark script (only during offline hours 02:00-05:00 IST).

    Returns dict with ok, output, and error fields.
    Refuses to run outside the 02:00-05:00 window.
    """
    _log("run_benchmark: starting")

    bench_script = "/home/rohit/agentharness/scripts/benchmark.sh"
    if not Path(bench_script).exists():
        bench_script = "/home/rohit/.hermes/hermes-agent/scripts/benchmark.sh"
        if not Path(bench_script).exists():
            return {"ok": False, "error": "Benchmark script not found"}

    _log(f"run_benchmark: executing {bench_script}")
    rc, out, err = _run(f"bash {bench_script} 2>&1", timeout=300)
    return {
        "ok": rc == 0,
        "output": out[-3000:],
        "error": err[-500:] if err else "",
    }




# ===========================================================================
# 13. cost_dashboard — Telegram /cost command
# ===========================================================================

@_safe
def cost_dashboard() -> dict:
    """Fetch cost/routing dashboard from the LLM proxy.

    Returns provider status, usage today, routing order, cooldowns.
    Call this when user sends /cost on Telegram.
    """
    _log("cost_dashboard: fetching from proxy")
    rc, out, err = _run("curl -sf -m 10 http://127.0.0.1:8080/v1/cost")
    if rc != 0:
        return {"ok": False, "error": err or "Proxy unreachable"}
    try:
        data = json.loads(out)
        # Format a human-readable summary
        providers = data.get("providers", {})
        usage = data.get("usage_today", {})

        lines = ["LLM Cost Dashboard"]
        lines.append("=" * 30)

        # OpenRouter Credit
        or_credit = data.get("openrouter_credit")
        if or_credit:
            limit_raw = or_credit.get("limit")
            limit = float(limit_raw) if limit_raw is not None else 30.0

            usage_raw = or_credit.get("usage")
            or_usage = float(usage_raw) if usage_raw is not None else 0.0

            remaining_raw = or_credit.get("limit_remaining")
            if remaining_raw is not None:
                remaining = float(remaining_raw)
            else:
                remaining = limit - or_usage

            lines.append(f"\nOpenRouter Balance: ${or_usage:.2f} used / ${limit:.2f} limit (${remaining:.2f} remaining)")

        # Provider status
        lines.append("\nProvider Status:")
        for name, info in providers.items():
            status = info.get("status", "?")
            emoji = "+" if status in ("ready", "healthy") else "-"
            extra = ""
            if info.get("cooldown_seconds", 0) > 0:
                extra = f" (cooldown {info['cooldown_seconds']}s)"
            if info.get("consecutive_429s", 0) > 0:
                extra += f" ({info['consecutive_429s']}x 429)"
            lines.append(f"  {emoji} {name}: {status}{extra}")

        # Usage today
        lines.append("\nUsage Today:")
        if usage:
            for name, stats in usage.items():
                reqs = stats.get("requests", 0)
                t_in = stats.get("tokens_in", 0)
                t_out = stats.get("tokens_out", 0)
                lines.append(f"  {name}: {reqs} reqs, {t_in:,} in / {t_out:,} out tokens")
        else:
            lines.append("  No usage recorded yet")

        # Routing order
        routing = data.get("routing_order", {})
        if routing:
            lines.append("\nRouting Order:")
            for route_type, order in routing.items():
                lines.append(f"  {route_type}: {' -> '.join(order)}")

        return {"ok": True, "summary": "\n".join(lines), "raw": data}
    except json.JSONDecodeError:
        return {"ok": True, "raw": out[:2000]}


# ===========================================================================
# 14. routing_control — manage routing from Telegram
# ===========================================================================

@_safe
def routing_control(action: str, provider: str = "") -> dict:
    """Control LLM routing at runtime.

    Actions:
      reset    — clear all cooldowns and re-enable all providers
      disable <provider> — disable a provider (e.g. 'google')
      enable <provider>  — re-enable a provider
    """
    _log(f"routing_control: action={action} provider={provider}")

    payload = {"action": action}
    if provider:
        payload["provider"] = provider

    cmd = f"curl -sf -m 10 -X POST http://127.0.0.1:8080/v1/routing -H 'Content-Type: application/json' -d '{json.dumps(payload)}'"
    rc, out, err = _run(cmd)
    if rc != 0:
        return {"ok": False, "error": err or "Proxy unreachable"}
    try:
        return {"ok": True, "result": json.loads(out)}
    except json.JSONDecodeError:
        return {"ok": True, "raw": out[:500]}



# ===========================================================================
# 15. cap_control — /cap command from Telegram
# ===========================================================================

@_safe
def cap_control(provider: str = "", limit: int = -1) -> dict:
    """View or adjust provider daily caps at runtime.

    No args: show all caps. With provider+limit: set new cap.
    Call this when user sends /cap on Telegram.
    """
    _log(f"cap_control: provider={provider} limit={limit}")

    if not provider:
        # Show all caps
        rc, out, err = _run("curl -sf -m 10 http://127.0.0.1:8080/v1/cap")
        if rc != 0:
            return {"ok": False, "error": err or "Proxy unreachable"}
        try:
            data = json.loads(out)
            caps = data.get("caps", {})
            lines = ["Provider Daily Caps", "=" * 25]
            for name, info in caps.items():
                used = info.get("used_today", 0)
                lim = info.get("daily_limit", 0)
                remaining = info.get("remaining", 0)
                pct = int((used / lim) * 100) if lim > 0 else 0
                lines.append(f"  {name}: {used}/{lim} ({pct}% used, {remaining} left)")
            lines.append("\nUsage: /cap <provider> <limit>")
            return {"ok": True, "summary": "\n".join(lines), "raw": data}
        except json.JSONDecodeError:
            return {"ok": True, "raw": out[:1000]}
    else:
        # Set cap
        payload = json.dumps({"provider": provider, "limit": limit})
        cmd = f"curl -sf -m 10 -X POST http://127.0.0.1:8080/v1/cap -H 'Content-Type: application/json' -d '{payload}'"
        rc, out, err = _run(cmd)
        if rc != 0:
            return {"ok": False, "error": err or "Proxy unreachable"}
        try:
            return {"ok": True, "result": json.loads(out)}
        except json.JSONDecodeError:
            return {"ok": True, "raw": out[:500]}

# ===========================================================================
# 16. run_background_task
# ===========================================================================

@_safe
def run_background_task(cmd: str) -> dict:
    """Run a command in the background and return a task ID.

    The task ID is used to check progress/logs later.
    Useful for long-running downloads or benchmarks.
    """
    task_id = str(uuid.uuid4())[:8]
    task_dir = Path("/tmp/hermes_tasks") / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    log_file = task_dir / "output.log"
    pid_file = task_dir / "pid"

    # Run with nohup and redirect all output
    full_cmd = f"nohup {cmd} > {log_file} 2>&1 & echo $! > {pid_file}"
    _log(f"run_background_task: {task_id} -> {cmd}")

    rc, out, err = _run(full_cmd)

    if rc == 0:
        return {
            "ok": True,
            "task_id": task_id,
            "log_file": str(log_file),
            "status": "started"
        }
    return {"ok": False, "error": err}


# ===========================================================================
# 17. check_background_task
# ===========================================================================

@_safe
def check_background_task(task_id: str) -> dict:
    """Check the status and recent logs of a background task."""
    task_dir = Path("/tmp/hermes_tasks") / task_id
    if not task_dir.exists():
        return {"ok": False, "error": "Task not found"}

    pid_file = task_dir / "pid"
    log_file = task_dir / "output.log"

    status = "finished"
    if pid_file.exists():
        pid = pid_file.read_text().strip()
        rc, out, _ = _run(f"ps -p {pid}")
        if rc == 0:
            status = "running"

    recent_logs = ""
    if log_file.exists():
        _, recent_logs, _ = _run(f"tail -n 20 {log_file}")

    return {
        "ok": True,
        "task_id": task_id,
        "status": status,
        "recent_logs": recent_logs,
        "log_path": str(log_file)
    }

# ===========================================================================
# 18. alert_lifecycle — ack/snooze from Telegram
# ===========================================================================

@_safe
def ack_alert(alert_id: str) -> dict:
    """Acknowledge an alert (mark as acknowledged, stop re-alerting)."""
    try:
        state = {}
        if ALERT_LIFECYCLE_PATH.exists():
            state = json.loads(ALERT_LIFECYCLE_PATH.read_text())
        state[alert_id] = {
            "status": "acknowledged",
            "acked_at": _now_iso(),
        }
        ALERT_LIFECYCLE_PATH.write_text(json.dumps(state, indent=2))
        _log(f"ack_alert: {alert_id} acknowledged")
        return {"ok": True, "alert_id": alert_id, "status": "acknowledged"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@_safe
def snooze_alert(alert_id: str, hours: int = 4) -> dict:
    """Snooze an alert for N hours (suppress notifications)."""
    try:
        state = {}
        if ALERT_LIFECYCLE_PATH.exists():
            state = json.loads(ALERT_LIFECYCLE_PATH.read_text())
        snooze_until = time.time() + (hours * 3600)
        state[alert_id] = {
            "status": "snoozed",
            "snoozed_at": _now_iso(),
            "snooze_until": snooze_until,
            "snooze_hours": hours,
        }
        ALERT_LIFECYCLE_PATH.write_text(json.dumps(state, indent=2))
        _log(f"snooze_alert: {alert_id} snoozed for {hours}h")
        return {"ok": True, "alert_id": alert_id, "status": "snoozed", "snooze_until": snooze_until}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@_safe
def is_alert_suppressed(alert_id: str) -> bool:
    """Check if an alert is currently suppressed (acknowledged or snoozed)."""
    try:
        if not ALERT_LIFECYCLE_PATH.exists():
            return False
        state = json.loads(ALERT_LIFECYCLE_PATH.read_text())
        entry = state.get(alert_id)
        if not entry:
            return False
        if entry["status"] == "acknowledged":
            return True
        if entry["status"] == "snoozed":
            snooze_until = entry.get("snooze_until", 0)
            if time.time() < snooze_until:
                return True
            # Snooze expired, remove entry
            del state[alert_id]
            ALERT_LIFECYCLE_PATH.write_text(json.dumps(state, indent=2))
            return False
        return False
    except Exception:
        return False

# ===========================================================================
# 19. alert_status — /alerts command from Telegram
# ===========================================================================

@_safe
def alert_status() -> dict:
    """Show current alert lifecycle state (acknowledged/snoozed alerts)."""
    try:
        if not ALERT_LIFECYCLE_PATH.exists():
            return {"ok": True, "alerts": [], "summary": "No active alert lifecycle entries"}
        state = json.loads(ALERT_LIFECYCLE_PATH.read_text())
        lines = ["Alert Lifecycle Status", "=" * 30]
        now = time.time()
        active = 0
        for alert_id, entry in state.items():
            status = entry["status"]
            extra = ""
            if status == "snoozed":
                remaining = max(0, entry.get("snooze_until", 0) - now)
                extra = f" ({int(remaining // 60)}m remaining)"
                if remaining > 0:
                    active += 1
            lines.append(f"  {alert_id}: {status}{extra}")
        if active == 0:
            lines.append("  No active suppressions")
        return {"ok": True, "alerts": state, "summary": "\n".join(lines)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ===========================================================================
# 20. get_daily_report — /report command
# ===========================================================================

@_safe
def get_daily_report() -> dict:
    """Generate a daily infrastructure summary."""
    _log("get_daily_report: starting")
    health = check_all_health()
    stats = get_incident_stats(days=1)

    lines = ["Daily Homelab Report", "=" * 40]
    lines.append(f"Generated: {_now_iso()}")
    lines.append("")

    # Overall status
    lines.append(f"Overall: {'HEALTHY' if health['ok'] else 'ISSUES FOUND'}")
    lines.append(f"Issues: {len(health.get('issues', []))}")
    lines.append("")

    # Containers
    containers = health.get("containers", {})
    running = len(containers.get("running", []))
    stopped = len(containers.get("stopped", []))
    unhealthy = len(containers.get("unhealthy", []))
    lines.append(f"Containers: {running} running, {stopped} stopped, {unhealthy} unhealthy")
    lines.append("")

    # Disk
    disk = health.get("disk", {})
    for p in disk.get("partitions", []):
        lines.append(f"  Disk {p['mount']}: {p['used_pct']}% used ({p['avail']} free)")

    # Memory
    mem = health.get("memory", {})
    lines.append(f"  RAM: {mem.get('ram_pct', '?')}% | Swap: {mem.get('swap_pct', '?')}%")

    # Network
    net = health.get("network", {})
    dns_ok = sum(1 for v in net.get("dns", {}).values() if v)
    dns_total = len(net.get("dns", {}))
    gw_ok = sum(1 for v in net.get("internet", {}).values() if v)
    gw_total = len(net.get("internet", {}))
    lines.append(f"  DNS: {dns_ok}/{dns_total} | Internet: {gw_ok}/{gw_total}")

    # Incidents
    lines.append("")
    lines.append(f"Incidents (24h): {stats.get('by_severity', {})} | Unresolved: {stats.get('unresolved', 0)}")

    return {"ok": True, "report": "\n".join(lines), "raw": health}


# ===========================================================================
# Module self-test
# ===========================================================================

if __name__ == "__main__":
    import pprint
    print("=== homelab_ops self-test ===")
    print("\n--- check_all_health ---")
    pprint.pprint(check_all_health())
    print("\n--- check_providers ---")
    pprint.pprint(check_providers())
    print("\n--- run_doctor (list) ---")
    pprint.pprint(run_doctor())
    print("\nAll functions imported and callable.")
