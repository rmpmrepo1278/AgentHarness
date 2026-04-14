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
    log "LLM proxy not responding on :8080 — restarting..."
    cd /home/rohit/agentharness && source data/.env
    nohup /home/rohit/agentharness/venv/bin/python3 -m core.providers.proxy_server \
        --host 0.0.0.0 --port 8080 --data-dir /home/rohit/agentharness/data \
        > /home/rohit/agentharness/logs/proxy.log 2>&1 &
    sleep 3
    if curl -sf --max-time 5 http://localhost:8080/health &>/dev/null; then
        log "LLM proxy restarted successfully"
        ((restarts++)) || true
    else
        log "LLM proxy restart FAILED"
        send_alert "CRITICAL" "LLM proxy failed to restart on port 8080"
    fi
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

# --- Check local LLM server (port 8081) ---
if ! curl -sf --max-time 10 http://localhost:8081/health &>/dev/null; then
    log "Local LLM (llama-primary) not responding on :8081 — restarting..."
    sudo -n systemctl restart llama-primary 2>/dev/null && {
        sleep 5
        if curl -sf --max-time 10 http://localhost:8081/health &>/dev/null; then
            log "llama-primary restarted successfully"
            ((restarts++)) || true
        else
            log "llama-primary restart did not recover health"
            send_alert "CRITICAL" "Local LLM (llama-primary) failed to restart"
        fi
    } || log "llama-primary restart FAILED (sudo issue?)"
fi

# --- Check shared memory DB integrity ---
SHARED_DB="/home/rohit/shared_agent_memory/shared_facts.db"
if [ -f "${SHARED_DB}" ]; then
    if ! sqlite3 "${SHARED_DB}" "SELECT count(*) FROM shared_facts;" &>/dev/null; then
        log "Shared memory DB corrupted — attempting recovery..."
        sqlite3 "${SHARED_DB}" "PRAGMA integrity_check;" 2>/dev/null || {
            log "Shared memory DB integrity check FAILED"
            send_alert "WARNING" "Shared agent memory DB may be corrupted"
        }
    fi
fi

# --- Summary ---
if [ "${restarts}" -gt 0 ]; then
    log "Completed with ${restarts} restart(s)"
    send_alert "WARNING" "Service watchdog performed ${restarts} restart(s). Check watchdog.log for details."
else
    log "All services healthy"
fi
