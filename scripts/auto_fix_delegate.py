#!/usr/bin/env python3
"""
auto_fix_delegate.py — Spawn headless Claude Code sessions for auto-remediation.

Called by homelab-triage.sh or autonomous_fixer.py when an issue is too complex
for simple bash fixes. This script:
  1. Runs safety pre-flight checks (cost guard, rate limit, concurrency lock)
  2. Enriches the issue with system context
  3. Creates a git snapshot for rollback
  4. Spawns a headless Claude Code session with a structured prompt
  5. Runs a post-fix health check
  6. Sends Telegram notification with results
  7. Logs the session for audit

Usage:
    python3 auto_fix_delegate.py --issue "Container X restarting 3x" --issue-type restart_loop
    python3 auto_fix_delegate.py --issue "Disk at 90%" --issue-type disk_space --dry-run
    python3 auto_fix_delegate.py --issue "Proxy down" --issue-type service_down --timeout 600

Safety guardrails:
    - Cost guard: verifies free model before spawning (via zero_cost_guard.py)
    - Rate limit: max 1 session per 30 minutes
    - Concurrency: flock prevents parallel sessions
    - No --dangerously-skip-permissions (Claude must ask for destructive ops)
    - Git snapshot before changes for clean rollback
    - Post-fix health check to verify improvement
    - Configurable timeout (default 900s / 15 min)
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
AG_HOME = Path("/home/rohit/agentharness")
STATE_DIR = HERMES_HOME / "state"
LOG_DIR = AG_HOME / "logs"
DATA_DIR = AG_HOME / "data"

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
DEFAULT_TIMEOUT = int(os.environ.get("CC_AUTO_FIX_TIMEOUT", "1800"))
RATE_LIMIT_SECONDS = 1800  # 30 minutes

LOCK_FILE = Path("/tmp/auto_fix_delegate.lock")
RATE_LIMIT_FILE = STATE_DIR / "auto_fix_rate_limit.json"
SESSION_LOG = STATE_DIR / "auto_fix_sessions.jsonl"
COST_GUARD_SCRIPT = Path.home() / ".claude" / "scripts" / "zero_cost_guard.py"
ENV_FILE = HERMES_HOME / ".env"

# Snapshot directories — what git tracks for rollback
SNAPSHOT_DIRS = [
    Path.home() / ".hermes",
    Path.home() / "agentharness",
    Path.home() / "services",
    Path.home() / ".claude",
]

# ---------------------------------------------------------------------------
# Issue classification — maps issue types to prompt templates + verification
# ---------------------------------------------------------------------------

ISSUE_TEMPLATES = {
    "restart_loop": {
        "keywords": ["restart", "restarting", "crash", "crashing", "loop", "restart loop"],
        "priority": "high",
        "investigation_steps": [
            "Check container status and restart count: docker ps -a --filter 'name=<container>'",
            "Check container logs: docker logs --tail 100 <container>",
            "Check for OOM kills: dmesg | grep -i oom",
            "Check docker inspect for exit codes",
            "Check network connectivity between dependent containers",
            "Check compose file for the service configuration",
        ],
        "fix_permissions": [
            "docker restart", "docker compose restart", "docker compose up -d",
            "systemctl --user restart", "sudo systemctl restart",
            "edit compose files", "edit config files",
        ],
        "verify_template": "docker inspect --format='{{.State.Status}} {{.State.Restarting}}' <container>",
    },
    "healthcheck_fail": {
        "keywords": ["healthcheck", "unhealthy", "health check", "failing", "health"],
        "priority": "high",
        "investigation_steps": [
            "Check which containers are unhealthy: docker ps --filter 'health=unhealthy'",
            "Check health check logs: docker inspect --format='{{json .State.Health}}' <container>",
            "Check service logs for errors",
            "Check network connectivity to dependencies",
            "Check if the health check command itself is correct in compose",
        ],
        "fix_permissions": [
            "docker restart", "docker compose restart", "docker compose up -d",
            "edit compose healthcheck config", "edit application config",
        ],
        "verify_template": "docker inspect --format='{{.State.Health.Status}}' <container>",
    },
    "service_down": {
        "keywords": ["down", "stopped", "not running", "unreachable", "timeout", "not responding"],
        "priority": "high",
        "investigation_steps": [
            "Check service/container status: docker ps -a | grep <service>",
            "Check logs: docker logs --tail 100 <service> or journalctl -u <service>",
            "Check systemd: systemctl status <service>",
            "Check dependencies (database, redis, etc.)",
            "Check port conflicts: ss -tlnp | grep <port>",
        ],
        "fix_permissions": [
            "docker restart", "docker compose up -d",
            "systemctl --user start", "sudo systemctl start",
            "docker compose restart",
        ],
        "verify_template": "docker inspect --format='{{.State.Status}}' <service>",
    },
    "disk_space": {
        "keywords": ["disk", "storage", "full", "space", "usage", "disk_space"],
        "priority": "medium",
        "investigation_steps": [
            "Check disk usage: df -h",
            "Check Docker disk: docker system df",
            "Find large directories: du -sh /* 2>/dev/null | sort -rh | head -20",
            "Check Docker volumes: docker system df -v",
            "Check log sizes: du -sh /var/log/* 2>/dev/null | sort -rh | head -10",
        ],
        "fix_permissions": [
            "docker system prune -f", "docker builder prune -f",
            "docker volume prune -f",
            "rm old log files", "truncate large logs",
        ],
        "verify_template": "df / | tail -1 | awk '{print $5}'",
    },
    "memory_pressure": {
        "keywords": ["memory", "ram", "oom", "out of memory", "pressure", "low memory"],
        "priority": "medium",
        "investigation_steps": [
            "Check memory: free -h",
            "Check top consumers: ps aux --sort=-%mem | head -20",
            "Check Docker container memory: docker stats --no-stream",
            "Check for OOM kills: dmesg | grep -i oom | tail -10",
            "Check swap usage",
        ],
        "fix_permissions": [
            "docker restart high-memory containers",
            "docker compose restart",
            "systemctl --user restart services",
        ],
        "verify_template": "free -g | awk '/^Mem:/{print $7}'",
    },
    "config_drift": {
        "keywords": ["drift", "config", "configuration", "mismatch", "documented", "docs"],
        "priority": "low",
        "investigation_steps": [
            "Compare running containers vs compose files",
            "Check for undocumented containers: docker ps vs compose config",
            "Verify port bindings match documentation",
            "Check service URLs are accessible",
        ],
        "fix_permissions": [
            "edit compose files", "edit config files",
            "docker compose up -d to match config",
        ],
        "verify_template": "docker ps --format '{{.Names}}' | wc -l",
    },
    "dependency_failure": {
        "keywords": ["dependency", "depends", "network", "dns", "resolve", "connect", "redis", "database", "db"],
        "priority": "high",
        "investigation_steps": [
            "Check all related containers are running: docker ps",
            "Check Docker networks: docker network ls",
            "Check DNS resolution from inside container: docker exec <container> python3 -c \"import socket; print(socket.gethostbyname('<dep>'))\"",
            "Check network connectivity: docker exec <container> nc -zv <dep> <port>",
            "Check compose network configuration",
        ],
        "fix_permissions": [
            "docker network connect", "docker compose restart",
            "edit compose network config", "docker compose up -d",
        ],
        "verify_template": "docker exec <container> python3 -c \"import socket; print(socket.gethostbyname('<dep>'))\"",
    },
    "missing_script": {
        "keywords": ["missing script", "script missing", "no such file", "cannot open file", "missing_script"],
        "priority": "high",
        "investigation_steps": [
            "Check if the script exists: ls -la <script_path>",
            "Search for it elsewhere: find /home/rohit -name '<script_name>' 2>/dev/null",
            "Check if it was moved to archive: find /home/rohit/.hermes/archive -name '<script_name>' 2>/dev/null",
            "Check git history for the script: git log --all --full-history -- <script_path>",
            "Verify the calling code's path reference is correct",
        ],
        "fix_permissions": [
            "cp from archive to expected location",
            "git checkout <script_path> to restore from git",
            "edit calling code to point to correct path",
            "create symlink from expected to actual location",
        ],
        "verify_template": "test -f <script_path> && echo 'EXISTS' || echo 'MISSING'",
    },
    "api_retry_failure": {
        "keywords": ["api retry", "connection error", "failed after.*retries", "api_retry_failure", "max retries", "all providers"],
        "priority": "high",
        "investigation_steps": [
            "Check proxy health: curl -s localhost:8080/health",
            "Check provider status: curl -s localhost:8080/v1/status | python3 -m json.tool",
            "Check which provider was active when the error occurred",
            "Review the error classifier logic in agent/error_classifier.py",
            "Check if _TRANSIENT_TRANSPORT_ERRORS includes the error type",
            "Check network connectivity to the provider endpoint",
            "Check proxy logs: tail -100 <log_file>",
        ],
        "fix_permissions": [
            "edit run_agent.py to fix retry logic",
            "edit agent/error_classifier.py to classify the error correctly",
            "systemctl --user restart proxy-server",
            "restart gateway",
        ],
        "verify_template": "curl -s localhost:8080/health && grep -c 'API call failed' <log_file>",
    },
    "ssl_cert_expiry": {
        "keywords": ["ssl", "cert", "certificate", "expir", "tls", "letsencrypt", "duckdns"],
        "priority": "high",
        "investigation_steps": [
            "Check cert expiry: find /home/rohit/services/data/nginx-proxy-manager/letsencrypt -name '*.pem' -exec openssl x509 -in {} -noout -enddate \\;",
            "List NPM cert files: ls -la /home/rohit/services/data/nginx-proxy-manager/letsencrypt/",
            "Check if cert auto-renewal is configured in NPM",
            "Check NPM logs for cert renewal errors: docker logs --tail 200 nginx-proxy-manager | grep -i cert",
        ],
        "fix_permissions": [
            "call NPM API to renew certificate",
            "docker restart nginx-proxy-manager",
            "edit NPM cert configuration",
        ],
        "verify_template": "find /home/rohit/services/data/nginx-proxy-manager/letsencrypt -name 'fullchain.pem' -exec sh -c 'openssl x509 -in \"$1\" -noout -enddate 2>/dev/null' _ {} \\;",
    },
    "oom_kill_pattern": {
        "keywords": ["oom", "out of memory", "killed process", "oom_kill"],
        "priority": "critical",
        "investigation_steps": [
            "Check recent OOM kills: dmesg | grep -i 'oom\\|killed process' | tail -20",
            "Check current memory: free -h",
            "Check top memory consumers: ps aux --sort=-%mem | head -20",
            "Check Docker container memory: docker stats --no-stream | head -20",
            "Check swap usage",
            "Check if any container has no memory limit set",
        ],
        "fix_permissions": [
            "docker restart high-memory containers",
            "systemctl --user restart services",
            "docker update --memory 512m <container>",
        ],
        "verify_template": "dmesg | grep -i oom | tail -5 && free -h | awk '/^Mem:/{print \"Available:\", $7}'",
    },
    "zombie_process": {
        "keywords": ["zombie", "defunct", "zombie_process"],
        "priority": "medium",
        "investigation_steps": [
            "Count zombies: ps -eo stat,pid,ppid,comm | grep '^Z' | wc -l",
            "Find zombie parents: ps -eo stat,pid,ppid,comm | grep '^Z' | awk '{print $3}' | sort | uniq -c",
            "Check parent process health: ps -p <parent_pid> -o pid,comm,stat",
            "Check if parent is a known service",
        ],
        "fix_permissions": [
            "kill -HUP <parent_pid>",
            "kill <parent_pid>",
            "systemctl --user restart <parent_service>",
        ],
        "verify_template": "ps -eo stat,pid,ppid,comm | grep '^Z' | wc -l",
    },
    "inode_exhaustion": {
        "keywords": ["inode", "inodes", "inode_exhaustion", "no space left"],
        "priority": "high",
        "investigation_steps": [
            "Check inode usage: df -i / /mnt/usb",
            "Find directories with many small files: find / -xdev -type d -size +1M 2>/dev/null | head -20",
            "Check /tmp file count: find /tmp -type f 2>/dev/null | wc -l",
            "Check Docker overlay: du -sh /var/lib/docker/overlay2",
            "Check log rotation status",
        ],
        "fix_permissions": [
            "find /tmp -mtime +3 -type f -delete",
            "docker system prune -f",
            "find /var/log -name '*.gz' -mtime +30 -delete",
            "truncate large log files",
        ],
        "verify_template": "df -i / | tail -1 | awk '{print $5}'",
    },
    "tmp_space": {
        "keywords": ["tmp", "tmp_space", "/tmp full", "/tmp space"],
        "priority": "high",
        "investigation_steps": [
            "Check /tmp usage: df -h /tmp || df -h /",
            "Find large /tmp files: find /tmp -type f -size +100M 2>/dev/null",
            "Find old /tmp files: find /tmp -mtime +7 -type f 2>/dev/null | wc -l",
            "Check which process is writing to /tmp: lsof +D /tmp 2>/dev/null | head -20",
        ],
        "fix_permissions": [
            "find /tmp -mtime +3 -type f -delete",
            "find /tmp -type f -size +500M -delete",
            "truncate large /tmp files",
        ],
        "verify_template": "df -h /tmp 2>/dev/null | tail -1 | awk '{print $5}'",
    },
    "port_conflict": {
        "keywords": ["port conflict", "port_conflict", "already in use", "address already"],
        "priority": "high",
        "investigation_steps": [
            "Check port usage: ss -tlnp",
            "Find conflicting listeners: ss -tlnp | grep ':<port>'",
            "Check Docker port mappings: docker ps --format '{{.Names}}: {{.Ports}}'",
            "Identify which service should own the port",
        ],
        "fix_permissions": [
            "docker stop <conflicting-container>",
            "docker compose restart",
            "edit compose port mappings",
            "systemctl stop <conflicting-service>",
        ],
        "verify_template": "ss -tlnp | grep ':<port>' | wc -l",
    },
    "volume_leak": {
        "keywords": ["volume leak", "volume_leak", "dangling volume", "unused volume"],
        "priority": "low",
        "investigation_steps": [
            "List dangling volumes: docker volume ls -f dangling=true",
            "Check volume disk usage: docker system df -v | head -30",
            "Check total Docker disk: docker system df",
            "Identify volumes not referenced by any container",
        ],
        "fix_permissions": [
            "docker volume prune -f",
            "docker volume rm <volume_name>",
        ],
        "verify_template": "docker volume ls -f dangling=true | wc -l",
    },
    "dns_resolution": {
        "keywords": ["dns", "resolution", "dns_resolution", "resolve", "dig", "nslookup", "pihole", "pi-hole"],
        "priority": "critical",
        "investigation_steps": [
            "Check Pi-hole container: docker inspect --format='{{.State.Status}}' pihole",
            "Check Pi-hole logs: docker logs --tail 100 pihole | grep -i error",
            "Test DNS: dig @127.0.0.1 google.com +short +time=5",
            "Check resolv.conf: cat /etc/resolv.conf",
            "Check for port 53 conflicts: ss -tlnp | grep ':53'",
        ],
        "fix_permissions": [
            "docker restart pihole",
            "docker compose -f <compose_file> restart pihole",
            "edit /etc/resolv.conf",
        ],
        "verify_template": "dig @127.0.0.1 google.com +short +time=5",
    },
    "duckdns_sync": {
        "keywords": ["duckdns", "ddns", "dns sync", "ip mismatch", "external ip", "duckdns_sync"],
        "priority": "high",
        "investigation_steps": [
            "Get current IP: curl -s https://api.ipify.org",
            "Get DNS record: dig +short chagulihome.duckdns.org @8.8.8.8",
            "Compare current IP vs DNS record",
            "Check if DuckDNS token file exists: cat ~/.duckdns_token",
            "Check if DuckDNS update cron job exists: crontab -l | grep -i duck",
        ],
        "fix_permissions": [
            "bash ~/.hermes/scripts/duckdns_update.sh",
            "curl 'https://www.duckdns.org/update?domains=chagulihome&token=$(cat ~/.duckdns_token)&ip='",
        ],
        "verify_template": "dig +short chagulihome.duckdns.org @8.8.8.8 && echo '---' && curl -s https://api.ipify.org",
    },
    "mcp_child_health": {
        "keywords": ["mcp", "mcp_child", "mcp_health", "mcp gateway", "8090", "8091"],
        "priority": "high",
        "investigation_steps": [
            "Check all MCP ports: for port in 8090 8091 8095 8097 8100 8102 8103 8104 8105 8106 8108; do echo -n \"Port $port: \"; echo > /dev/tcp/127.0.0.1/$port 2>/dev/null && echo UP || echo DOWN; done",
            "Check MCP gateway logs: docker logs --tail 50 mcp-gateway",
            "Check individual MCP container logs for the unreachable service",
            "Check if autoheal is restarting MCP containers in a loop",
        ],
        "fix_permissions": [
            "docker restart <mcp-container>",
            "docker compose -f /home/rohit/agentharness/docker-compose.mcp.yml restart",
            "edit MCP healthcheck config",
        ],
        "verify_template": "docker ps --filter 'name=mcp' --format '{{.Names}}: {{.Status}}'",
    },
    "timer_drift": {
        "keywords": ["timer", "timer_drift", "systemd timer", "n/a", "never fired"],
        "priority": "medium",
        "investigation_steps": [
            "List all timers: systemctl list-timers --all --no-pager",
            "Check specific timer: systemctl status <timer>",
            "Check timer trigger: systemctl show <timer> --property=LastTriggerUSec,NextElapseUSec",
            "Check if the timer's service exists: systemctl cat <timer>",
        ],
        "fix_permissions": [
            "systemctl restart <timer>",
            "systemctl start <timer>",
            "edit timer schedule",
            "systemctl daemon-reload",
        ],
        "verify_template": "systemctl show <timer> --property=LastTriggerUSec",
    },
    "git_drift": {
        "keywords": ["git", "git_drift", "uncommitted", "local behind", "dirty repo"],
        "priority": "low",
        "investigation_steps": [
            "Check git status in all repos: for d in ~/.hermes/hermes-agent ~/agentharness ~/services; do echo \"=== $d ===\"; git -C $d status --porcelain 2>/dev/null; done",
            "Check if local is behind remote: git -C <repo> fetch --dry-run 2>&1",
            "Check age of oldest uncommitted change",
        ],
        "fix_permissions": [
            "git add -A && git commit -m '<message>' && git push",
            "git pull --rebase",
            "git checkout -- <discard_uncommitted>",
        ],
        "verify_template": "git -C <repo> status --porcelain | wc -l",
    },
    "api_key_invalid": {
        "keywords": ["api key", "api_key_invalid", "key missing", "key empty", "credential"],
        "priority": "critical",
        "investigation_steps": [
            "Check env vars: env | grep -i 'API_KEY\\|TOKEN' | head -20",
            "Check .env file: cat ~/.hermes/.env | grep -i 'API_KEY\\|TOKEN' | head -20",
            "Check if keys are expired by testing provider endpoints",
            "Check provider dashboards for key status",
        ],
        "fix_permissions": [
            "edit ~/.hermes/.env to add/update keys",
            "ALERT HUMAN: API keys must be manually regenerated from provider dashboards",
        ],
        "verify_template": "env | grep -c 'API_KEY'",
    },
}


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Auto-rollback
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] auto-fix-delegate: {msg}"
    print(line, file=sys.stderr)
    # Also append to log file
    log_file = LOG_DIR / "auto_fix_delegate.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a") as f:
        f.write(line + "\n")


def load_env() -> dict:
    """Load environment variables from ~/.hermes/.env"""
    env = dict(os.environ)
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key not in env:
                    env[key] = val
    return env


def send_telegram(message: str):
    """Send notification via Telegram bot API using curl."""
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_HOME_CHANNEL", "")

    if not token or not chat_id:
        log("Telegram: token or chat_id not configured — skipping")
        return

    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()

    try:
        result = subprocess.run(
            ["curl", "-s", "-X", "POST",
             f"https://api.telegram.org/bot{token}/sendMessage",
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            log(f"Telegram send failed: {result.stderr[:200]}")
        else:
            log("Telegram notification sent")
    except Exception as e:
        log(f"Telegram send error: {e}")


def read_json_safe(path: Path) -> dict:
    """Read a JSON file, return empty dict on any error."""
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------------------
# Safety pre-flight checks
# ---------------------------------------------------------------------------

def check_cost_guard() -> tuple[bool, str]:
    """Verify the current Claude model is free. Returns (passed, reason)."""
    if not COST_GUARD_SCRIPT.exists():
        log("WARNING: zero_cost_guard.py not found — skipping cost check")
        return True, "cost guard script not found, proceeding"

    try:
        result = subprocess.run(
            [sys.executable, str(COST_GUARD_SCRIPT), "check"],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout + result.stderr
        if "BLOCKED" in output or "PAID" in output:
            return False, "Cost guard: model is paid — aborting auto-fix"
        return True, "model verified free"
    except subprocess.TimeoutExpired:
        return True, "cost guard timed out, proceeding with caution"
    except Exception as e:
        return True, f"cost guard error ({e}), proceeding with caution"


def check_rate_limit() -> tuple[bool, str]:
    """Check if enough time has passed since last session. Returns (allowed, reason)."""
    record = read_json_safe(RATE_LIMIT_FILE)
    if record.get("last_session_ts"):
        elapsed = time.time() - record["last_session_ts"]
        if elapsed < RATE_LIMIT_SECONDS:
            remaining = int(RATE_LIMIT_SECONDS - elapsed)
            return False, f"Rate limited: {remaining}s since last session (min {RATE_LIMIT_SECONDS}s)"
    return True, "rate limit OK"


def update_rate_limit():
    """Record that a session was started."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    RATE_LIMIT_FILE.write_text(json.dumps({
        "last_session_ts": time.time(),
        "last_session_id": datetime.now().isoformat(),
    }))


def acquire_lock() -> tuple[bool, int | None]:
    """Try to acquire the flock. Returns (acquired, fd)."""
    import fcntl
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_WRONLY)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True, fd
    except (IOError, OSError):
        if 'fd' in dir():
            os.close(fd)
        return False, None


def release_lock(fd: int):
    """Release the flock."""
    import fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Issue classification
# ---------------------------------------------------------------------------

def classify_issue(issue: str, hint_type: str = "auto-detect") -> dict:
    """Classify an issue and return the template config."""
    if hint_type != "auto-detect" and hint_type in ISSUE_TEMPLATES:
        return {"type": hint_type, **ISSUE_TEMPLATES[hint_type]}

    lower = issue.lower()
    for issue_type, config in ISSUE_TEMPLATES.items():
        if any(kw in lower for kw in config["keywords"]):
            return {"type": issue_type, **config}

    # Default: general investigation
    return {
        "type": "general",
        "priority": "medium",
        "investigation_steps": [
            "Gather system information: docker ps, systemctl --failed, df -h, free -h",
            "Check recent logs for errors",
            "Identify the root cause",
            "Apply targeted fix",
        ],
        "fix_permissions": [
            "docker restart", "docker compose restart", "docker compose up -d",
            "systemctl --user restart", "sudo systemctl restart",
            "edit config files",
        ],
        "verify_template": "docker ps --format '{{.Names}}: {{.Status}}'",
    }


# ---------------------------------------------------------------------------
# Context enrichment
# ---------------------------------------------------------------------------

def enrich_context(issue: str, issue_type: str) -> str:
    """Build rich system context for the Claude prompt."""
    parts = []

    # 1. Docker container overview
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.State}}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            parts.append("## Docker Containers")
            parts.append("```")
            parts.extend(lines[:30])  # Cap at 30 lines
            parts.append("```")
    except Exception:
        pass

    # 2. Unhealthy containers
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "health=unhealthy", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
        unhealthy = [l for l in result.stdout.strip().split("\n") if l]
        if unhealthy:
            parts.append(f"\n## Unhealthy Containers: {', '.join(unhealthy)}")
    except Exception:
        pass

    # 3. Systemd failed services
    try:
        result = subprocess.run(
            ["systemctl", "--failed", "--no-legend"],
            capture_output=True, text=True, timeout=10,
        )
        failed = [l for l in result.stdout.strip().split("\n") if l and "0 loaded" not in l]
        if failed:
            parts.append(f"\n## Failed Systemd Services:")
            parts.extend(failed[:10])
    except Exception:
        pass

    # 4. Disk usage
    try:
        result = subprocess.run(["df", "-h", "/", "/mnt/usb"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            parts.append("\n## Disk Usage")
            parts.append("```")
            parts.append(result.stdout.strip())
            parts.append("```")
    except Exception:
        pass

    # 5. Memory
    try:
        result = subprocess.run(["free", "-h"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            parts.append("\n## Memory")
            parts.append("```")
            parts.append(result.stdout.strip())
            parts.append("```")
    except Exception:
        pass

    # 6. Docker networks (useful for dependency_failure issues)
    if issue_type in ("dependency_failure", "restart_loop", "healthcheck_fail"):
        try:
            result = subprocess.run(
                ["docker", "network", "ls", "--format", "{{.Name}}\t{{.Driver}}\t{{.Scope}}"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                parts.append("\n## Docker Networks")
                parts.append("```")
                parts.append(result.stdout.strip())
                parts.append("```")
        except Exception:
            pass

    # 7. Recent errors from journal (last 5 min)
    try:
        result = subprocess.run(
            ["journalctl", "--since", "5 min ago", "--priority=err", "--no-pager", "-n", "20"],
            capture_output=True, text=True, timeout=10,
        )
        errors = result.stdout.strip()
        if errors and len(errors) > 10:
            parts.append("\n## Recent System Errors (last 5 min)")
            parts.append("```")
            parts.extend(errors.split("\n")[-15:])
            parts.append("```")
    except Exception:
        pass

    # 8. Ambient status from state files
    ambient = read_json_safe(STATE_DIR / "ambient_status.json")
    if ambient:
        score = ambient.get("health_score", "?")
        label = ambient.get("health_label", "?")
        parts.append(f"\n## Health Score: {score}/100 ({label})")
        resources = ambient.get("resources", {})
        if resources:
            parts.append(f"Disk: {resources.get('disk_pct', '?')}% | "
                         f"Memory: {resources.get('memory_pct', '?')}% | "
                         f"Docker: {resources.get('docker_running', '?')} running")

    return "\n".join(parts) if parts else "(no additional context available)"


# ---------------------------------------------------------------------------
# Git snapshot
# ---------------------------------------------------------------------------

def create_git_snapshot(issue_short: str) -> str:
    """Create a git snapshot of config directories. Returns commit hash or 'none'."""
    try:
        # Use the hermes-agent monorepo as the git repo
        git_dir = HERMES_HOME / "hermes-agent"
        if not (git_dir / ".git").exists():
            git_dir = Path.home()

        # Stage all snapshot dirs that exist
        for d in SNAPSHOT_DIRS:
            if d.exists():
                subprocess.run(
                    ["git", "-C", str(git_dir), "add", "--ignore-errors", "-A", str(d)],
                    capture_output=True, text=True, timeout=30,
                )

        # Commit
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        result = subprocess.run(
            ["git", "-C", str(git_dir), "commit", "-m",
             f"auto-fix-snapshot: before fixing '{issue_short[:60]}' [{ts}]",
             "--allow-empty"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            # Get the commit hash
            hash_result = subprocess.run(
                ["git", "-C", str(git_dir), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            commit_hash = hash_result.stdout.strip()
            log(f"Git snapshot created: {commit_hash}")
            return commit_hash
        else:
            log(f"Git snapshot: nothing to commit or error: {result.stderr[:100]}")
            return "none"
    except Exception as e:
        log(f"Git snapshot failed: {e}")
        return "none"


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_prompt(issue: str, issue_type: str, context: str, template: dict, commit_hash: str) -> str:
    """Build the full structured prompt for Claude."""
    steps = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(template["investigation_steps"]))
    perms = "\n".join(f"  - {p}" for p in template["fix_permissions"])
    verify = template["verify_template"]
    priority = template["priority"]

    # Extract container/service name from issue if possible
    container_hint = ""
    words = issue.split()
    for w in words:
        clean = w.strip("():,.")
        if clean and not clean.lower() in ("container", "service", "is", "has", "been", "the", "a", "an", "not"):
            container_hint = clean
            break

    prompt = f"""## AUTO-FIX SESSION — HOMELAB REMEDIATION

### ISSUE
{issue}

### CLASSIFICATION
Type: {issue_type} | Priority: {priority}

### SYSTEM CONTEXT
{context}

### YOUR TASK
You are an automated remediation agent for a Debian 13 homelab server. Investigate and fix the issue above.

#### Investigation Steps:
{steps}

#### You MAY:
{perms}

#### You MUST NOT:
  - Delete data volumes or databases
  - Modify firewall rules (UFW)
  - Run apt upgrade or dist-upgrade
  - Delete Docker images that are in use
  - Edit CLAUDE.md or documentation files
  - Modify SSH configuration
  - Change user passwords or authentication

#### Safety Rules:
  - Working directory: /home/rohit
  - Git snapshot already created: {commit_hash} (rollback: git checkout {commit_hash})
  - If a fix requires a restart, wait 10 seconds then verify health
  - Keep changes minimal and targeted
  - If unsure about a step, skip it and report it in the summary

#### Post-Fix Verification:
Run this check and include the output in your summary:
  {verify}

### OUTPUT FORMAT
End your response with a structured summary:
  - Root cause: <one sentence>
  - Actions taken: <numbered list>
  - Verification: <command output + pass/fail>
  - Rollback: git checkout {commit_hash} if needed
"""
    return prompt


# ---------------------------------------------------------------------------
# Claude invocation
# ---------------------------------------------------------------------------

def spawn_claude(prompt: str, timeout: int) -> dict:
    """Spawn a headless Claude Code session. Returns result dict."""
    sid = f"auto-{str(uuid.uuid4())[:8]}"

    cmd = [
        CLAUDE_BIN,
        "--print",
        "--output-format", "json",
        "--add-dir", "/home:/home",
        "--", prompt,
    ]

    log(f"Spawning Claude session {sid} (timeout={timeout}s)")
    log(f"Command: {' '.join(cmd[:6])}... (prompt: {len(prompt)} chars)")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(Path.home()),
            start_new_session=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(Path.home())},
        )

        if result.returncode == 0:
            try:
                output = json.loads(result.stdout)
                # Claude JSON output has a "result" field with the text
                summary = output.get("result", result.stdout[:2000])
            except json.JSONDecodeError:
                summary = result.stdout[:2000] if result.stdout else "(no output)"

            # Empty / no-op sessions are NOT successes — never report COMPLETED.
            log(f"Claude session {sid} completed successfully")
            if not summary or not str(summary).strip():
                return {
                    "status": "failed",
                    "session_id": sid,
                    "summary": "",
                    "raw_output": result.stdout[:5000],
                    "error": "Delegate returned an empty result (no action performed)",
                }
            return {
                "status": "completed",
                "session_id": sid,
                "summary": str(summary)[:3000],
                "raw_output": result.stdout[:5000],
            }
        else:
            error = result.stderr[:1000] if result.stderr else f"Exit code {result.returncode}"
            log(f"Claude session {sid} failed: {error}")
            return {
                "status": "failed",
                "session_id": sid,
                "error": error,
            }

    except subprocess.TimeoutExpired:
        log(f"Claude session {sid} timed out after {timeout}s")
        try:
            os.killpg(os.getpgid(result.pid), signal.SIGKILL)
        except (ProcessLookupError, AttributeError):
            pass
        return {
            "status": "timeout",
            "session_id": sid,
            "error": f"Session timed out after {timeout} seconds",
        }
    except Exception as e:
        log(f"Claude session {sid} error: {e}")
        return {
            "status": "error",
            "session_id": sid,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Post-fix health check
# ---------------------------------------------------------------------------

def post_fix_health_check(issue_type: str, verify_template: str, issue: str = "") -> dict:
    """Run a targeted health check after the fix. Returns {passed, output}."""
    # For container-level issues, verify the specific container is actually up.
    # A bare `docker ps` exit code is NOT a valid health signal — it passes even
    # when the target container is still down.
    if issue_type in ("service_down", "dependency_failure", "restart_loop", "healthcheck_fail"):
        name = _extract_container_name(issue)
        if name:
            cmd = (
                "docker ps --filter name=^/{name}$ --format '{{.Names}}: {{.Status}}'".format(name=name)
            )
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
            output = result.stdout.strip()
            passed = bool(output) and "Up" in output and "Restarting" not in output
            return {"passed": passed, "output": output or f"container '{name}' not running"}

    # Build a simple check based on issue type
    checks = {
        "restart_loop": "docker ps --filter 'status=restarting' --format '{{.Names}}'",
        "healthcheck_fail": "docker ps --filter 'health=unhealthy' --format '{{.Names}}'",
        "service_down": "docker ps --format '{{.Names}}: {{.Status}}' | head -20",
        "disk_space": "df / | tail -1 | awk '{print $5}'",
        "memory_pressure": "free -g | awk '/^Mem:/{print \"Available: \" $7 \"GB\"}'",
        "config_drift": "docker ps --format '{{.Names}}' | wc -l",
        "dependency_failure": "docker ps --format '{{.Names}}: {{.Status}}' | head -20",
        "general": "docker ps --format '{{.Names}}: {{.Status}}' | head -10",
    }

    cmd = checks.get(issue_type, checks["general"])
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=15,
        )
        output = result.stdout.strip()
        # For restart_loop and healthcheck_fail, empty output = good (no unhealthy)
        if issue_type in ("restart_loop", "healthcheck_fail"):
            passed = not output  # empty = no unhealthy containers
        elif issue_type == "disk_space":
            try:
                pct = int(output.replace("%", ""))
                passed = pct < 90
            except ValueError:
                passed = True
        else:
            passed = result.returncode == 0

        return {"passed": passed, "output": output}
    except Exception as e:
        return {"passed": False, "output": str(e)}


def _extract_container_name(issue: str) -> str:
    """Extract a container name from an issue string like 'Container mentedb has exited'."""
    if not issue:
        return ""
    text = issue.strip()
    # Patterns: "Container X has exited", "X has exited", "Container X ...", "X down"
    for prefix in ("Container ", "container "):
        if text.startswith(prefix):
            rest = text[len(prefix):]
            return rest.split()[0] if rest.split() else ""
    for marker in (" has exited", " is down", " has failed", " is restarting"):
        if marker in text:
            name = text.split(marker)[0].strip()
            return name.split()[0] if name.split() else ""
    return ""


# ---------------------------------------------------------------------------
# Session logging
# ---------------------------------------------------------------------------

def log_session(result: dict):
    """Write session record to JSONL log."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now().isoformat(),
        **result,
    }
    with open(SESSION_LOG, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")
    log(f"Session logged: {result.get('session_id', '?')} status={result.get('status', '?')}")


# ---------------------------------------------------------------------------
# Telegram report
# ---------------------------------------------------------------------------

def build_telegram_report(result: dict, issue: str, issue_type: str, duration: float,
                          commit_hash: str, health_check: dict) -> str:
    """Build a structured Telegram message."""
    status = result.get("status", "unknown")

    if status == "completed":
        icon = "✅"
        status_text = "COMPLETED"
    elif status == "timeout":
        icon = "⏰"
        status_text = "TIMED OUT"
    elif status == "failed":
        icon = "❌"
        status_text = "FAILED"
    else:
        icon = "⚠️"
        status_text = status.upper()

    health_icon = "✅" if health_check.get("passed") else "⚠️"

    summary = result.get("summary", "")
    # Truncate summary for Telegram (max ~3000 chars for the whole message)
    if len(summary) > 1500:
        summary = summary[:1500] + "\n... (truncated)"

    msg = f"""{icon} <b>AUTO-FIX {status_text}</b>

<b>Issue:</b> {issue[:200]}
<b>Type:</b> {issue_type} | <b>Session:</b> {result.get('session_id', '?')}
<b>Duration:</b> {duration:.0f}s | <b>Health:</b> {health_icon} {'PASS' if health_check.get('passed') else 'CHECK NEEDED'}

{summary}

<b>Rollback:</b> <code>git checkout {commit_hash}</code>"""

    if result.get("error"):
        msg += f"\n\n<b>Error:</b> {result['error'][:300]}"

    return msg


def build_telegram_pre_report(issue: str, issue_type: str, session_id: str) -> str:
    """Build a 'session started' Telegram message."""
    return f"""🤖 <b>AUTO-FIX SESSION STARTED</b>

<b>Issue:</b> {issue[:200]}
<b>Type:</b> {issue_type}
<b>Session:</b> {session_id}
<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Investigating and fixing...


This may take up to 15 minutes."""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Auto-fix delegate — spawn headless Claude for complex remediation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--issue", "-i", required=True, help="Issue description")
    parser.add_argument("--issue-type", "-t", default="auto-detect",
                        help="Issue type hint (restart_loop, healthcheck_fail, disk_space, etc.)")
    parser.add_argument("--source", "-s", default="autonomous_fixer",
                        help="What invoked this (homelab-triage, autonomous_fixer, health_check)")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Print what would be done without spawning Claude")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"Max seconds for Claude session (default {DEFAULT_TIMEOUT})")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    args = parser.parse_args()

    issue = args.issue
    issue_type = args.issue_type
    source = args.source
    dry_run = args.dry_run
    timeout = args.timeout
    sid = f"auto-{str(uuid.uuid4())[:8]}"
    start_time = time.time()

    log(f"=== Auto-fix session {sid} ===")
    log(f"Issue: {issue}")
    log(f"Type: {issue_type}, Source: {source}, Dry-run: {dry_run}")

    # ── 1. Classify issue ──
    template = classify_issue(issue, issue_type)
    actual_type = template["type"]
    log(f"Classified as: {actual_type} (priority: {template['priority']})")

    # ── 2. Safety pre-flight checks ──
    # 2a. Cost guard
    cost_ok, cost_reason = check_cost_guard()
    log(f"Cost guard: {cost_reason}")
    if not cost_ok:
        result = {"status": "aborted", "session_id": sid, "error": cost_reason,
                  "issue": issue, "issue_type": actual_type, "source": source}
        log_session(result)
        send_telegram(f"🚫 <b>AUTO-FIX ABORTED</b>\n\n<b>Issue:</b> {issue}\n<b>Reason:</b> {cost_reason}")
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        sys.exit(1)

    # 2b. Rate limit
    rate_ok, rate_reason = check_rate_limit()
    log(f"Rate limit: {rate_reason}")
    if not rate_ok:
        result = {"status": "rate_limited", "session_id": sid, "error": rate_reason,
                  "issue": issue, "issue_type": actual_type, "source": source}
        log_session(result)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        sys.exit(0)  # Not an error, just skip

    # 2c. Concurrency lock
    lock_acquired, lock_fd = acquire_lock()
    if not lock_acquired:
        result = {"status": "locked", "session_id": sid,
                  "error": "Another auto-fix session is already running",
                  "issue": issue, "issue_type": actual_type, "source": source}
        log_session(result)
        log("Another session is running — aborting")
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        sys.exit(0)

    try:
        # ── 3. Enrich context ──
        context = enrich_context(issue, actual_type)
        log(f"Context gathered: {len(context)} chars")

        # ── 4. Git snapshot ──
        issue_short = issue[:80]
        commit_hash = create_git_snapshot(issue_short)
        log(f"Git snapshot: {commit_hash}")

        # ── 5. Build prompt ──
        prompt = build_prompt(issue, actual_type, context, template, commit_hash)
        log(f"Prompt built: {len(prompt)} chars")

        if dry_run:
            # ── Dry run: just print what would be done ──
            log("DRY RUN — not spawning Claude")
            result = {
                "status": "dry_run",
                "session_id": sid,
                "issue": issue,
                "issue_type": actual_type,
                "source": source,
                "prompt_length": len(prompt),
                "commit_hash": commit_hash,
                "prompt_preview": prompt[:500],
            }
            log_session(result)

            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"[DRY RUN] Session: {sid}")
                print(f"  Issue: {issue}")
                print(f"  Type: {actual_type}")
                print(f"  Priority: {template['priority']}")
                print(f"  Timeout: {timeout}s")
                print(f"  Git snapshot: {commit_hash}")
                print(f"  Prompt ({len(prompt)} chars):")
                print(f"  {prompt[:400]}...")
            sys.exit(0)

        # ── 6. Send "session started" Telegram notification ──
        send_telegram(build_telegram_pre_report(issue, actual_type, sid))

        # ── 7. Update rate limit ──
        update_rate_limit()

        # ── 8. Spawn Claude ──
        cc_result = spawn_claude(prompt, timeout)
        cc_result["issue"] = issue
        cc_result["issue_type"] = actual_type
        cc_result["source"] = source
        cc_result["commit_hash"] = commit_hash

        # ── 9. Post-fix health check ──
        health = post_fix_health_check(actual_type, template.get("verify_template", ""), issue)
        cc_result["health_check"] = health
        log(f"Post-fix health check: {'PASS' if health.get('passed') else 'FAIL'} — {health.get('output', '')[:100]}")

        # ── 10. Calculate duration ──
        duration = time.time() - start_time
        cc_result["duration_s"] = round(duration, 1)

        # ── 11. Log session ──
        log_session(cc_result)

        # ── 12. Send Telegram report ──
        report = build_telegram_report(cc_result, issue, actual_type, duration, commit_hash, health)
        send_telegram(report)

        # ── 13. Output result ──
        if args.json:
            print(json.dumps(cc_result, indent=2, default=str))
        else:
            print(f"Session: {sid}")
            print(f"Status: {cc_result['status']}")
            print(f"Duration: {duration:.0f}s")
            if cc_result.get("summary"):
                print(f"Summary:\n{cc_result['summary'][:1000]}")
            if cc_result.get("error"):
                print(f"Error: {cc_result['error']}")

        # Exit code based on status
        if cc_result["status"] == "completed":
            sys.exit(0)
        else:
            sys.exit(1)

    finally:
        release_lock(lock_fd)


if __name__ == "__main__":
    main()
