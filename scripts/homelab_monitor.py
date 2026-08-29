#!/usr/bin/env python3
"""homelab_monitor.py — Proactive monitoring, run by cron every 15 minutes.

Calls check_all_health(), analyzes results, takes AUTO-tier actions,
sends notifications for issues found, logs everything to SQLite incidents DB.

Guardrail tiers:
  AUTO    — Fix immediately without human approval (container restart, disk cleanup, etc.)
  NOTIFY  — Send notification, do not fix automatically
  CONFIRM — Send notification and wait for human confirmation (not acted on here)

Cron entry (example):
  */15 * * * * /usr/bin/python3 /home/rohit/.hermes/hermes-agent/scripts/homelab_monitor.py >> /home/rohit/agentharness/data/logs/hermes_monitor_cron.log 2>&1
"""

import json
import sys
import time
from pathlib import Path

# Ensure the current directory is on the import path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import hashlib
import urllib.error
import urllib.request

import homelab_ops as ops

# ---------------------------------------------------------------------------
# Application-level health checks (beyond container status)
# ---------------------------------------------------------------------------

# HTTP endpoints to verify after container is running.
# Maps container_name -> (url, expected_min_status, expected_max_status)
SERVICE_HTTP_CHECKS = {
    "homepage": ("http://127.0.0.1:7575", 200, 399),
    "paperless": ("http://127.0.0.1:8000", 200, 399),
    "uptime-kuma": ("http://127.0.0.1:3002", 200, 399),
    "gitea": ("http://127.0.0.1:3001", 200, 399),
    "stump": ("http://127.0.0.1:10801", 200, 399),
    "netdata": ("http://127.0.0.1:19999/api/v1/info", 200, 399),
    "n8n": ("http://127.0.0.1:5678/healthz", 200, 399),
}

# Config drift detection: container -> (config_path_in_container, min_expected_size_bytes, backup_path)
CONFIG_BACKUP_DIR = Path("/home/rohit/shared_agent_memory/config_backups")
SERVICE_CONFIG_CHECKS = {
    "mcp-gateway": {
        "container_path": "/data/gateway_state.json",
        "schema_key": None,
        "min_apps": 10,
        "backup_path": CONFIG_BACKUP_DIR / "mcp-gateway_state.json.bak",
    },
}

# Disk cleanup threshold: auto-clean if any partition exceeds this %
DISK_CLEANUP_THRESHOLD = 85

# ---------------------------------------------------------------------------
# Guardrail classification
# ---------------------------------------------------------------------------

# AUTO: safe to fix without asking
# NOTIFY: tell Rohit, do not auto-fix
# CONFIRM: tell Rohit and wait (monitor script just notifies)

def classify_issue(issue_text: str, report: dict) -> str:
    """Return guardrail tier for an issue string: AUTO, NOTIFY, LOG, or SILENT."""
    text = issue_text.lower()

    # Stopped containers are safe to restart
    if "containers down" in text:
        stopped = report.get("containers", {}).get("stopped", [])
        # If more than 5 containers are down, something systemic -- notify instead
        if len(stopped) > 5:
            return "NOTIFY"
        return "AUTO"

    # Unhealthy containers -- restart them
    if "unhealthy" in text:
        return "AUTO"

    # LLM proxy/local LLM down -- notify
    if "proxy" in text or "local llm" in text:
        return "NOTIFY"

    # Disk critical -- auto-clean if threshold exceeded
    if "disk critical" in text or "disk " in text:
        return "AUTO"

    # Swap high -- just log, common in homelab
    if "swap" in text:
        return "LOG"

    # Watchdog failures -- log unless they are persistent
    if "watchdog" in text:
        return "LOG"

    # Network issues -- notify
    if "dns" in text or "internet" in text or "ssl" in text:
        return "NOTIFY"

    # Trend issues -- notify
    if "jumped" in text or "climbed" in text:
        return "NOTIFY"

    # Log scan findings -- notify
    if "error" in text or "oom" in text or "crash" in text or "fatal" in text:
        return "NOTIFY"

    # Backup stale -- notify
    if "backup" in text and "stale" in text:
        return "NOTIFY"

    return "LOG"


# ---------------------------------------------------------------------------
# AUTO-fix actions
# ---------------------------------------------------------------------------

def auto_fix_stopped_containers(report: dict) -> list:
    """Restart stopped containers. Returns list of action result dicts."""
    stopped = report.get("containers", {}).get("stopped", [])
    results = []
    for name in stopped:
        ops._log(f"monitor: AUTO-restarting stopped container: {name}")
        result = ops.restart_service(name)
        results.append({"container": name, "action": "restart", "result": result})
        ops.log_incident(name, "Container was stopped, auto-restarted", "WARNING", "restart", result.get("ok", False))
    return results


def auto_fix_unhealthy_containers(report: dict) -> list:
    """Restart unhealthy containers. Returns list of action result dicts."""
    unhealthy = report.get("containers", {}).get("unhealthy", [])
    results = []
    for name in unhealthy:
        ops._log(f"monitor: AUTO-restarting unhealthy container: {name}")
        result = ops.restart_service(name)
        results.append({"container": name, "action": "restart", "result": result})
        ops.log_incident(name, "Container was unhealthy, auto-restarted", "WARNING", "restart", result.get("ok", False))
    return results


def auto_cleanup_disk_action(report: dict) -> list:
    """Auto-clean disk if usage exceeds threshold."""
    results = []
    disk = report.get("disk", {})
    critical = disk.get("critical_partitions", [])
    for part in critical:
        if part["used_pct"] >= DISK_CLEANUP_THRESHOLD:
            ops._log(f"monitor: AUTO-cleaning disk for {part['mount']} ({part['used_pct']}%)")
            cleanup = ops.auto_cleanup_disk(dry_run=False)
            results.append({
                "container": "system",
                "action": "disk_cleanup",
                "mount": part["mount"],
                "result": cleanup,
            })
            ops.log_incident("system", f"Disk cleanup triggered for {part['mount']} at {part['used_pct']}%",
                             "WARNING", f"pruned {len(cleanup.get('actions', []))} items", True)
    return results


# ---------------------------------------------------------------------------
# HTTP health checks
# ---------------------------------------------------------------------------

def check_service_http_health() -> list:
    """Hit each service's HTTP endpoint. Returns list of (name, ok, detail) tuples."""
    results = []
    for name, (url, min_status, max_status) in SERVICE_HTTP_CHECKS.items():
        try:
            req = urllib.request.Request(url, method="GET")
            resp = urllib.request.urlopen(req, timeout=10)
            code = resp.getcode()
            ok = min_status <= code <= max_status
            results.append((name, ok, f"HTTP {code}"))
        except urllib.error.HTTPError as e:
            code = e.code
            ok = min_status <= code <= max_status
            results.append((name, ok, f"HTTP {code}"))
        except Exception as e:
            results.append((name, False, str(e)[:200]))
    return results


# ---------------------------------------------------------------------------
# Config drift detection and auto-fix
# ---------------------------------------------------------------------------

def check_config_drift() -> list:
    """Check if service configs have been reset to defaults. Returns list of issues."""
    issues = []
    for name, cfg in SERVICE_CONFIG_CHECKS.items():
        try:
            # Read current config from container
            rc, out, err = ops._run(
                f"docker cp {name}:{cfg['container_path']} /tmp/_config_check_{name}.json"
            )
            if rc != 0:
                ops._log(f"monitor: config check skipped for {name}: {err}")
                continue

            import json
            with open(f"/tmp/_config_check_{name}.json") as f:
                current = json.load(f)

            # schema_key can be a nested key (e.g. "services") or None to count top-level keys
            if cfg.get("schema_key"):
                item_count = len(current.get(cfg["schema_key"], []))
            else:
                item_count = len(current)
            if item_count < cfg["min_apps"]:
                issues.append({
                    "container": name,
                    "detail": f"Config drift detected: {cfg['schema_key']} count = {item_count} (expected >= {cfg['min_apps']})",
                    "backup_path": str(cfg["backup_path"]),
                })
        except Exception as e:
            ops._log(f"monitor: config check error for {name}: {e}")
    return issues


def auto_fix_config_drift(drift_issues: list) -> list:
    """Restore configs from backup and restart affected containers."""
    results = []
    for issue in drift_issues:
        name = issue["container"]
        backup = issue["backup_path"]

        if not Path(backup).exists():
            ops._log(f"monitor: no backup found for {name} at {backup}, cannot auto-fix")
            results.append({"container": name, "action": "config_restore", "ok": False, "detail": "no backup"})
            ops.log_incident(name, f"Config drift detected but no backup at {backup}", "CRITICAL", "restore_failed", False)
            continue

        ops._log(f"monitor: restoring config for {name} from {backup}")
        cfg = SERVICE_CONFIG_CHECKS[name]

        # Copy backup into container
        rc, _, err = ops._run(f"docker cp {backup} {name}:{cfg['container_path']}")
        if rc != 0:
            ops._log(f"monitor: config restore failed for {name}: {err}")
            results.append({"container": name, "action": "config_restore", "ok": False, "detail": err})
            ops.log_incident(name, f"Config restore failed: {err}", "CRITICAL", "restore_failed", False)
            continue

        # Restart the container
        restart_result = ops.restart_service(name)
        ok = restart_result.get("ok", False)
        ops._log(f"monitor: config restore + restart for {name}: {'OK' if ok else 'FAILED'}")
        results.append({"container": name, "action": "config_restore", "ok": ok, "detail": "restored from backup"})
        ops.log_incident(name, "Config drift auto-fixed from backup", "WARNING", "config_restore", ok)

    return results




# ---------------------------------------------------------------------------
# Alert dedup with lifecycle check
# ---------------------------------------------------------------------------

def _compute_alert_id(issue_text: str, source: str = "monitor") -> str:
    """Compute a stable alert ID for dedup/lifecycle."""
    raw = f"{source}:{issue_text}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def run_monitor():
    """Execute one monitoring pass. Designed to be called from cron."""
    start = time.monotonic()
    ops._log("=" * 60)
    ops._log("monitor: starting health check pass")

    # Ensure incident DB exists
    ops._init_incident_db()

    # 1. Run full health check
    report = ops.check_all_health()

    auto_actions = []
    notifications = []

    issues = report.get("issues", [])
    if not issues:
        ops._log("monitor: all checks passed")
    else:
        ops._log(f"monitor: found {len(issues)} issue(s)")

    # 2. Classify and act on each issue
    for issue in issues:
        tier = classify_issue(issue, report)
        alert_id = _compute_alert_id(issue)
        ops._log(f"monitor: [{tier}] {issue}")

        # Check if this alert is suppressed (acknowledged/snoozed)
        if ops.is_alert_suppressed(alert_id):
            ops._log(f"monitor: suppressed alert {alert_id}: {issue[:60]}")
            continue

        if tier == "AUTO":
            if "containers down" in issue.lower():
                results = auto_fix_stopped_containers(report)
                auto_actions.extend(results)
            elif "unhealthy" in issue.lower():
                results = auto_fix_unhealthy_containers(report)
                auto_actions.extend(results)
            elif "disk" in issue.lower():
                results = auto_cleanup_disk_action(report)
                auto_actions.extend(results)

        elif tier == "NOTIFY":
            notifications.append({
                "title": f"Issue: {issue[:60]}",
                "message": issue,
                "severity": "WARNING",
                "alert_id": alert_id,
            })
            ops.log_incident(
                "monitor",
                issue,
                "WARNING",
                "notified",
                True,
            )
        elif tier == "CONFIRM":
            notifications.append({
                "title": f"Needs confirmation: {issue[:60]}",
                "message": f"[CONFIRM REQUIRED] {issue}",
                "severity": "CRITICAL",
                "alert_id": alert_id,
            })
            ops.log_incident("monitor", f"[CONFIRM] {issue}", "CRITICAL", "pending_confirmation", False)

    # 2b. HTTP health checks on running services
    ops._log("monitor: running HTTP health checks")
    http_results = check_service_http_health()
    for name, ok, detail in http_results:
        if not ok:
            alert_id = _compute_alert_id(f"http_{name}_{detail}")
            if not ops.is_alert_suppressed(alert_id):
                ops._log(f"monitor: [NOTIFY] HTTP check failed for {name}: {detail}")
                notifications.append({
                    "title": f"Service unhealthy: {name}",
                    "message": f"{name} is running but HTTP check failed: {detail}",
                    "severity": "WARNING",
                    "alert_id": alert_id,
                })
                ops.log_incident(name, f"HTTP health check failed: {detail}", "WARNING", "notified", True)

    # 2c. Config drift detection
    ops._log("monitor: checking for config drift")
    drift_issues = check_config_drift()
    for issue in drift_issues:
        name = issue["container"]
        alert_id = _compute_alert_id(f"config_drift_{name}")
        if ops.is_alert_suppressed(alert_id):
            continue
        ops._log(f"monitor: [AUTO] {issue['detail']}")

        fix_results = auto_fix_config_drift([issue])
        for fix in fix_results:
            if not fix["ok"]:
                notifications.append({
                    "title": f"Config drift fix FAILED: {name}",
                    "message": f"{issue['detail']}. Auto-fix failed: {fix.get('detail', 'unknown')}",
                    "severity": "CRITICAL",
                    "alert_id": alert_id,
                })
                auto_actions.append(fix)
            else:
                auto_actions.append(fix)

    # 2d. Resolve incidents for healthy containers
    for cname in report.get("containers", {}).get("running", []):
        if cname not in report.get("containers", {}).get("unhealthy", []):
            ops.resolve_incidents(cname)

    # 3. Deduplicate and send notifications
    ALERT_CACHE_FILE = Path("/tmp/hermes_alert_cache.json")
    try:
        if ALERT_CACHE_FILE.exists():
            alert_cache = json.loads(ALERT_CACHE_FILE.read_text())
        else:
            alert_cache = {}
    except Exception:
        alert_cache = {}

    now_ts = time.time()
    alert_cache = {k: v for k, v in alert_cache.items() if now_ts - v < 3600}

    for notif in notifications:
        msg_hash = hashlib.md5(notif["message"].encode()).hexdigest()

        if msg_hash in alert_cache and notif["severity"] != "CRITICAL":
            ops._log(f"monitor: suppressing duplicate notification: {notif['title']}")
            continue

        ops.send_notification(
            title=notif["title"],
            message=notif["message"],
            severity=notif["severity"],
            alert_id=notif.get("alert_id"),
            topic="infrastructure",
        )
        alert_cache[msg_hash] = now_ts

    try:
        ALERT_CACHE_FILE.write_text(json.dumps(alert_cache))
    except Exception:
        pass

    # 4. Log summary
    elapsed = round(time.monotonic() - start, 1)
    summary = (
        f"monitor: completed in {elapsed}s -- "
        f"{len(issues)} issues, {len(auto_actions)} auto-fixes, "
        f"{len(notifications)} notifications"
    )
    ops._log(summary)

    for action in auto_actions:
        ok = action.get("result", {}).get("ok", False)
        ops._log(
            f"monitor: auto-fix {action['container']} "
            f"{action['action']} -> {'OK' if ok else 'FAILED'}"
        )

    # 5. If auto-fixes failed, escalate
    failed_fixes = [a for a in auto_actions if not a.get("result", {}).get("ok")]
    if failed_fixes:
        names = [a["container"] for a in failed_fixes]
        ops.send_notification(
            title="Auto-fix FAILED for containers",
            message=f"Failed to fix: {names}. Manual intervention needed.",
            severity="CRITICAL",
            alert_id=_compute_alert_id("auto_fix_failed"),
            topic="infrastructure",
        )
        for name in names:
            ops.log_incident(name, "Auto-fix failed, manual intervention needed", "CRITICAL", "auto_fix_failed", False)

    ops._log("monitor: pass complete")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        run_monitor()
    except Exception as exc:
        try:
            ops._log(f"monitor: FATAL unhandled exception: {exc}", "error")
            ops.send_notification(
                title="Monitor script crashed",
                message=str(exc),
                severity="CRITICAL",
            )
        except Exception:
            pass
        sys.exit(1)
    sys.exit(0)
