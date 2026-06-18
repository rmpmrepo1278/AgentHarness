#!/usr/bin/env bash
# =============================================================================
# consolidated_health.sh — Single health check replacing 4 separate cron jobs
#
# Replaces:
#   - health_check.sh (every 5 min)
#   - unified_cost_guard.py check (every 5 min)
#   - export_systemd_status.sh (every 5 min) — kept for backward compat
#   - deadman_check.sh (every 10 min) — REMOVED (systemd handles restarts)
#
# Runs every 5 min via cron.
# =============================================================================

set -euo pipefail

# Healthchecks ping
source /home/rohit/.hermes/scripts/hc_uuids.sh 2>/dev/null || true
HC_PID=""
hc_start() { [ -n "$HC_UUID_CONSOLIDATED_HEALTH" ] && /home/rohit/.hermes/scripts/hc_ping.sh "$HC_UUID_CONSOLIDATED_HEALTH" start & HC_PID=$!; }
hc_done() { [ -n "$HC_UUID_CONSOLIDATED_HEALTH" ] && /home/rohit/.hermes/scripts/hc_ping.sh "$HC_UUID_CONSOLIDATED_HEALTH" "$([ "$ISSUES" -eq 0 ] && echo success || echo fail)"; [ -n "$HC_PID" ] && wait "$HC_PID" 2>/dev/null || true; }
hc_start

LOG_PREFIX="[$(date "+%Y-%m-%d %H:%M:%S")] consolidated_health"
log() { echo "${LOG_PREFIX}: $*"; }

# DBUS for systemctl --user from cron
_UID=$(id -u)
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/${_UID}/bus}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${_UID}}"

# -- Concurrency guard --
exec 200>/tmp/consolidated_health.lock
if ! flock -n 200; then
    log "Previous run still active — skipping"
    exit 0
fi

ISSUES=0

# ── 1. Docker container health ──
log "=== Docker container check ==="
UNHEALTHY=$(docker ps --filter "health=unhealthy" --format "{{.Names}}" 2>/dev/null || true)
if [ -n "$UNHEALTHY" ]; then
    log "Unhealthy containers: $UNHEALTHY"
    ISSUES=$((ISSUES + 1))
fi
DOWN=$(docker ps --filter "status=exited" --filter "restart_policy=always" --format "{{.Names}}" 2>/dev/null || true)
if [ -n "$DOWN" ]; then
    log "Exited containers with restart=always: $DOWN"
    ISSUES=$((ISSUES + 1))
fi

# ── 2. Systemd user services ──
log "=== Systemd service check ==="
for svc in hermes-gateway.service hermes-watcher.service proactive-daemon.service; do
    state=$(systemctl --user is-active "$svc" 2>/dev/null || echo "unknown")
    if [ "$state" != "active" ]; then
        log "WARNING: $svc is $state"
        ISSUES=$((ISSUES + 1))
    fi
done

# ── 3. Cost guard check ──
log "=== Cost guard check ==="
CG_LOG="/home/rohit/.hermes/logs/unified_cost_guard.log"
if /usr/bin/python3 /home/rohit/.hermes/scripts/unified_cost_guard.py check >> "$CG_LOG" 2>&1; then
    log "Cost guard: OK"
else
    log "Cost guard: ISSUE DETECTED"
    ISSUES=$((ISSUES + 1))
fi

# ── 4. Export systemd status (for backward compat) ──
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
OUTPUT="/home/rohit/.hermes/systemd_status.json"
SERVICES=(
    "hermes-gateway.service"
    "hermes-watcher.service"
    "proactive-daemon.service"
    "homelab-backup.service"
    "gdrive-sync.service"
)
echo '{"services": {' > "$OUTPUT"
first=true
for svc in "${SERVICES[@]}"; do
    state=$(systemctl --user is-active "$svc" 2>/dev/null || echo "unknown")
    sub=$(systemctl --user show "$svc" --property=SubState --value 2>/dev/null || echo "unknown")
    if [ "$first" = true ]; then first=false; else echo "," >> "$OUTPUT"; fi
    echo "\"$svc\": {\"active\": \"$state\", \"sub\": \"$sub\"}" >> "$OUTPUT"
done
echo '}, "timestamp": "'"$(date -Iseconds)"'"}' >> "$OUTPUT"

# ── 5. Inode usage check ──
log "=== Inode check ==="
INODE_PCT=$(df -i / | awk 'NR==2{print $5}' | tr -d '%')
if [ "${INODE_PCT:-0}" -gt 95 ]; then
    log "CRITICAL: Inode usage at ${INODE_PCT}%"
    ISSUES=$((ISSUES + 1))
elif [ "${INODE_PCT:-0}" -gt 85 ]; then
    log "WARNING: Inode usage at ${INODE_PCT}%"
    ISSUES=$((ISSUES + 1))
fi

# ── 6. /tmp space check + auto-cleanup ──
log "=== /tmp space check ==="
TMP_PCT=$(df / | awk 'NR==2{print $5}' | tr -d '%')
if [ "${TMP_PCT:-0}" -gt 90 ]; then
    log "WARNING: /tmp at ${TMP_PCT}% — cleaning old files"
    find /tmp -mtime +3 -type f -delete 2>/dev/null || true
    find /tmp -type f -size +500M -delete 2>/dev/null || true
    ISSUES=$((ISSUES + 1))
fi

# ── 7. Zombie process check ──
log "=== Zombie process check ==="
ZOMBIE_COUNT=$(ps -eo stat,pid 2>/dev/null | grep -c '^Z' || echo 0)
if [ "$ZOMBIE_COUNT" -gt 50 ]; then
    log "CRITICAL: $ZOMBIE_COUNT zombie processes"
    # Signal zombie parents to reap children
    ps -eo stat,pid,ppid,comm 2>/dev/null | grep '^Z' | awk '{print $3}' | sort -u | \
        xargs -r kill -HUP 2>/dev/null || true
    ISSUES=$((ISSUES + 1))
elif [ "$ZOMBIE_COUNT" -gt 10 ]; then
    log "WARNING: $ZOMBIE_COUNT zombie processes"
    ps -eo stat,pid,ppid,comm 2>/dev/null | grep '^Z' | awk '{print $3}' | sort -u | \
        xargs -r kill -HUP 2>/dev/null || true
fi

# ── 8. OOM kill check ──
log "=== OOM kill check ==="
OOM_COUNT=$(dmesg 2>/dev/null | grep -ci "oom\|killed process" || echo 0)
if [ "$OOM_COUNT" -gt 0 ]; then
    log "WARNING: $OOM_COUNT OOM kill(s) detected in dmesg"
    ISSUES=$((ISSUES + 1))
fi

# ── 9. Docker volume leak check + auto-prune ──
log "=== Docker volume leak check ==="
DANGLING=$(docker volume ls -f dangling=true 2>/dev/null | wc -l)
DANGLING=$((DANGLING - 1))  # Subtract header line
if [ "$DANGLING" -gt 5 ]; then
    log "WARNING: $DANGLING dangling Docker volumes — pruning"
    docker volume prune -f 2>/dev/null || true
fi

# ── 10. Log rotation for oversized files ──
log "=== Log rotation check ==="
for log_file in /home/rohit/agentharness/logs/*.log /home/rohit/.hermes/logs/*.log; do
    [ -f "$log_file" ] || continue
    SIZE_MB=$(du -m "$log_file" 2>/dev/null | cut -f1)
    if [ "${SIZE_MB:-0}" -gt 500 ]; then
        log "Rotating large log: $log_file (${SIZE_MB}MB)"
        tail -10000 "$log_file" > "${log_file}.tmp" 2>/dev/null && \
            mv "${log_file}.tmp" "$log_file" 2>/dev/null || true
    fi
done

# ── 11. DuckDNS sync check ──
log "=== DuckDNS sync check ==="
DUCKDNS_TOKEN_FILE="/home/rohit/.duckdns_token"
if [ -f "$DUCKDNS_TOKEN_FILE" ]; then
    TOKEN=$(cat "$DUCKDNS_TOKEN_FILE" 2>/dev/null | tr -d '[:space:]')
    if [ -n "$TOKEN" ] && [ "$TOKEN" != "PASTE_YOUR_TOKEN_HERE" ]; then
        CURRENT_IP=$(curl -s --max-time 10 https://api.ipify.org 2>/dev/null || echo "")
        DNS_IP=$(dig +short chagulihome.duckdns.org @8.8.8.8 2>/dev/null | head -1)
        if [ -n "$CURRENT_IP" ] && [ -n "$DNS_IP" ] && [ "$CURRENT_IP" != "$DNS_IP" ]; then
            log "WARNING: DuckDNS out of sync — current=$CURRENT_IP dns=$DNS_IP"
            # Auto-fix: run the update script
            bash /home/rohit/.hermes/scripts/duckdns_update.sh >> /home/rohit/.hermes/logs/duckdns_auto_fix.log 2>&1 || true
            ISSUES=$((ISSUES + 1))
        else
            log "DuckDNS: in sync ($CURRENT_IP)"
        fi
    fi
fi

# ── Summary ──
if [ "$ISSUES" -gt 0 ]; then
    log "Health check complete: $ISSUES issue(s) detected"
else
    log "Health check complete: all clear"
fi

hc_done
exit $ISSUES
