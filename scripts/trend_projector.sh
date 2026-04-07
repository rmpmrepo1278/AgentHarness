#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# trend_projector.sh — Track resource trends and predict when thresholds hit
#
# Samples disk, RAM, swap, container count, Docker image size.
# Projects forward: "At this rate, disk hits 90% in 12 days."
# Stores history in a simple CSV for trend analysis.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

[ -f /opt/agentharness/.env ] && source /opt/agentharness/.env

TREND_DATA="/opt/agentharness/trend_data.csv"
TREND_REPORT="/opt/agentharness/reports/trends_$(timestamp).md"

# =============================================================================
# Sample current metrics
# =============================================================================
sample_metrics() {
    local ts
    ts=$(date -Iseconds)

    local disk_pct
    disk_pct=$(df / | awk 'NR==2 {gsub(/%/,""); print $5}')
    local disk_used_gb
    disk_used_gb=$(df / | awk 'NR==2 {printf "%.1f", $3/1024/1024}')
    local ram_pct
    ram_pct=$(free | awk '/Mem/ {printf "%.0f", $3/$2*100}')
    local swap_mb
    swap_mb=$(free -m | awk '/Swap/ {print $3}')
    local containers
    containers=$(docker ps -q 2>/dev/null | wc -l)
    local docker_gb
    docker_gb=$(docker system df --format '{{.Size}}' 2>/dev/null | head -1 | grep -oP '[\d.]+' | head -1 || echo "0")

    # Create header if file doesn't exist
    if [ ! -f "${TREND_DATA}" ]; then
        echo "timestamp,disk_pct,disk_used_gb,ram_pct,swap_mb,containers,docker_gb" > "${TREND_DATA}"
    fi

    echo "${ts},${disk_pct},${disk_used_gb},${ram_pct},${swap_mb},${containers},${docker_gb}" >> "${TREND_DATA}"
}

# =============================================================================
# Project trends forward
# =============================================================================
project_trends() {
    log_info "Projecting trends..."

    python3 << 'PYEOF'
import csv
import json
from datetime import datetime, timedelta

data_file = "/opt/agentharness/trend_data.csv"
alerts = []

try:
    with open(data_file) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
except:
    print("No trend data yet")
    exit(0)

if len(rows) < 2:
    print("Need more data points (at least 2)")
    exit(0)

# Use last 7 days of data
cutoff = (datetime.now() - timedelta(days=7)).isoformat()
recent = [r for r in rows if r.get("timestamp", "") > cutoff]
if len(recent) < 2:
    recent = rows[-10:]  # Use last 10 samples

def project_days_to_threshold(values, threshold, current):
    """Given a list of (timestamp, value) pairs, project when threshold is hit."""
    if len(values) < 2:
        return None

    # Simple linear regression
    first_val = values[0]
    last_val = values[-1]
    first_time = datetime.fromisoformat(first_val[0].replace("Z", "+00:00").split("+")[0])
    last_time = datetime.fromisoformat(last_val[0].replace("Z", "+00:00").split("+")[0])

    days_elapsed = max((last_time - first_time).total_seconds() / 86400, 0.01)
    rate_per_day = (float(last_val[1]) - float(first_val[1])) / days_elapsed

    if rate_per_day <= 0:
        return None  # Decreasing or stable — won't hit threshold

    remaining = threshold - float(current)
    if remaining <= 0:
        return 0  # Already past threshold

    return remaining / rate_per_day

# Project disk usage
disk_values = [(r["timestamp"], r["disk_pct"]) for r in recent if r.get("disk_pct")]
if disk_values:
    current_disk = float(disk_values[-1][1])
    days_to_90 = project_days_to_threshold(disk_values, 90, current_disk)
    days_to_95 = project_days_to_threshold(disk_values, 95, current_disk)

    if days_to_90 is not None and days_to_90 < 14:
        alerts.append(f"Disk will hit 90% in ~{int(days_to_90)} days (currently {current_disk:.0f}%)")
    if days_to_95 is not None and days_to_95 < 7:
        alerts.append(f"CRITICAL: Disk will hit 95% in ~{int(days_to_95)} days")

# Project swap trend
swap_values = [(r["timestamp"], r["swap_mb"]) for r in recent if r.get("swap_mb")]
if swap_values:
    current_swap = float(swap_values[-1][1])
    if current_swap > 100:
        days_to_2000 = project_days_to_threshold(swap_values, 2000, current_swap)
        if days_to_2000 is not None and days_to_2000 < 7:
            alerts.append(f"Swap growing fast — will hit 2GB in ~{int(days_to_2000)} days (currently {current_swap:.0f}MB)")

# Container count trend
container_values = [(r["timestamp"], r["containers"]) for r in recent if r.get("containers")]
if container_values and len(container_values) >= 2:
    first_count = int(container_values[0][1])
    last_count = int(container_values[-1][1])
    if last_count > first_count + 3:
        alerts.append(f"Container count growing: {first_count} → {last_count} in the last week")

# Summary
result = {
    "sampled_at": datetime.now().isoformat(),
    "data_points": len(recent),
    "current": {
        "disk_pct": float(disk_values[-1][1]) if disk_values else 0,
        "swap_mb": float(swap_values[-1][1]) if swap_values else 0,
        "containers": int(container_values[-1][1]) if container_values else 0
    },
    "projections": alerts,
    "data_range_days": (datetime.fromisoformat(recent[-1]["timestamp"].split("+")[0]) -
                        datetime.fromisoformat(recent[0]["timestamp"].split("+")[0])).days if len(recent) > 1 else 0
}

json.dump(result, open("/opt/agentharness/latest_trends.json", "w"), indent=2)

for alert in alerts:
    print(f"PROJECTION: {alert}")

if not alerts:
    print("All trends stable — no threshold crossings projected in the next 2 weeks")
PYEOF
}

# =============================================================================
# Alert on concerning projections
# =============================================================================
send_projection_alerts() {
    if [ ! -f /opt/agentharness/latest_trends.json ]; then
        return 0
    fi

    python3 -c "
import json, subprocess

trends = json.load(open('/opt/agentharness/latest_trends.json'))
for projection in trends.get('projections', []):
    severity = 'CRITICAL' if 'CRITICAL' in projection else 'WARN'
    subprocess.run([
        'bash', '/opt/agentharness/scripts/alert.sh', severity, projection
    ], capture_output=True, timeout=10)
" 2>/dev/null
}

# =============================================================================
# Generate report
# =============================================================================
generate_report() {
    cat > "${TREND_REPORT}" << EOF
# Trend Projections
**Date**: $(date '+%Y-%m-%d %H:%M')

---

EOF

    if [ -f /opt/agentharness/latest_trends.json ]; then
        python3 -c "
import json
t = json.load(open('/opt/agentharness/latest_trends.json'))

print(f'Data points: {t[\"data_points\"]} over {t[\"data_range_days\"]} days')
print(f'Current: disk {t[\"current\"][\"disk_pct\"]:.0f}%, swap {t[\"current\"][\"swap_mb\"]:.0f}MB, {t[\"current\"][\"containers\"]} containers')
print()

if t['projections']:
    print('## Projections')
    for p in t['projections']:
        print(f'- {p}')
else:
    print('## All Clear')
    print('No threshold crossings projected in the next 2 weeks.')
" 2>/dev/null >> "${TREND_REPORT}"
    fi

    log_ok "Report: ${TREND_REPORT}"
}

# =============================================================================
main() {
    log_header "Trend Projector"
    ensure_dir /opt/agentharness/reports

    sample_metrics
    project_trends
    send_projection_alerts
    generate_report
}

main "$@"
