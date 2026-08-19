#!/usr/bin/env python3
"""
health_dashboard.py — Unified health dashboard for the homelab.

Consolidates all checks into a single JSON status document.
Runs every 5 min via cron. Outputs to data/health_dashboard.json.

Usage:
    python3 health_dashboard.py           # Write JSON to file + stdout
    python3 health_dashboard.py --text    # Human-readable output
    python3 health_dashboard.py --quick   # Fast checks only (~5s)
"""

from __future__ import annotations

import json
import os
import socket
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
DATA_DIR = AG_HOME / "data"
LOG_DIR = AG_HOME / "logs"
OUTPUT_FILE = DATA_DIR / "health_dashboard.json"

CHECK_TIMEOUT = 10  # seconds per subprocess check

# Ensure DBUS session bus is available for systemctl --user even when run from cron
_UID = os.getuid()
_DBUS_ENV = {
    "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{_UID}/bus",
    "XDG_RUNTIME_DIR": f"/run/user/{_UID}",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: int = CHECK_TIMEOUT, env: dict | None = None) -> subprocess.CompletedProcess:
    # Merge extra env vars (used for DBUS session bus access from cron)
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=run_env)
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr="")


def _check(name: str, status: str, details: dict | None = None, message: str = "") -> dict:
    return {
        "name": name,
        "status": status,
        "message": message,
        "details": details or {},
    }


def _overall_status(checks: list[dict]) -> tuple[str, int]:
    """Compute overall status and score from individual check results."""
    status_weights = {"healthy": 0, "warning": 1, "critical": 3}
    total_weight = 0
    max_weight = 0
    has_critical = False
    for c in checks:
        w = status_weights.get(c["status"], 1)
        total_weight += w
        max_weight += 3
        if c["status"] == "critical":
            has_critical = True
    if max_weight == 0:
        return "healthy", 100
    score = max(0, 100 - int((total_weight / max_weight) * 100))
    if has_critical or score < 50:
        return "critical", score
    if total_weight > 0 or score < 80:
        return "degraded", score
    return "healthy", score


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

def check_docker() -> dict:
    result = _run(["docker", "ps", "-a", "--format", "{{json .}}"])
    if result.returncode != 0:
        return _check("docker", "critical", message="Docker daemon unreachable")
    containers = []
    for line in result.stdout.strip().split("\n"):
        if line:
            try:
                containers.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    total = len(containers)
    running = sum(1 for c in containers if c.get("Status", "").startswith("Up"))
    unhealthy = sum(1 for c in containers if "unhealthy" in c.get("Status", "").lower())
    restarting = sum(1 for c in containers if "Restarting" in c.get("Status", ""))
    exited = sum(1 for c in containers if c.get("Status", "").startswith("Exited"))
    if unhealthy > 0 or restarting > 0:
        return _check("docker", "critical", {"total": total, "running": running, "unhealthy": unhealthy, "restarting": restarting, "exited": exited})
    if exited > 0:
        return _check("docker", "warning", {"total": total, "running": running, "unhealthy": 0, "restarting": 0, "exited": exited})
    return _check("docker", "healthy", {"total": total, "running": running})


def check_systemd() -> dict:
    # Skip systemd checks when running inside a container (no DBUS access)
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return _check("systemd", "healthy", {"note": "skipped in container", "services_checked": 0})
    services = ["hermes-gateway.service", "hermes-mind-loop.service"]
    failed = []
    for svc in services:
        r = _run(["systemctl", "--user", "is-active", svc], env=_DBUS_ENV)
        if r.returncode != 0:
            failed.append(svc)
    if failed:
        return _check("systemd", "critical", {"failed": failed})
    return _check("systemd", "healthy", {"services_checked": len(services)})


def check_disk() -> dict:
    r = _run(["df", "-h", "/", "/mnt/usb"])
    if r.returncode != 0:
        return _check("disk", "warning", message="Cannot check disk usage")
    usage = {}
    for line in r.stdout.strip().split("\n")[1:]:
        parts = line.split()
        if len(parts) >= 6:
            mount = parts[-1]
            try:
                pct = int(parts[4].replace("%", ""))
                usage[mount] = pct
            except ValueError:
                continue
    max_pct = max(usage.values()) if usage else 0
    status = "healthy"
    if max_pct > 90:
        status = "critical"
    elif max_pct > 80:
        status = "warning"
    return _check("disk", status, {"usage_pct": usage, "max_pct": max_pct})


def check_inodes() -> dict:
    r = _run(["df", "-i", "/", "/mnt/usb"])
    if r.returncode != 0:
        return _check("inodes", "warning", message="Cannot check inode usage")
    usage = {}
    for line in r.stdout.strip().split("\n")[1:]:
        parts = line.split()
        if len(parts) >= 5:
            mount = parts[-1]
            try:
                pct = int(parts[4].replace("%", ""))
                usage[mount] = {"inode_pct": pct}
            except ValueError:
                continue
    max_pct = max((v["inode_pct"] for v in usage.values()), default=0)
    status = "healthy"
    if max_pct > 95:
        status = "critical"
    elif max_pct > 85:
        status = "warning"
    return _check("inodes", status, {"usage": usage, "max_pct": max_pct})


def check_memory() -> dict:
    # Use /proc/meminfo for container-compatible memory check
    try:
        meminfo = Path("/proc/meminfo").read_text()
        mem = {}
        for line in meminfo.splitlines():
            if line.startswith("MemTotal:"):
                mem["total_mb"] = int(line.split()[1]) // 1024
            elif line.startswith("MemAvailable:"):
                mem["available_mb"] = int(line.split()[1]) // 1024
        if not mem:
            return _check("memory", "warning", message="Cannot parse /proc/meminfo")
        total_gb = mem.get("total_mb", 999) // 1024 or 1
        available_gb = mem.get("available_mb", 999) // 1024 or 1
        status = "healthy"
        if available_gb < 1:
            status = "critical"
        elif available_gb < 2:
            status = "warning"
        return _check("memory", status, {"total_gb": total_gb, "available_gb": available_gb})
    except Exception as e:
        return _check("memory", "warning", message=f"Cannot check memory: {e}")


def check_cpu() -> dict:
    with open("/proc/loadavg") as f:
        parts = f.read().split()
    cores = os.cpu_count() or 4
    load_1 = float(parts[0])
    status = "healthy"
    if load_1 > cores * 5:
        status = "critical"
    elif load_1 > cores * 3:
        status = "warning"
    return _check("cpu", status, {"load_1min": load_1, "load_5min": float(parts[1]), "cores": cores})


def check_network() -> dict:
    # Check key ports - only check services that should be running
    key_ports = {
        8080: "llm-proxy", 8082: "hermes-webui",
        3001: "grafana", 8000: "paperless",
    }
    down = []
    for port, name in key_ports.items():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                down.append(f"{name}:{port}")
            s.close()
        except Exception:
            down.append(f"{name}:{port}")
    # Note: portainer (9000) removed - not running
    if down:
        return _check("network", "warning", {"down_ports": down})
    return _check("network", "healthy", {"ports_checked": len(key_ports)})


def check_providers() -> dict:
    r = _run(["curl", "-s", "--max-time", "5", "http://localhost:8080/health"])
    if r.returncode != 0:
        return _check("providers", "critical", message="LLM proxy unreachable")
    try:
        data = json.loads(r.stdout)
        proxy_status = data.get("status", "unknown")
        if proxy_status != "ok":
            return _check("providers", "warning", {"proxy_status": proxy_status})
        return _check("providers", "healthy", {"proxy_status": proxy_status})
    except json.JSONDecodeError:
        return _check("providers", "warning", message="Proxy returned non-JSON")


def check_dns() -> dict:
    tests = [
        ("google.com", 5),
        ("chagulihome.duckdns.org", 5),
    ]
    failures = []
    for domain, port in tests:
        try:
            socket.setdefaulttimeout(3)
            socket.getaddrinfo(domain, port)
        except (socket.gaierror, socket.timeout):
            failures.append(domain)
    if failures:
        return _check("dns", "critical", {"failures": failures})
    return _check("dns", "healthy")


def check_zombies() -> dict:
    r = _run(["ps", "-eo", "stat,pid"])
    zombies = [l for l in r.stdout.strip().split("\n") if l.startswith("Z")]
    count = len(zombies)
    status = "healthy"
    if count > 50:
        status = "critical"
    elif count > 10:
        status = "warning"
    return _check("zombies", status, {"count": count})


def check_oom() -> dict:
    r = _run(["sudo", "dmesg"], timeout=5)
    if r.returncode != 0:
        r = _run(["sudo", "journalctl", "-k", "--since", "24 hours ago", "--no-pager", "-q"], timeout=10)
    oom_lines = [l for l in r.stdout.strip().split("\n") if "oom" in l.lower() or "killed process" in l.lower()]
    count = len(oom_lines)
    if count > 0:
        return _check("oom", "critical", {"recent_kills": count})
    return _check("oom", "healthy", {"recent_kills": 0})


def check_mcp() -> dict:
    ports = [8090, 8091, 8095, 8097, 8100, 8102, 8103, 8104, 8105, 8106, 8108]
    down = []
    for port in ports:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                down.append(port)
            s.close()
        except Exception:
            down.append(port)
    if down:
        severity = "critical" if 8090 in down else "high"
        return _check("mcp_services", severity, {"down_ports": down})
    return _check("mcp_services", "healthy")


def check_duckdns() -> dict:
    try:
        import urllib.request
        req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": "homelab-dashboard/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            current_ip = resp.read().decode().strip()
    except Exception:
        return _check("duckdns", "warning", message="Cannot determine external IP")
    # Resolve via external DNS to avoid Pi-hole local wildcard returning private IP
    try:
        dns_r = _run(["dig", "+short", "chagulihome.duckdns.org", "@8.8.8.8"], timeout=5)
        dns_ip = dns_r.stdout.strip().split("\n")[0] if dns_r.returncode == 0 and dns_r.stdout.strip() else None
    except Exception:
        dns_ip = None
    if dns_ip and current_ip != dns_ip:
        return _check("duckdns", "warning", {"current_ip": current_ip, "dns_ip": dns_ip, "synced": False})
    return _check("duckdns", "healthy", {"ip": current_ip, "synced": True})


def check_backups() -> dict:
    """Check backups — uses Kopia snapshots (replaced tar-based backups)."""
    try:
        result = subprocess.run(
            ["sudo", "/usr/local/bin/kopia", "--config-file",
             "/root/.config/kopia/repository.config", "snapshot", "list", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return _check("backups", "warning", message="Kopia snapshot list failed")

        snapshots = json.loads(result.stdout)
        if not snapshots:
            return _check("backups", "warning", message="No Kopia snapshots found")

        # Find the latest snapshot across all sources
        latest = max(snapshots, key=lambda s: s.get("startTime", ""))
        start_time = latest.get("startTime", "")
        if start_time:
            # Parse ISO timestamp
            from datetime import timezone
            try:
                dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            except Exception:
                age_hours = 0
        else:
            age_hours = 0

        status = "healthy"
        if age_hours > 168:  # 7 days
            status = "critical"
        elif age_hours > 72:  # 3 days
            status = "warning"

        return _check("backups", status, {
            "latest": start_time[:19] if start_time else "unknown",
            "age_hours": round(age_hours, 1),
            "snapshots": len(snapshots),
            "type": "kopia",
        })
    except FileNotFoundError:
        return _check("backups", "warning", message="kopia binary not found")
    except Exception as e:
        return _check("backups", "warning", message=f"Kopia check error: {e}")


def check_ssl_certs() -> dict:
    cert_dirs = [
        Path("/home/rohit/services/data/nginx-proxy-manager/letsencrypt"),
        Path("/etc/letsencrypt/live"),
    ]
    expiring = []
    for cert_dir in cert_dirs:
        if not cert_dir.exists():
            continue
        for cert_file in cert_dir.rglob("*.pem"):
            if cert_file.name not in ("fullchain.pem", "cert.pem"):
                continue
            r = _run(["openssl", "x509", "-in", str(cert_file), "-noout", "-enddate"])
            if r.returncode == 0:
                try:
                    from datetime import datetime as _dt
                    import re as _re
                    m = _re.search(r"notAfter=(.+)", r.stdout)
                    if m:
                        expiry = _dt.strptime(m.group(1).strip(), "%b %d %H:%M:%S %Y %Z")
                        days_left = (expiry - _dt.utcnow()).days
                        if days_left < 30:
                            expiring.append({"domain": cert_file.parent.name, "days_left": days_left})
                except Exception:
                    continue
    if expiring:
        status = "critical" if any(e["days_left"] < 7 for e in expiring) else "warning"
        return _check("ssl", status, {"expiring": expiring})
    return _check("ssl", "healthy")


def check_git() -> dict:
    repos = [
        (Path.home() / ".hermes" / "hermes-agent", "hermes-agent"),
        (Path("/home/rohit/agentharness"), "agentharness"),
    ]
    dirty = []
    # File patterns that are noise (logs, caches, generated artifacts) — not real issues
    NOISE_PATTERNS = (
        ".log", ".log.", ".gz", ".tmp", ".cache", ".pyc",
        "__pyclogs/", "logs/", "data/logs/",
    )
    for repo_path, repo_name in repos:
        if not (repo_path / ".git").exists():
            continue
        r = _run(["git", "-C", str(repo_path), "status", "--porcelain"])
        all_changes = [l for l in r.stdout.strip().split("\n") if l]
        # Filter out noise files (logs, caches, etc.)
        real_changes = [
            l for l in all_changes
            if not any(pat in l for pat in NOISE_PATTERNS)
        ]
        if real_changes:
            dirty.append({"repo": repo_name, "changes": len(real_changes)})
    if dirty:
        return _check("git", "warning", {"dirty_repos": dirty})
    return _check("git", "healthy")


def check_tmp() -> dict:
    r = _run(["df", "-h", "/"])
    if r.returncode != 0:
        return _check("tmp", "warning")
    lines = r.stdout.strip().split("\n")
    if len(lines) >= 2:
        parts = lines[1].split()
        if len(parts) >= 5:
            try:
                pct = int(parts[4].replace("%", ""))
                status = "healthy" if pct < 85 else ("critical" if pct > 95 else "warning")
                return _check("tmp", status, {"pct": pct})
            except ValueError:
                pass
    return _check("tmp", "healthy")


def check_volumes() -> dict:
    r = _run(["docker", "volume", "ls", "-f", "dangling=true"])
    if r.returncode != 0:
        return _check("volumes", "warning")
    dangling = max(0, len(r.stdout.strip().split("\n")) - 1)
    if dangling > 5:
        return _check("volumes", "warning", {"dangling": dangling})
    return _check("volumes", "healthy", {"dangling": dangling})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Homelab health dashboard")
    parser.add_argument("--text", action="store_true", help="Human-readable output")
    parser.add_argument("--quick", action="store_true", help="Fast checks only")
    parser.add_argument("--output", type=str, default=str(OUTPUT_FILE), help="Output JSON file")
    args = parser.parse_args()

    start_time = time.time()

    checks = []
    checks.append(check_docker())
    checks.append(check_systemd())
    checks.append(check_disk())
    checks.append(check_inodes())
    checks.append(check_memory())
    checks.append(check_cpu())
    checks.append(check_network())
    checks.append(check_providers())
    checks.append(check_dns())
    checks.append(check_mcp())
    checks.append(check_ssl_certs())
    checks.append(check_backups())
    checks.append(check_duckdns())

    if not args.quick:
        checks.append(check_zombies())
        checks.append(check_oom())
        checks.append(check_git())
        checks.append(check_tmp())
        checks.append(check_volumes())

    overall, score = _overall_status(checks)
    elapsed = round(time.time() - start_time, 2)

    dashboard = {
        "timestamp": datetime.now().isoformat(),
        "overall_status": overall,
        "health_score": score,
        "elapsed_seconds": elapsed,
        "checks": {c["name"]: c for c in checks},
        "issues": [c for c in checks if c["status"] != "healthy"],
        "metrics": {
            "checks_total": len(checks),
            "checks_healthy": sum(1 for c in checks if c["status"] == "healthy"),
            "checks_warning": sum(1 for c in checks if c["status"] == "warning"),
            "checks_critical": sum(1 for c in checks if c["status"] == "critical"),
        },
    }

    # Write JSON
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(dashboard, indent=2, default=str))

    # Emit containers.json for the health dashboard UI / MCP server.
    c_out = DATA_DIR / "containers.json"
    container_list = []
    cresult = _run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"])
    if cresult.returncode == 0:
        for line in cresult.stdout.strip().split("\n"):
            if "\t" not in line:
                continue
            name, status = line.split("\t", 1)
            healthy = "unhealthy" not in status.lower() and "restarting" not in status.lower()
            container_list.append({"name": name, "healthy": healthy, "status": status})
    c_out.write_text(json.dumps(container_list, indent=2))

    if args.text:
        status_icon = {"healthy": "✅", "warning": "⚠️", "critical": "❌"}
        print(f"\n{'=' * 60}")
        print(f"  Homelab Health Dashboard — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Overall: {status_icon.get(overall, '?')} {overall.upper()}  (Score: {score}/100)")
        print(f"  Elapsed: {elapsed}s")
        print(f"{'=' * 60}")
        for c in checks:
            icon = status_icon.get(c["status"], "?")
            msg = f" — {c['message']}" if c["message"] else ""
            print(f"  {icon} {c['name']}: {c['status']}{msg}")
        print(f"{'=' * 60}")
        if dashboard["issues"]:
            print(f"\n  {len(dashboard['issues'])} issue(s) detected:")
            for i in dashboard["issues"]:
                print(f"    [{i['status'].upper()}] {i['name']}: {i.get('message', 'see details')}")
        else:
            print("\n  All checks passed ✓")
        print()
    else:
        print(json.dumps(dashboard, indent=2, default=str))


if __name__ == "__main__":
    main()
