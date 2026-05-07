#!/usr/bin/env python3
"""
Daily Homelab Audit — runs at 11am via cron.

Checks disk, Docker, LLM proxy health, and security.
Writes report to log directory (NOT inbox — avoids triggering inbox_watcher).
"""

import os
import subprocess
import json
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR = "/home/rohit/agentharness"
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_DIR = os.path.join(DATA_DIR, "logs")
REPORT_DIR = os.path.join(DATA_DIR, "reports")


def run_cmd(cmd, timeout=60):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return res.stdout.strip()
    except Exception:
        return ""


def audit_docker():
    """Check Docker reclaimable space."""
    report = []
    docker_df = run_cmd("docker system df --format '{{.Type}}: {{.Reclaimable}}'")
    if docker_df:
        report.append(f"Docker Reclaimable:\n{docker_df}")
    else:
        report.append("Docker: unable to query (docker may not be running)")
    return "\n".join(report)


def benchmark_llm():
    """Quick LLM proxy health check."""
    start = time.time()
    cmd = (
        'curl -sf -m 30 http://localhost:8080/v1/chat/completions '
        '-H "Content-Type: application/json" '
        '-d \'{"messages":[{"role":"user","content":"Reply with OK"}],"max_tokens":5}\''
    )
    res = run_cmd(cmd, timeout=35)
    duration = time.time() - start

    if res:
        return f"LLM Proxy: OK ({duration:.2f}s)"
    else:
        return "LLM Proxy: FAILED or timed out"


def audit_security():
    """List listening ports."""
    ports = run_cmd("ss -tulpn 2>/dev/null | grep LISTEN | awk '{print $5}' | cut -d: -f2 | sort -nu | xargs")
    return f"Active Listening Ports: {ports or 'none detected'}"


def audit_disk():
    """Check disk usage."""
    res = run_cmd("df -h / /mnt/usb 2>/dev/null")
    return f"Disk Usage:\n{res}"


def run_audit():
    now = datetime.now()
    print(f"Starting Daily Audit: {now.strftime('%Y-%m-%d %H:%M:%S')}")

    sections = {
        "Disk": audit_disk(),
        "Docker": audit_docker(),
        "LLM Performance": benchmark_llm(),
        "Security": audit_security(),
    }

    date_str = now.strftime("%Y-%m-%d %H:%M:%S")
    full_report = f"# Daily Homelab Audit Report\nDate: {date_str}\n\n"
    for title, content in sections.items():
        full_report += f"## {title}\n{content}\n\n"

    # Save to reports directory (NOT inbox — avoids inbox_watcher noise)
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_file = os.path.join(REPORT_DIR, f"audit_{now.strftime('%Y%m%d_%H%M%S')}.txt")
    with open(report_file, "w") as f:
        f.write(full_report)

    # Also save latest as JSON for programmatic access
    json_file = os.path.join(REPORT_DIR, "latest_audit.json")
    payload = {
        "timestamp": now.isoformat(),
        "sections": {title: content for title, content in sections.items()},
    }
    with open(json_file, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"Audit complete. Report saved to {report_file}")
    print(full_report)


if __name__ == "__main__":
    run_audit()
