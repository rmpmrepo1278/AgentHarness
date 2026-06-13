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

# Issue types that Claude can fix (vs. simple bash or human-only)
CLAUDE_FIXABLE_TYPES = {
    "restart_loop",
    "healthcheck_fail",
    "disk_space",
    "memory_pressure",
    "service_down",
    "config_drift",
    "dependency_failure",
}

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
# Issue detection
# ---------------------------------------------------------------------------

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

    # ── 1. Detect all issues ──
    all_issues = detect_issues()
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

    # ── 4. Check rate limit: max 1 session per 30 min ──
    recent = count_recent_sessions(30)
    if recent > 0:
        log(f"Rate limited: {recent} session(s) in last 30 min — skipping")
        if args.json:
            print(json.dumps({"status": "rate_limited", "recent_sessions": recent}))
        sys.exit(0)

    # ── 5. Process top issue (max 1 per run) ──
    top_issues = fixable[:MAX_ISSUES_PER_RUN]
    results = []

    for issue in top_issues:
        log(f"Processing: {issue['issue']} (type={issue['type']}, severity={issue['severity']})")

        if dry_run:
            log(f"DRY RUN: would invoke delegate for: {issue['issue']}")
            result = {
                "status": "dry_run",
                "issue": issue["issue"],
                "type": issue["type"],
                "severity": issue["severity"],
            }
        else:
            result = invoke_delegate(issue, dry_run=False)

        results.append(result)
        log(f"Result: {result.get('status', '?')}")

    # ── 6. Save state ──
    state = load_state()
    state["last_run"] = datetime.now().isoformat()
    state["last_issues_detected"] = len(all_issues)
    state["last_fixable"] = len(fixable)
    state["last_results"] = [r.get("status") for r in results]
    state["dry_run"] = dry_run
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
