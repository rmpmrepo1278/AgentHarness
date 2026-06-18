#!/usr/bin/env python3
"""
autonomous_fixer.py — Periodic orchestrator that detects complex issues and
invokes headless Claude Code sessions for auto-remediation.

Runs every 30 min via cron. Reads system state files, identifies issues that
are too complex for simple bash fixes, and delegates them to auto_fix_delegate.py.

Default: DRY RUN mode for first 2 weeks. Remove the flag file to go live:
    Dry run ON:  touch /home/rohit/agentharness/data/auto_fixer_dry_run
    Dry run OFF: rm /home/rohit/agentharness/data/auto_fixer_dry_run

Usage:
    python3 autonomous_fixer.py [--dry-run] [--json] [--min-severity medium]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
AG_HOME = Path("/home/rohit/agentharness")
STATE_DIR = HERMES_HOME / "state"
DATA_DIR = AG_HOME / "data"
LOG_DIR = AG_HOME / "logs"

DRY_RUN_FLAG = DATA_DIR / "auto_fixer_dry_run"
DELEGATE_SCRIPT = AG_HOME / "scripts" / "auto_fix_delegate.py"
SESSION_LOG = STATE_DIR / "auto_fix_sessions.jsonl"
FIXER_LOG = LOG_DIR / "autonomous_fixer.log"
STATE_FILE = DATA_DIR / "autonomous_fixer_state.json"
STAGNATION_FILE = DATA_DIR / "stagnation_state.json"
REFLEXION_FILE = HERMES_HOME / "reflexion_memory.jsonl"
EXPERIMENTS_FILE = HERMES_HOME / "experiments.jsonl"

# Stagnation detection: max attempts per (issue_type, target) within window
STAGNATION_THRESHOLD = 3          # max attempts
STAGNATION_WINDOW_SECONDS = 1800  # 30 minutes

# Issue types that Claude can fix (vs. simple bash or human-only)
CLAUDE_FIXABLE_TYPES = {
    # Infrastructure
    "restart_loop",
    "healthcheck_fail",
    "service_down",
    "disk_space",
    "memory_pressure",
    # Configuration & drift
    "config_drift",
    "missing_script",
    "cron_failure",
    "timer_drift",
    "port_conflict",
    "volume_leak",
    "git_drift",
    # Network & DNS
    "dependency_failure",
    "network_partition",
    "dns_resolution",
    "duckdns_sync",
    # Data & storage
    "db_integrity",
    "backup_integrity",
    "log_bloat",
    "inode_exhaustion",
    "tmp_space",
    "image_stale",
    # Application & providers
    "api_retry_failure",
    "provider_stale",
    "gateway_log_error",
    "mcp_child_health",
    # System-level
    "oom_kill_pattern",
    "zombie_process",
    "ssl_cert_expiry",
    # Security (alert-only, L3 human)
    "api_key_invalid",
}

# Adaptive rate limiting: category-based buckets.
# Issues from different categories can be processed in the same run.
RATE_LIMIT_CATEGORIES = {
    "container":  {"max_per_hour": 4, "cooldown_seconds": 600},
    "resource":   {"max_per_hour": 2, "cooldown_seconds": 900},
    "config":     {"max_per_hour": 2, "cooldown_seconds": 1200},
    "network":    {"max_per_hour": 2, "cooldown_seconds": 600},
    "security":   {"max_per_hour": 1, "cooldown_seconds": 3600},
    "data":       {"max_per_hour": 1, "cooldown_seconds": 1800},
}

# Map issue types to rate-limit categories
ISSUE_TYPE_CATEGORY = {
    "restart_loop":        "container",
    "healthcheck_fail":    "container",
    "service_down":        "container",
    "disk_space":          "resource",
    "memory_pressure":     "resource",
    "log_bloat":           "resource",
    "inode_exhaustion":    "resource",
    "tmp_space":           "resource",
    "image_stale":         "resource",
    "config_drift":        "config",
    "missing_script":      "config",
    "cron_failure":        "config",
    "timer_drift":         "config",
    "port_conflict":       "config",
    "volume_leak":         "config",
    "git_drift":           "config",
    "dependency_failure":  "network",
    "network_partition":   "network",
    "dns_resolution":      "network",
    "duckdns_sync":        "network",
    "db_integrity":        "data",
    "backup_integrity":    "data",
    "api_retry_failure":   "security",
    "provider_stale":      "security",
    "gateway_log_error":   "security",
    "mcp_child_health":    "security",
    "oom_kill_pattern":    "container",
    "zombie_process":      "container",
    "ssl_cert_expiry":     "security",
    "api_key_invalid":     "security",
}

# Critical scripts that must exist for the system to function.
# Maps a human-readable name to the expected absolute path.
CRITICAL_SCRIPTS = {
    "claude_code_delegate": Path.home() / ".hermes" / "hermes-agent" / "scripts" / "claude_code_delegate.py",
    "auto_fix_delegate": AG_HOME / "scripts" / "auto_fix_delegate.py",
    "autonomous_fixer": AG_HOME / "scripts" / "autonomous_fixer.py",
    "consolidated_health": AG_HOME / "scripts" / "consolidated_health.sh",
    "docker_ghost_check": AG_HOME / "scripts" / "docker_ghost_check.sh",
    "proactive_quality_monitor": HERMES_HOME / "scripts" / "proactive_quality_monitor.py",
    "unified_cost_guard": HERMES_HOME / "scripts" / "unified_cost_guard.py",
    "daily_audit": AG_HOME / "scripts" / "daily_audit.py",
    "cos_briefing": HERMES_HOME / "hermes-agent" / "scripts" / "cos_briefing.py",
    "evening_briefing": HERMES_HOME / "hermes-agent" / "scripts" / "evening_briefing.py",
    "weekly_review": HERMES_HOME / "hermes-agent" / "scripts" / "weekly_review.py",
    "document_auto_ingest": HERMES_HOME / "hermes-agent" / "scripts" / "document_auto_ingest.py",
    "document_intel": HERMES_HOME / "hermes-agent" / "scripts" / "document_intel.py",
    # Removed in ponytail cleanup (Jun 16): cross_domain_correlator, predictive_engine
    "email_action_loop": HERMES_HOME / "hermes-agent" / "scripts" / "email_action_loop.py",
    "morning_pipeline": HERMES_HOME / "cron" / "morning_pipeline.sh",
    "morning_prep": HERMES_HOME / "cron" / "morning_prep.sh",
}

# Log files to scan for API retry / connection errors
API_ERROR_LOGS = [
    HERMES_HOME / "logs" / "gateway.log",
    HERMES_HOME / "logs" / "proxy.log",
    AG_HOME / "data" / "logs" / "proxy.log",
    HERMES_HOME / "logs" / "autonomous_tier.log",
]

# Patterns that indicate API retry / connection failures in logs
API_RETRY_ERROR_PATTERNS = [
    "API call failed after.*retries",
    "Connection error",
    "All AI providers are currently unavailable",
    "All providers are currently unavailable",
    "No fallback provider available",
    "Max retries.*exhausted",
    "Connection to provider.*failed after",
]

# Severity levels for filtering
SEVERITY_LEVELS = {"low": 1, "medium": 2, "high": 3, "critical": 4}

# Max issues to process per run (prevent flooding)
MAX_ISSUES_PER_RUN = 1


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] autonomous-fixer: {msg}"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(FIXER_LOG, "a") as f:
        f.write(line + "\n")
    print(line, file=sys.stderr)


def read_json_safe(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return {}


def load_env() -> dict:
    env = dict(os.environ)
    env_file = HERMES_HOME / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key not in env:
                    env[key] = val
    return env


def send_telegram(message: str):
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_HOME_CHANNEL", "")
    if not token or not chat_id:
        return
    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    try:
        subprocess.run(
            ["curl", "-s", "-X", "POST",
             f"https://api.telegram.org/bot{token}/sendMessage",
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, timeout=15,
        )
    except Exception:
        pass


def docker_ps() -> list[dict]:
    """Get container list with status."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=15,
        )
        containers = []
        for line in result.stdout.strip().split("\n"):
            if line:
                try:
                    containers.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return containers
    except Exception:
        return []


def docker_inspect_health(name: str) -> dict:
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{json .State.Health}}", name],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception:
        pass
    return {}


def get_failed_services() -> list[str]:
    try:
        result = subprocess.run(
            ["systemctl", "--failed", "--no-legend"],
            capture_output=True, text=True, timeout=10,
        )
        services = []
        for line in result.stdout.strip().split("\n"):
            if line and "0 loaded" not in line:
                parts = line.split()
                if parts:
                    svc = parts[0].lstrip("●○ ")
                    if svc:
                        services.append(svc)
        return services
    except Exception:
        return []


def get_disk_usage() -> dict:
    try:
        result = subprocess.run(["df", "-h", "/", "/mnt/usb"],
                                capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().split("\n")[1:]  # skip header
        usage = {}
        for line in lines:
            parts = line.split()
            if len(parts) >= 5:
                mount = parts[4] if len(parts) > 4 else parts[-1]
                pct = parts[3] if len(parts) > 3 else parts[4]
                pct_val = int(pct.replace("%", ""))
                usage[mount] = pct_val
        return usage
    except Exception:
        return {}


def get_memory_info() -> dict:
    try:
        result = subprocess.run(["free", "-g"], capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().split("\n")
        mem_line = lines[1].split() if len(lines) > 1 else []
        swap_line = lines[2].split() if len(lines) > 2 else []
        return {
            "total_gb": int(mem_line[1]) if len(mem_line) > 1 else 0,
            "available_gb": int(mem_line[6]) if len(mem_line) > 6 else 0,
            "swap_used_gb": int(swap_line[2]) if len(swap_line) > 2 else 0,
            "swap_total_gb": int(swap_line[1]) if len(swap_line) > 1 else 0,
        }
    except Exception:
        return {}


def get_cpu_load() -> dict:
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
        cores = os.cpu_count() or 4
        return {
            "load_1min": float(parts[0]),
            "load_5min": float(parts[1]),
            "load_15min": float(parts[2]),
            "cores": cores,
        }
    except Exception:
        return {}


def get_recent_restart_count(name: str) -> int:
    """Get restart count for a container."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.RestartCount}}", name],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    return 0


def count_recent_sessions(minutes: int = 30) -> int:
    """Count how many auto-fix sessions ran in the last N minutes."""
    if not SESSION_LOG.exists():
        return 0
    count = 0
    cutoff = time.time() - (minutes * 60)
    try:
        for line in SESSION_LOG.read_text().splitlines():
            if not line:
                continue
            try:
                record = json.loads(line)
                ts_str = record.get("timestamp", "")
                if ts_str:
                    from datetime import datetime
                    ts = datetime.fromisoformat(ts_str).timestamp()
                    if ts > cutoff:
                        count += 1
            except Exception:
                pass
    except Exception:
        pass
    return count


# ---------------------------------------------------------------------------
# Stagnation detection — prevent infinite retry loops
# ---------------------------------------------------------------------------

def _stagnation_key(issue: dict) -> str:
    """Build a unique key for tracking repeated fix attempts."""
    target = (
        issue.get("container")
        or issue.get("service")
        or issue.get("domain")
        or issue.get("mount")
        or issue.get("port")
        or issue.get("script_name")
        or issue.get("key")
        or issue.get("repo")
        or issue.get("log_file")
        or "unknown"
    )
    return f"{issue.get('type', '?')}:{target}"


def load_stagnation() -> dict:
    """Load stagnation state from disk."""
    return read_json_safe(STAGNATION_FILE)


def save_stagnation(state: dict):
    """Persist stagnation state to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STAGNATION_FILE.write_text(json.dumps(state, indent=2, default=str))


def is_stagnant(issue: dict) -> bool:
    """Check if this issue type+target has been attempted too many times."""
    state = load_stagnation()
    key = _stagnation_key(issue)
    now = time.time()
    attempts = state.get(key, [])
    # Prune old attempts outside the window
    attempts = [t for t in attempts if now - t < STAGNATION_WINDOW_SECONDS]
    state[key] = attempts
    save_stagnation(state)
    return len(attempts) >= STAGNATION_THRESHOLD


def record_attempt(issue: dict):
    """Record a fix attempt for stagnation tracking."""
    state = load_stagnation()
    key = _stagnation_key(issue)
    attempts = state.get(key, [])
    attempts.append(time.time())
    state[key] = attempts
    save_stagnation(state)


# ---------------------------------------------------------------------------
# Reflexion — query past failures before acting, write reflections after
# ---------------------------------------------------------------------------

def query_reflexion(issue: dict) -> dict:
    """Before fixing, query past reflections for this (type, target) to avoid
    repeating known-failing approaches. Returns reflections + recommendation."""
    target = (
        issue.get("container")
        or issue.get("service")
        or issue.get("domain")
        or issue.get("mount")
        or issue.get("script_name")
        or issue.get("port")
        or issue.get("key")
        or issue.get("repo")
        or issue.get("log_file")
        or "unknown"
    )
    gene_id = f"gene_{issue.get('type', 'unknown')}"
    result = {"reflections": [], "recommendation": "Proceed", "capsule_history": {}}

    # Query capsule history
    capsule_file = HERMES_HOME / "capsules" / "outcomes.jsonl"
    if capsule_file.exists():
        capsules = []
        for line in capsule_file.read_text().splitlines():
            if not line.strip():
                continue
            try:
                c = json.loads(line)
                if c.get("gene_id") == gene_id and c.get("target") == target:
                    capsules.append(c)
            except json.JSONDecodeError:
                continue
        total = len(capsules)
        successes = sum(1 for c in capsules if c.get("outcome") == "success")
        failures = sum(1 for c in capsules if c.get("outcome") == "fail")
        result["capsule_history"] = {
            "total": total, "successes": successes, "failures": failures,
            "rate": f"{successes/total:.0%}" if total > 0 else "N/A",
            "recent_notes": [c.get("notes", "") for c in capsules[-3:] if c.get("notes")],
        }
        if failures > successes and total > 2:
            result["recommendation"] = f"WARNING: This approach has failed {failures}/{total} times for {target}. Consider a different strategy."

    # Query reflection memory
    if REFLEXION_FILE.exists():
        reflections = []
        for line in REFLEXION_FILE.read_text().splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                if r.get("gene_id") == gene_id and (not target or r.get("target") == target):
                    reflections.append(r)
            except json.JSONDecodeError:
                continue
        result["reflections"] = reflections[-5:]  # last 5

    return result


def write_reflection(issue: dict, outcome: str, notes: str = ""):
    """After fixing, write a reflection to close the learning loop."""
    target = (
        issue.get("container")
        or issue.get("service")
        or issue.get("domain")
        or issue.get("mount")
        or issue.get("script_name")
        or issue.get("port")
        or issue.get("key")
        or issue.get("repo")
        or issue.get("log_file")
        or "unknown"
    )
    gene_id = f"gene_{issue.get('type', 'unknown')}"
    entry = {
        "timestamp": datetime.now().isoformat(),
        "gene_id": gene_id,
        "target": target,
        "outcome": outcome,
        "reflection": notes or f"Fix attempt for {issue.get('issue', '')[:100]} — outcome: {outcome}",
    }
    REFLEXION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REFLEXION_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# CRITIC-style verification — tool-grounded spot checks
# ---------------------------------------------------------------------------

def critic_verify_claim(claim_type: str, target: str) -> dict:
    """Independently verify a service health claim via direct tool calls.
    Returns {claimed, actual, match} to catch hallucinated states."""
    if claim_type == "container_healthy":
        r = subprocess.run(
            ["docker", "inspect", "--format={{.State.Status}}", target],
            capture_output=True, text=True, timeout=5,
        )
        actual = r.stdout.strip()
        return {"claimed": "running", "actual": actual, "match": actual == "running"}
    elif claim_type == "service_active":
        r = subprocess.run(
            ["systemctl", "is-active", target],
            capture_output=True, text=True, timeout=5,
        )
        actual = r.stdout.strip()
        return {"claimed": "active", "actual": actual, "match": actual == "active"}
    elif claim_type == "dns_resolves":
        import socket
        try:
            socket.setdefaulttimeout(5)
            result = socket.getaddrinfo(target, 80)
            actual = result[0][4][0] if result else "unresolved"
            return {"claimed": "resolves", "actual": actual, "match": bool(result)}
        except socket.gaierror:
            return {"claimed": "resolves", "actual": "NXDOMAIN", "match": False}
    elif claim_type == "port_open":
        import socket
        port = int(target.split(":")[-1]) if ":" in target else int(target)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        result = s.connect_ex(("127.0.0.1", port))
        s.close()
        actual = "open" if result == 0 else "closed"
        return {"claimed": "open", "actual": actual, "match": result == 0}
    return {"claimed": claim_type, "actual": "unknown", "match": True}


# ---------------------------------------------------------------------------
# Issue detection
# ---------------------------------------------------------------------------

def check_critical_scripts() -> list[dict]:
    """Check that all critical scripts exist on disk."""
    import os
    issues = []
    for name, path in CRITICAL_SCRIPTS.items():
        if not path.exists():
            issues.append({
                "issue": f"Critical script missing: {name} (expected at {path})",
                "type": "missing_script",
                "severity": "high",
                "script_name": name,
                "script_path": str(path),
            })
        elif not os.access(path, os.R_OK):
            issues.append({
                "issue": f"Critical script not readable: {name} (at {path})",
                "type": "missing_script",
                "severity": "medium",
                "script_name": name,
                "script_path": str(path),
            })
    return issues


def check_api_retry_failures() -> list[dict]:
    """Scan recent log entries for API retry / connection error patterns."""
    import re
    issues = []
    # Only look at log entries from the last 30 minutes
    cutoff = time.time() - 1800

    for log_path in API_ERROR_LOGS:
        if not log_path.exists():
            continue
        try:
            # Read last 500 lines of each log file
            with open(log_path, "r", errors="replace") as f:
                lines = f.readlines()
            recent_lines = lines[-500:]
            matched_patterns = set()
            for line in recent_lines:
                for pattern in API_RETRY_ERROR_PATTERNS:
                    if re.search(pattern, line, re.IGNORECASE):
                        matched_patterns.add(pattern)
                        break
            if matched_patterns:
                # Count total matches across all patterns
                total_matches = sum(
                    1 for line in recent_lines
                    for pattern in matched_patterns
                    if re.search(pattern, line, re.IGNORECASE)
                )
                severity = "critical" if total_matches >= 5 else "high"
                issues.append({
                    "issue": (
                        f"API retry/connection errors in {log_path.name}: "
                        f"{total_matches} occurrences in last 30 min "
                        f"(patterns: {', '.join(sorted(matched_patterns))})"
                    ),
                    "type": "api_retry_failure",
                    "severity": severity,
                    "log_file": str(log_path),
                    "match_count": total_matches,
                    "patterns": list(matched_patterns),
                })
        except Exception:
            continue

    return issues


def check_ssl_cert_expiry() -> list[dict]:
    """Check SSL certificate expiry for all DuckDNS domains."""
    import re as _re
    issues = []
    # Find cert files from NPM or Let's Encrypt
    cert_dirs = [
        Path("/home/rohit/services/data/nginx-proxy-manager/letsencrypt"),
        Path("/home/rohit/.hermes/certs"),
        Path("/etc/letsencrypt/live"),
    ]
    checked_domains = set()
    for cert_dir in cert_dirs:
        if not cert_dir.exists():
            continue
        for cert_file in cert_dir.rglob("*.pem"):
            if cert_file.name in ("fullchain.pem", "cert.pem"):
                domain = cert_file.parent.name
                if domain in checked_domains:
                    continue
                checked_domains.add(domain)
                try:
                    result = subprocess.run(
                        ["openssl", "x509", "-in", str(cert_file),
                         "-noout", "-enddate"],
                        capture_output=True, text=True, timeout=10,
                    )
                    if result.returncode == 0:
                        # Parse: notAfter=Jun 13 12:00:00 2026 GMT
                        match = _re.search(r"notAfter=(.+)", result.stdout)
                        if match:
                            from datetime import datetime as _dt
                            expiry = _dt.strptime(match.group(1).strip(), "%b %d %H:%M:%S %Y %Z")
                            days_left = (expiry - _dt.utcnow()).days
                            if days_left < 0:
                                issues.append({
                                    "issue": f"SSL cert EXPIRED for {domain} ({abs(days_left)} days ago)",
                                    "type": "ssl_cert_expiry",
                                    "severity": "critical",
                                    "domain": domain,
                                    "days_left": days_left,
                                })
                            elif days_left < 7:
                                issues.append({
                                    "issue": f"SSL cert expiring in {days_left} days for {domain}",
                                    "type": "ssl_cert_expiry",
                                    "severity": "critical",
                                    "domain": domain,
                                    "days_left": days_left,
                                })
                            elif days_left < 30:
                                issues.append({
                                    "issue": f"SSL cert expiring in {days_left} days for {domain}",
                                    "type": "ssl_cert_expiry",
                                    "severity": "high",
                                    "domain": domain,
                                    "days_left": days_left,
                                })
                except Exception:
                    continue
    return issues


def check_oom_kills() -> list[dict]:
    """Check dmesg for recent OOM kills."""
    issues = []
    try:
        result = subprocess.run(
            ["dmesg", "--time-format=iso"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            # dmesg may need root; try journalctl
            result = subprocess.run(
                ["journalctl", "-k", "--since", "1 hour ago", "--no-pager", "-q"],
                capture_output=True, text=True, timeout=10,
            )
        lines = result.stdout.strip().split("\n")
        oom_lines = [l for l in lines if "oom" in l.lower() or "killed process" in l.lower()]
        if oom_lines:
            # Check for recent (last 1 hour) vs older
            recent_count = len([l for l in oom_lines if "hour" not in l.lower() or "minute" in l.lower()])
            severity = "critical" if recent_count > 0 else "high"
            # Get the process names
            killed_procs = []
            for line in oom_lines[-5:]:
                import re as _re
                m = _re.search(r"Killed process \d+ \(([^)]+)\)", line)
                if m:
                    killed_procs.append(m.group(1))
            proc_str = ", ".join(killed_procs) if killed_procs else "unknown"
            issues.append({
                "issue": f"OOM kills detected: {len(oom_lines)} total, recent: {proc_str}",
                "type": "oom_kill_pattern",
                "severity": severity,
                "oom_count": len(oom_lines),
                "killed_processes": killed_procs,
            })
    except Exception:
        pass
    return issues


def check_zombie_processes() -> list[dict]:
    """Check for zombie process accumulation."""
    issues = []
    try:
        result = subprocess.run(
            ["ps", "-eo", "stat,pid,ppid,comm"],
            capture_output=True, text=True, timeout=5,
        )
        zombies = [l for l in result.stdout.strip().split("\n") if l.startswith("Z")]
        if len(zombies) > 50:
            issues.append({
                "issue": f"Critical zombie process count: {len(zombies)}",
                "type": "zombie_process",
                "severity": "high",
                "zombie_count": len(zombies),
            })
        elif len(zombies) > 10:
            issues.append({
                "issue": f"Elevated zombie process count: {len(zombies)}",
                "type": "zombie_process",
                "severity": "medium",
                "zombie_count": len(zombies),
            })
    except Exception:
        pass
    return issues


def check_inode_exhaustion() -> list[dict]:
    """Check inode usage across filesystems."""
    issues = []
    try:
        result = subprocess.run(
            ["df", "-i", "/", "/mnt/usb"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 6:
                mount = parts[5]
                pct_str = parts[4].replace("%", "")
                try:
                    pct = int(pct_str)
                except ValueError:
                    continue
                if pct > 95:
                    issues.append({
                        "issue": f"Inode exhaustion on {mount}: {pct}% used",
                        "type": "inode_exhaustion",
                        "severity": "critical",
                        "mount": mount,
                        "inode_pct": pct,
                    })
                elif pct > 85:
                    issues.append({
                        "issue": f"High inode usage on {mount}: {pct}% used",
                        "type": "inode_exhaustion",
                        "severity": "high",
                        "mount": mount,
                        "inode_pct": pct,
                    })
    except Exception:
        pass
    return issues


def check_tmp_space() -> list[dict]:
    """Check /tmp partition usage."""
    issues = []
    try:
        result = subprocess.run(
            ["df", "-h", "/tmp"],
            capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 5:
                pct_str = parts[4].replace("%", "")
                try:
                    pct = int(pct_str)
                except ValueError:
                    return issues
                if pct > 95:
                    issues.append({
                        "issue": f"/tmp critically full: {pct}% used ({parts[3]} free)",
                        "type": "tmp_space",
                        "severity": "critical",
                        "tmp_pct": pct,
                    })
                elif pct > 85:
                    issues.append({
                        "issue": f"/tmp usage high: {pct}% used ({parts[3]} free)",
                        "type": "tmp_space",
                        "severity": "high",
                        "tmp_pct": pct,
                    })
    except Exception:
        pass
    return issues


def check_port_conflicts() -> list[dict]:
    """Check for port conflicts between services."""
    issues = []
    try:
        result = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True, text=True, timeout=5,
        )
        # Group by port, but ignore IPv4 vs IPv6 dual-stack (0.0.0.0 + :: is normal)
        port_addrs = {}  # port -> set of normalized addresses
        import re as _re
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 4:
                local = parts[3]
                m = _re.search(r":(\d+)$", local)
                if m:
                    port = m.group(1)
                    addr = _re.sub(r"^.+:", "", local)  # strip port
                    # Normalize: 0.0.0.0 and :: both mean "all interfaces"
                    if addr in ("0.0.0.0", "::", "*"):
                        addr = "*"
                    port_addrs.setdefault(port, set()).add(addr)
        for port, addrs in port_addrs.items():
            if len(addrs) > 1:
                # Multiple different addresses on same port = real conflict
                addr_str = ", ".join(sorted(addrs))
                issues.append({
                    "issue": f"Port conflict on :{port}: bound on {addr_str}",
                    "type": "port_conflict",
                    "severity": "high",
                    "port": port,
                    "addrs": sorted(addrs),
                })
    except Exception:
        pass
    return issues


def check_volume_leaks() -> list[dict]:
    """Check for dangling Docker volumes."""
    issues = []
    try:
        result = subprocess.run(
            ["docker", "volume", "ls", "-f", "dangling=true", "--format", "{{.Name}}"],
            capture_output=True, text=True, timeout=10,
        )
        dangling = [l for l in result.stdout.strip().split("\n") if l]
        if len(dangling) > 10:
            issues.append({
                "issue": f"Many dangling Docker volumes: {len(dangling)}",
                "type": "volume_leak",
                "severity": "medium",
                "dangling_count": len(dangling),
            })
        elif len(dangling) > 5:
            issues.append({
                "issue": f"Dangling Docker volumes: {len(dangling)}",
                "type": "volume_leak",
                "severity": "low",
                "dangling_count": len(dangling),
            })
    except Exception:
        pass
    return issues


def check_dns_resolution() -> list[dict]:
    """Check DNS resolution via Pi-hole."""
    issues = []
    try:
        import socket
        # Test Pi-hole resolution
        try:
            socket.setdefaulttimeout(5)
            result = socket.getaddrinfo("google.com", 80)
            if not result:
                issues.append({
                    "issue": "DNS resolution failed: Pi-hole not resolving google.com",
                    "type": "dns_resolution",
                    "severity": "critical",
                })
        except socket.gaierror:
            issues.append({
                "issue": "DNS resolution failed: Pi-hole not resolving google.com",
                "type": "dns_resolution",
                "severity": "critical",
            })
        # Test DuckDNS resolution
        try:
            socket.setdefaulttimeout(5)
            result = socket.getaddrinfo("chagulihome.duckdns.org", 443)
            if not result:
                issues.append({
                    "issue": "DNS resolution failed: chagulihome.duckdns.org not resolving",
                    "type": "dns_resolution",
                    "severity": "high",
                })
        except socket.gaierror:
            issues.append({
                "issue": "DNS resolution failed: chagulihome.duckdns.org not resolving",
                "type": "dns_resolution",
                "severity": "high",
            })
    except Exception:
        pass
    return issues


def check_duckdns_sync() -> list[dict]:
    """Check if DuckDNS record matches current external IP."""
    issues = []
    try:
        import urllib.request
        import socket
        # Get current external IP
        current_ip = None
        for url in ["https://api.ipify.org", "https://ifconfig.me/ip"]:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "homelab-monitor/1.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    current_ip = resp.read().decode().strip()
                    break
            except Exception:
                continue
        if not current_ip:
            return issues  # Can't determine external IP, skip
        # Get DuckDNS record — must query external DNS (8.8.8.8) to bypass Pi-hole
        dns_ip = None
        try:
            dns_r = subprocess.run(
                ["dig", "+short", "chagulihome.duckdns.org", "@8.8.8.8"],
                capture_output=True, text=True, timeout=10,
            )
            if dns_r.returncode == 0 and dns_r.stdout.strip():
                dns_ip = dns_r.stdout.strip().split("\n")[0]
        except Exception:
            pass
        if dns_ip and current_ip != dns_ip:
            # L1 self-heal: try running the DuckDNS update script directly
            script = Path.home() / ".hermes" / "scripts" / "duckdns_update.sh"
            if script.exists():
                try:
                    result = subprocess.run(
                        ["bash", str(script)],
                        capture_output=True, text=True, timeout=30,
                    )
                    if result.returncode == 0:
                        log(f"DuckDNS self-healed: {result.stdout.strip()}")
                        # Re-check DNS after update
                        import time
                        time.sleep(5)
                        dns_r2 = subprocess.run(
                            ["dig", "+short", "chagulihome.duckdns.org", "@8.8.8.8"],
                            capture_output=True, text=True, timeout=10,
                        )
                        new_dns = dns_r2.stdout.strip().split("\n")[0] if dns_r2.returncode == 0 and dns_r2.stdout.strip() else None
                        if new_dns and new_dns == current_ip:
                            log(f"DuckDNS verified: now resolves to {new_dns}")
                            return issues  # Fixed, don't report
                        else:
                            log(f"DuckDNS update ran but DNS still shows {new_dns}, current={current_ip}")
                    else:
                        log(f"DuckDNS update script failed: {result.stdout.strip()} {result.stderr.strip()}")
                except Exception as e:
                    log(f"DuckDNS update script error: {e}")
            else:
                log(f"DuckDNS update script not found at {script}")

            # If we get here, self-heal failed — report the issue
            issues.append({
                "issue": f"DuckDNS out of sync: current={current_ip}, dns={dns_ip}",
                "type": "duckdns_sync",
                "severity": "high",
                "current_ip": current_ip,
                "dns_ip": dns_ip,
            })
    except Exception:
        pass
    return issues


def check_mcp_child_health() -> list[dict]:
    """Check individual MCP service ports."""
    issues = []
    mcp_ports = {
        8090: "mcp-gateway",
        8091: "hermes-memory-mcp",
        8095: "docker-mcp",
        8097: "filesystem-mcp",
        8098: "github-mcp",
        8099: "web-search-mcp",
        8100: "postgres-mcp",
        8102: "redis-mcp",
        8103: "slack-mcp",
        8104: "notion-mcp",
        8105: "browser-use-mcp",
        8106: "qdrant-mcp",
        8107: "browser-use-mcp-2",
    }
    import socket
    for port, name in mcp_ports.items():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            result = s.connect_ex(("127.0.0.1", port))
            s.close()
            if result != 0:
                severity = "critical" if port == 8090 else "high"
                issues.append({
                    "issue": f"MCP service {name} (port {port}) is unreachable",
                    "type": "mcp_child_health",
                    "severity": severity,
                    "service": name,
                    "port": port,
                })
        except Exception:
            pass
    return issues


def check_timer_drift() -> list[dict]:
    """Check systemd timers for drift or failures."""
    issues = []
    try:
        result = subprocess.run(
            ["systemctl", "list-timers", "--all", "--no-pager"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 5:
                timer_name = parts[0]
                if "n/a" in line.lower() and "timer" in timer_name:
                    # Timer has never fired
                    if timer_name not in ("fwupd-refresh.timer",):
                        issues.append({
                            "issue": f"Systemd timer {timer_name} has never fired (n/a)",
                            "type": "timer_drift",
                            "severity": "medium",
                            "timer": timer_name,
                        })
    except Exception:
        pass
    return issues


def check_git_drift() -> list[dict]:
    """Check for uncommitted changes or local behind remote."""
    issues = []
    repos = [
        (Path.home() / ".hermes" / "hermes-agent", "hermes-agent"),
        (Path("/home/rohit/agentharness"), "agentharness"),
        (Path.home() / "services", "services"),
    ]
    for repo_path, repo_name in repos:
        if not (repo_path / ".git").exists():
            continue
        try:
            # Check for uncommitted changes
            result = subprocess.run(
                ["git", "-C", str(repo_path), "status", "--porcelain"],
                capture_output=True, text=True, timeout=10,
            )
            changes = [l for l in result.stdout.strip().split("\n") if l]
            if changes:
                # Check if changes are old (>24h)
                old_changes = 0
                for f in changes[:5]:
                    parts = f.split(None, 2)
                    if len(parts) >= 3:
                        fpath = repo_path / parts[2]
                        if fpath.exists():
                            mtime = fpath.stat().st_mtime
                            if time.time() - mtime > 86400:
                                old_changes += 1
                if old_changes > 0:
                    issues.append({
                        "issue": f"Git repo {repo_name} has {len(changes)} uncommitted changes ({old_changes} >24h old)",
                        "type": "git_drift",
                        "severity": "medium",
                        "repo": repo_name,
                        "change_count": len(changes),
                    })
        except Exception:
            continue
    return issues


def check_api_key_validity() -> list[dict]:
    """Check that required API keys are set and non-empty."""
    issues = []
    env = load_env()
    required_keys = [
        "OPENROUTER_API_KEY",
        "TELEGRAM_BOT_TOKEN",
    ]
    optional_but_important = [
        "GROQ_API_KEY",
        "CEREBRAS_API_KEY",
        "SAMBANOVA_API_KEY",
    ]
    for key in required_keys:
        val = env.get(key, "")
        if not val or val.strip() in ("", "dummy", "your-key-here", "xxx"):
            issues.append({
                "issue": f"Required API key {key} is missing or empty",
                "type": "api_key_invalid",
                "severity": "critical",
                "key": key,
            })
    for key in optional_but_important:
        val = env.get(key, "")
        if not val or val.strip() in ("", "dummy", "your-key-here", "xxx"):
            issues.append({
                "issue": f"Optional API key {key} is missing (provider may be unavailable)",
                "type": "api_key_invalid",
                "severity": "medium",
                "key": key,
            })
    return issues


def detect_issues() -> list[dict]:
    """Scan system state and return list of detected issues."""
    issues = []
    containers = docker_ps()

    # ── 1. Container restart loops ──
    restarting = [c for c in containers if "Restarting" in c.get("Status", "")]
    for c in restarting:
        name = c.get("Name", c.get("Names", "?"))
        issues.append({
            "issue": f"Container {name} is in a restart loop",
            "type": "restart_loop",
            "severity": "high",
            "container": name,
        })

    # Also check restart count for running containers that recently crashed
    for c in containers:
        status = c.get("Status", "")
        name = c.get("Name", c.get("Names", "?"))
        if "Up" in status and "restart" not in status.lower():
            count = get_recent_restart_count(name)
            if count >= 3:
                issues.append({
                    "issue": f"Container {name} has restarted {count} times recently",
                    "type": "restart_loop",
                    "severity": "high",
                    "container": name,
                })

    # ── 2. Unhealthy containers ──
    unhealthy = [c for c in containers if "unhealthy" in c.get("Status", "").lower()]
    for c in unhealthy:
        name = c.get("Name", c.get("Names", "?"))
        issues.append({
            "issue": f"Container {name} is unhealthy",
            "type": "healthcheck_fail",
            "severity": "high",
            "container": name,
        })

    # ── 3. Exited containers (with restart policy) ──
    exited = [c for c in containers if c.get("Status", "").startswith("Exited")]
    for c in exited:
        name = c.get("Name", c.get("Names", "?"))
        # Skip known intentionally stopped containers
        if name in ("netdata",):
            continue
        issues.append({
            "issue": f"Container {name} has exited",
            "type": "service_down",
            "severity": "high",
            "container": name,
        })

    # ── 4. Failed systemd services ──
    failed = get_failed_services()
    known_disabled = {"agentharness-watchdog.timer", "sentinel-agent.service",
                      "llm-proxy.service"}  # user-level duplicate
    for svc in failed:
        if svc not in known_disabled:
            issues.append({
                "issue": f"Systemd service {svc} has failed",
                "type": "service_down",
                "severity": "high",
                "service": svc,
            })

    # ── 5. Disk space ──
    disk = get_disk_usage()
    for mount, pct in disk.items():
        if pct > 90:
            issues.append({
                "issue": f"Disk {mount} is critically full at {pct}%",
                "type": "disk_space",
                "severity": "critical",
            })
        elif pct > 80:
            issues.append({
                "issue": f"Disk {mount} is at {pct}% — cleanup recommended",
                "type": "disk_space",
                "severity": "medium",
            })

    # ── 6. Memory pressure ──
    mem = get_memory_info()
    avail = mem.get("available_gb", 999)
    if avail < 1:
        issues.append({
            "issue": f"Critical memory pressure: only {avail}GB available",
            "type": "memory_pressure",
            "severity": "critical",
        })
    elif avail < 2:
        issues.append({
            "issue": f"Low memory: {avail}GB available",
            "type": "memory_pressure",
            "severity": "medium",
        })

    # ── 7. Swap pressure ──
    swap_used = mem.get("swap_used_gb", 0)
    swap_total = mem.get("swap_total_gb", 0)
    if swap_total > 0 and swap_used > swap_total * 0.5:
        issues.append({
            "issue": f"Swap usage high: {swap_used}GB / {swap_total}GB",
            "type": "memory_pressure",
            "severity": "medium",
        })

    # ── 8. High CPU load ──
    load = get_cpu_load()
    cores = load.get("cores", 4)
    load_1 = load.get("load_1min", 0)
    if load_1 > cores * 5:
        issues.append({
            "issue": f"CPU load critically high: {load_1} (threshold: {cores * 5} for {cores} cores)",
            "type": "memory_pressure",
            "severity": "high",
        })

    # ── 9. Persistent issues from daemon state ──
    daemon_state = read_json_safe(STATE_DIR / "daemon_state.json")
    issues_seen = daemon_state.get("issues_seen", {})
    for issue_desc, count in issues_seen.items():
        if count > 10:
            # Already persistent — check if it's a type Claude can fix
            for fixable_type in CLAUDE_FIXABLE_TYPES:
                if fixable_type.replace("_", " ") in issue_desc.lower():
                    # Avoid duplicates
                    already = any(i["issue"] == issue_desc for i in issues)
                    if not already:
                        issues.append({
                            "issue": f"Persistent issue ({count}x): {issue_desc}",
                            "type": fixable_type,
                            "severity": "high",
                        })
                    break

    # ── 10. Quality monitor warnings ──
    quality_state = read_json_safe(STATE_DIR / "quality_monitor_state.json")
    if quality_state.get("last_grade") in ("D", "F"):
        issues.append({
            "issue": f"System quality degraded: grade {quality_state.get('last_grade')}",
            "type": "config_drift",
            "severity": "medium",
        })

    return issues


# ---------------------------------------------------------------------------
# Issue filtering and prioritization
# ---------------------------------------------------------------------------

def filter_issues(issues: list[dict], min_severity: str = "medium") -> list[dict]:
    """Filter issues: only Claude-fixable types, above min severity."""
    min_level = SEVERITY_LEVELS.get(min_severity, 2)
    filtered = []
    for issue in issues:
        # Must be a Claude-fixable type
        if issue["type"] not in CLAUDE_FIXABLE_TYPES:
            continue
        # Must meet minimum severity
        issue_level = SEVERITY_LEVELS.get(issue.get("severity", "medium"), 2)
        if issue_level < min_level:
            continue
        filtered.append(issue)
    return filtered


def deduplicate_issues(issues: list[dict]) -> list[dict]:
    """Remove duplicate issues (same container + type)."""
    seen = set()
    unique = []
    for issue in issues:
        key = (issue.get("issue", ""), issue.get("type", ""))
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


# ---------------------------------------------------------------------------
# Delegate invocation
# ---------------------------------------------------------------------------

def invoke_delegate(issue: dict, dry_run: bool = False) -> dict:
    """Invoke auto_fix_delegate.py for a single issue."""
    cmd = [
        sys.executable, str(DELEGATE_SCRIPT),
        "--issue", issue["issue"],
        "--issue-type", issue["type"],
        "--source", "autonomous_fixer",
        "--timeout", "900",
    ]
    if dry_run:
        cmd.append("--dry-run")
    cmd.append("--json")

    log(f"Invoking delegate: {' '.join(cmd[:5])}...")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # 2 min max for the delegate to start + pre-flight
        )
        if result.returncode in (0, 1):  # 0=completed, 1=failed but ran
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return {"status": "parse_error", "raw": result.stdout[:500]}
        else:
            return {"status": "delegate_error", "error": result.stderr[:500]}
    except subprocess.TimeoutExpired:
        return {"status": "delegate_timeout"}
    except Exception as e:
        return {"status": "delegate_exception", "error": str(e)}


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def save_state(state: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def load_state() -> dict:
    return read_json_safe(STATE_FILE)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Autonomous fixer — detect complex issues and invoke Claude for remediation",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Force dry-run mode (overrides flag file)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--min-severity", default="medium",
                        choices=["low", "medium", "high", "critical"],
                        help="Minimum severity to process (default: medium)")
    args = parser.parse_args()

    # Determine dry run mode
    dry_run = args.dry_run or DRY_RUN_FLAG.exists()

    log(f"=== Autonomous fixer starting ===")
    log(f"Dry run: {dry_run}, Min severity: {args.min_severity}")

    # ── 0. Load persistent state ──
    state = read_json_safe(STATE_FILE)

    # ── 1. Detect all issues ──
    all_issues = detect_issues()

    # ── 1b. Check critical scripts exist ──
    script_issues = check_critical_scripts()
    if script_issues:
        log(f"Script checks: {len(script_issues)} missing script(s) detected")
        all_issues.extend(script_issues)

    # ── 1c. Scan logs for API retry / connection errors ──
    api_issues = check_api_retry_failures()
    if api_issues:
        log(f"Log scan: {len(api_issues)} API retry failure(s) detected")
        all_issues.extend(api_issues)

    # ── 1d. SSL certificate expiry ──
    ssl_issues = check_ssl_cert_expiry()
    if ssl_issues:
        log(f"SSL checks: {len(ssl_issues)} cert issue(s) detected")
        all_issues.extend(ssl_issues)

    # ── 1e. System-level checks (fast, no subprocess where possible) ──
    all_issues.extend(check_oom_kills())
    all_issues.extend(check_zombie_processes())
    all_issues.extend(check_inode_exhaustion())
    all_issues.extend(check_tmp_space())

    # ── 1f. Docker volume leaks ──
    all_issues.extend(check_volume_leaks())

    # ── 1g. Port conflicts ──
    all_issues.extend(check_port_conflicts())

    # ── 1h. DNS resolution ──
    dns_issues = check_dns_resolution()
    if dns_issues:
        log(f"DNS checks: {len(dns_issues)} resolution issue(s) detected")
        all_issues.extend(dns_issues)

    # ── 1i. MCP child service health ──
    mcp_issues = check_mcp_child_health()
    if mcp_issues:
        log(f"MCP checks: {len(mcp_issues)} service(s) unreachable")
        all_issues.extend(mcp_issues)

    # ── 1j. DuckDNS sync ──
    duckdns_issues = check_duckdns_sync()
    if duckdns_issues:
        log(f"DuckDNS: sync issue detected")
        all_issues.extend(duckdns_issues)

    # ── 1k. Systemd timer drift ──
    all_issues.extend(check_timer_drift())

    # ── 1l. Git drift ──
    git_issues = check_git_drift()
    if git_issues:
        log(f"Git checks: {len(git_issues)} repo(s) with drift")
        all_issues.extend(git_issues)

    # ── 1m. API key validity ──
    key_issues = check_api_key_validity()
    if key_issues:
        log(f"Key checks: {len(key_issues)} issue(s) detected")
        all_issues.extend(key_issues)

    log(f"Detected {len(all_issues)} total issues")

    # ── 2. Filter to Claude-fixable issues ──
    fixable = filter_issues(all_issues, args.min_severity)
    fixable = deduplicate_issues(fixable)
    log(f"Claude-fixable issues (severity >= {args.min_severity}): {len(fixable)}")

    if not fixable:
        log("No fixable issues found — all clear")
        if args.json:
            print(json.dumps({"status": "all_clear", "issues_detected": len(all_issues),
                              "fixable": 0, "processed": 0}))
        sys.exit(0)

    # ── 3. Sort by severity (highest first) ──
    fixable.sort(key=lambda i: SEVERITY_LEVELS.get(i.get("severity", "medium"), 2), reverse=True)

    # ── 4. Adaptive rate limiting: per-category cooldowns ──
    rate_limit_data = read_json_safe(RATE_LIMIT_FILE) if 'RATE_LIMIT_FILE' in dir() else {}
    # RATE_LIMIT_FILE may not exist yet; use state file instead
    rate_limit_state = state.get("rate_limits", {})
    now = time.time()
    category_last_fixed = rate_limit_state.get("category_last_fixed", {})
    category_hour_count = rate_limit_state.get("category_hour_count", {})

    # Clean up hour counts older than 1 hour
    for cat in list(category_hour_count.keys()):
        if now - category_last_fixed.get(cat, 0) > 3600:
            category_hour_count[cat] = 0

    # Filter fixable issues by category rate limits
    eligible_issues = []
    for issue in fixable:
        cat = ISSUE_TYPE_CATEGORY.get(issue["type"], "config")
        cat_config = RATE_LIMIT_CATEGORIES.get(cat, {"max_per_hour": 2, "cooldown_seconds": 900})
        last_fixed = category_last_fixed.get(cat, 0)
        hour_count = category_hour_count.get(cat, 0)
        if (now - last_fixed) < cat_config["cooldown_seconds"]:
            log(f"Rate limited (category={cat}): {issue['issue'][:80]}")
            continue
        if hour_count >= cat_config["max_per_hour"]:
            log(f"Hourly limit reached (category={cat}): {issue['issue'][:80]}")
            continue
        eligible_issues.append(issue)

    if not eligible_issues:
        log("All fixable issues are rate-limited — skipping")
        if args.json:
            print(json.dumps({"status": "rate_limited", "fixable": len(fixable), "eligible": 0}))
        sys.exit(0)

    # ── 5. Process top issues (up to 3 per run, from different categories) ──
    used_categories = set()
    top_issues = []
    for issue in eligible_issues:
        cat = ISSUE_TYPE_CATEGORY.get(issue["type"], "config")
        if cat not in used_categories and len(top_issues) < 3:
            top_issues.append(issue)
            used_categories.add(cat)
        if len(top_issues) >= 3:
            break
    results = []

    for issue in top_issues:
        log(f"Processing: {issue['issue']} (type={issue['type']}, severity={issue['severity']})")

        # Stagnation check: skip if we've tried this too many times
        if is_stagnant(issue):
            key = _stagnation_key(issue)
            log(f"STAGNANT: {key} — skipping (threshold={STAGNATION_THRESHOLD})")
            send_telegram(
                f"⚠️ <b>Stagnation detected</b>\n\n"
                f"Fix attempts for <code>{key}</code> exceeded {STAGNATION_THRESHOLD} "
                f"in {STAGNATION_WINDOW_SECONDS // 60} minutes.\n"
                f"Human intervention may be needed."
            )
            results.append({"status": "stagnant", "issue": issue["issue"], "key": key})
            continue

        # ── Reflexion: query past failures before acting ──
        reflexion = query_reflexion(issue)
        if reflexion.get("reflections"):
            log(f"Reflexion: {len(reflexion['reflections'])} past reflection(s) found")
        if "WARNING" in reflexion.get("recommendation", ""):
            log(f"Reflexion warning: {reflexion['recommendation']}")

        # Record this attempt
        record_attempt(issue)

        if dry_run:
            log(f"DRY RUN: would invoke delegate for: {issue['issue']}")
            result = {
                "status": "dry_run",
                "issue": issue["issue"],
                "type": issue["type"],
                "severity": issue["severity"],
                "reflexion": reflexion,
            }
        else:
            result = invoke_delegate(issue, dry_run=False)

        results.append(result)
        log(f"Result: {result.get('status', '?')}")

        # ── Write reflection to close the learning loop ──
        fix_status = result.get("status", "unknown")
        outcome = "success" if fix_status in ("completed", "dry_run") else "fail"
        reflection_notes = (
            f"Fix for {issue['issue'][:80]}: status={fix_status}. "
            f"Reflexion pre-check: {reflexion.get('recommendation', 'N/A')}. "
            f"Result: {result.get('raw', '')[:100] if isinstance(result.get('raw'), str) else ''}"
        )
        write_reflection(issue, outcome, reflection_notes)
        log(f"Reflection recorded: {outcome}")

        # ── ACE playbook: generate candidate update ──
        try:
            ace_script = HERMES_HOME / "scripts" / "ace_playbook.py"
            if ace_script.exists():
                ace_outcome = "success" if outcome == "success" else "fail" if outcome == "fail" else "partial"
                subprocess.run(
                    [sys.executable, str(ace_script), "generate",
                     "--task", issue["issue"][:100],
                     "--outcome", ace_outcome,
                     "--notes", reflection_notes[:300]],
                    capture_output=True, timeout=10,
                )
        except Exception as e:
            log(f"ACE candidate generation failed: {e}")

        # ── Voyager skill library: extract on success ──
        if outcome == "success" and fix_status not in ("dry_run",):
            try:
                skill_script = HERMES_HOME / "scripts" / "skill_library.py"
                if skill_script.exists():
                    subprocess.run(
                        [sys.executable, str(skill_script), "extract"],
                        capture_output=True, timeout=10,
                    )
            except Exception as e:
                log(f"Skill extraction failed: {e}")

        # Record Capsule outcome
        try:
            from pathlib import Path as _P
            _capsule_script = _P("/home/rohit/.hermes/scripts/capsule_tracker.py")
            if _capsule_script.exists():
                _target = (
                    issue.get("container")
                    or issue.get("service")
                    or issue.get("domain")
                    or issue.get("mount")
                    or "unknown"
                )
                _outcome = "success" if fix_status in ("completed", "dry_run") else "fail"
                _signals = [issue.get("type", "")]
                subprocess.run(
                    [sys.executable, str(_capsule_script), "record",
                     "--gene", f"gene_{issue['type']}",
                     "--target", _target,
                     "--outcome", _outcome,
                     "--source", "autonomous_fixer",
                     "--notes", f"status={fix_status}"],
                    capture_output=True, timeout=10,
                )
        except Exception as e:
            log(f"Capsule record failed: {e}")

    # ── 6. Save state ──
    state = load_state()
    state["last_run"] = datetime.now().isoformat()
    state["last_issues_detected"] = len(all_issues)
    state["last_fixable"] = len(fixable)
    state["last_results"] = [r.get("status") for r in results]
    state["dry_run"] = dry_run
    # Update per-category rate limit tracking
    now_ts = time.time()
    if "rate_limits" not in state:
        state["rate_limits"] = {"category_last_fixed": {}, "category_hour_count": {}}
    for issue in top_issues:
        cat = ISSUE_TYPE_CATEGORY.get(issue["type"], "config")
        state["rate_limits"]["category_last_fixed"][cat] = now_ts
        state["rate_limits"]["category_hour_count"][cat] = (
            state["rate_limits"]["category_hour_count"].get(cat, 0) + 1
        )
    save_state(state)

    # ── 7. Output ──
    output = {
        "status": "completed",
        "timestamp": datetime.now().isoformat(),
        "dry_run": dry_run,
        "issues_detected": len(all_issues),
        "fixable": len(fixable),
        "processed": len(results),
        "results": results,
    }

    if args.json:
        print(json.dumps(output, indent=2, default=str))
    else:
        print(f"Autonomous fixer: {len(all_issues)} issues, {len(fixable)} fixable, {len(results)} processed")
        for r in results:
            print(f"  - {r.get('issue', r.get('status', '?'))}: {r.get('status', '?')}")

    # Exit 0 if all processed OK or dry run
    sys.exit(0)


if __name__ == "__main__":
    main()
