---
name: chaguli-dashboard
description: On-demand homelab status dashboard — a text-based overview of everything running, resource usage, and recent activity
requires:
  binaries: ["docker", "curl"]
---

# Homelab Dashboard

When the user asks "dashboard", "status", "how's the homelab", "overview", or "what's running" — generate a comprehensive text-based dashboard.

## Generate the Dashboard

Run these commands and combine the output into a formatted summary.

### System Overview

```bash
echo "=== SYSTEM ==="
echo "Hostname: $(hostname)"
echo "Uptime:   $(uptime -p 2>/dev/null || uptime)"
echo "Kernel:   $(uname -r)"
echo ""
echo "=== RESOURCES ==="
echo "CPU:  $(nproc) cores, load: $(cat /proc/loadavg | awk '{print $1, $2, $3}')"
echo "RAM:  $(free -h | awk '/Mem/ {printf "%s used / %s total (%s free)", $3, $2, $4}')"
echo "Swap: $(free -h | awk '/Swap/ {printf "%s used / %s total", $3, $2}')"
echo "Disk: $(df -h / | awk 'NR==2 {printf "%s used / %s total (%s free, %s)", $3, $2, $4, $5}')"
echo ""
# CPU temperature if available
if command -v sensors &>/dev/null; then
  echo "Temp: $(sensors 2>/dev/null | grep -oP '\+[0-9.]+°C' | head -1 || echo 'N/A')"
fi
```

### Docker Services

```bash
echo "=== DOCKER SERVICES ==="
echo ""
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null
echo ""
# Count by status
echo "Running:   $(docker ps -q 2>/dev/null | wc -l)"
echo "Stopped:   $(docker ps -aq --filter 'status=exited' 2>/dev/null | wc -l)"
echo "Unhealthy: $(docker ps --filter 'health=unhealthy' --format '{{.Names}}' 2>/dev/null | wc -l)"
# Any unhealthy containers
UNHEALTHY=$(docker ps --filter 'health=unhealthy' --format '{{.Names}}' 2>/dev/null)
[ -n "$UNHEALTHY" ] && echo "⚠ Unhealthy: $UNHEALTHY"
```

### LLM Status

```bash
echo "=== LLM ==="
if curl -sf --max-time 3 http://localhost:8080/health >/dev/null 2>&1; then
  echo "Primary (8080): UP"
  # Try to get model info
  curl -sf http://localhost:8080/slots 2>/dev/null | python3 -c "
import sys, json
try:
    slots = json.load(sys.stdin)
    for s in slots:
        model = s.get('model', 'unknown')
        print(f'  Model: {model}')
        break
except: pass
" 2>/dev/null
else
  echo "Primary (8080): DOWN"
fi
if curl -sf --max-time 3 http://localhost:8081/health >/dev/null 2>&1; then
  echo "Fast (8081): UP"
else
  echo "Fast (8081): DOWN"
fi
# Best config
if [ -f /opt/agentharness/best_config.env ]; then
  source /opt/agentharness/best_config.env
  echo "Best: ${BEST_MODEL:-?} on ${BEST_ENGINE:-?} (score: ${BEST_COMPOSITE:-?}/10)"
fi
```

### Network

```bash
echo "=== NETWORK ==="
if ping -c 1 -W 2 8.8.8.8 &>/dev/null; then
  echo "Internet: UP"
else
  echo "Internet: DOWN (offline window)"
fi
# Pi-hole stats if available
PIHOLE_API=$(curl -sf "http://localhost/admin/api.php?summary" 2>/dev/null)
if [ -n "$PIHOLE_API" ]; then
  echo "$PIHOLE_API" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f'Pi-hole: {d.get(\"dns_queries_today\", \"?\")} queries, {d.get(\"ads_blocked_today\", \"?\")} blocked ({d.get(\"ads_percentage_today\", \"?\")}%)')
except: pass
" 2>/dev/null
fi
```

### Storage

```bash
echo "=== STORAGE ==="
df -h / | awk 'NR==2 {printf "Root:    %s used / %s (%s free)\n", $3, $2, $4}'
# USB backup drive
if [ -f /opt/agentharness/storage_paths.env ]; then
  source /opt/agentharness/storage_paths.env
  if [ -n "${BACKUP_DRIVE:-}" ] && [ -d "${BACKUP_DRIVE}" ]; then
    df -h "${BACKUP_DRIVE}" | awk 'NR==2 {printf "Backup:  %s used / %s (%s free)\n", $3, $2, $4}'
  fi
fi
# Docker disk usage
echo "Docker:  $(docker system df 2>/dev/null | awk 'NR>1 {printf "%s: %s  ", $1, $4}')"
```

### Recent Activity

```bash
echo "=== RECENT ACTIVITY ==="
# Last few reports
ls -lt /opt/agentharness/reports/*.md 2>/dev/null | head -5 | awk '{print "  " $NF " (" $6, $7, $8 ")"}'
echo ""
# Scheduler state
if [ -f /opt/agentharness/scheduler_state.json ]; then
  python3 -c "
import json
s = json.load(open('/opt/agentharness/scheduler_state.json'))
print(f'Last scheduler: {s.get(\"last_run\", \"?\")[:16]} ({s.get(\"window\", \"?\")} window)')
" 2>/dev/null
fi
# Pending alerts
if [ -f /opt/agentharness/alert_queue.json ]; then
  PENDING=$(python3 -c "
import json
q = json.load(open('/opt/agentharness/alert_queue.json'))
pending = [a for a in q if not a.get('sent')]
print(len(pending))
" 2>/dev/null || echo "0")
  [ "$PENDING" -gt 0 ] && echo "⚠ ${PENDING} queued alert(s)"
fi
```

## How to Format the Response

Present as a clean, scannable summary via Telegram. Use emoji sparingly for status:
- 🟢 Healthy / UP
- 🔴 Down / Critical
- 🟡 Warning / Degraded
- 📊 For stats

Keep it compact — this should fit in one Telegram message. If the user wants details on a specific section, they can ask for more.
