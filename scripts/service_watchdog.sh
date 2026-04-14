#!/usr/bin/env bash
# =============================================================================
# service_watchdog.sh — Extended watchdog for ALL critical homelab services
#
# Checks: hermes-gateway, chaguli, mcp-gateway, LLM proxy, scheduler
# Restarts anything that's down. Runs via cron every 5 minutes.
#
# Cron: */5 * * * * /home/rohit/agentharness/scripts/service_watchdog.sh >> /home/rohit/agentharness/data/logs/watchdog.log 2>&1
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ALERT_SCRIPT="${BASE_DIR}/scripts/alert.sh"
LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')] watchdog"

log() { echo "${LOG_PREFIX}: $*"; }

send_alert() {
    if [ -x "${ALERT_SCRIPT}" ]; then
        "${ALERT_SCRIPT}" "$1" "$2" "service_watchdog" 2>/dev/null || true
    fi
}

# Track if any restarts happened
restarts=0

# --- Check hermes-gateway (user systemd service) ---
if ! systemctl --user is-active hermes-gateway &>/dev/null; then
    log "hermes-gateway is DOWN — restarting..."
    systemctl --user restart hermes-gateway 2>/dev/null && log "hermes-gateway restarted" || log "hermes-gateway restart FAILED"
    ((restarts++)) || true
fi

# --- Check Docker containers ---
critical_containers="chaguli mcp-gateway"
for container in ${critical_containers}; do
    status=$(docker inspect "${container}" --format '{{.State.Status}}' 2>/dev/null || echo "missing")
    if [ "${status}" != "running" ]; then
        log "${container} is ${status} — restarting..."
        docker restart "${container}" 2>/dev/null && log "${container} restarted" || log "${container} restart FAILED"
        ((restarts++)) || true
    fi
done

# --- Check unhealthy Docker containers (autoheal backup) ---
unhealthy=$(docker ps --filter 'health=unhealthy' --format '{{.Names}}' 2>/dev/null || true)
if [ -n "${unhealthy}" ]; then
    log "Unhealthy containers detected: ${unhealthy}"
    for container in ${unhealthy}; do
        log "Restarting unhealthy container: ${container}..."
        docker restart "${container}" 2>/dev/null && log "${container} restarted" || log "${container} restart FAILED"
        ((restarts++)) || true
    done
fi

# --- Check LLM proxy (port 8080) ---
if ! curl -sf --max-time 5 http://localhost:8080/health &>/dev/null; then
    log "LLM proxy not responding on :8080 — checking systemd..."
    # Don't restart here — the scheduler's restart_cmd handles this with sudo
    # Just log and alert
    log "LLM proxy health check failed (scheduler will handle restart)"
fi

# --- Check MCP gateway (port 8090) ---
if ! curl -sf --max-time 5 http://localhost:8090/health &>/dev/null; then
    status=$(docker inspect mcp-gateway --format '{{.State.Status}}' 2>/dev/null || echo "missing")
    if [ "${status}" = "running" ]; then
        log "mcp-gateway is running but not responding — restarting..."
        docker restart mcp-gateway 2>/dev/null && log "mcp-gateway restarted" || log "mcp-gateway restart FAILED"
        ((restarts++)) || true
    fi
fi

# --- Summary ---
if [ "${restarts}" -gt 0 ]; then
    log "Completed with ${restarts} restart(s)"
    send_alert "WARNING" "Service watchdog performed ${restarts} restart(s). Check watchdog.log for details."
else
    log "All services healthy"
fi
