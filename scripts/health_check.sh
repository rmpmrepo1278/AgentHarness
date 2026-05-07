#!/usr/bin/env bash
# =============================================================================
# health_check.sh — Consolidated homelab health check
#
# Replaces: service_watchdog.sh, homelab_monitor.sh (container part)
# Runs every 5 minutes via cron.
#
# The deadman_check.sh (scheduler heartbeat) runs separately every 10 min.
# The autonomous-healer (Hermes cron) is disabled — this script covers it.
# =============================================================================

set -euo pipefail

LOG_PREFIX="[$(date "+%Y-%m-%d %H:%M:%S")] health_check"
log() { echo "${LOG_PREFIX}: $*"; }
restarts=0

# --- 1. systemd user services ---
if ! systemctl --user is-active hermes-gateway &>/dev/null; then
    log "hermes-gateway is DOWN — restarting..."
    systemctl --user restart hermes-gateway 2>/dev/null \
        && log "hermes-gateway restarted" \
        || log "hermes-gateway restart FAILED"
    restarts=$((restarts + 1))
fi

# --- 2. HTTP health: LLM proxy ---
if ! curl -sf --max-time 5 http://localhost:8080/health &>/dev/null; then
    log "LLM proxy :8080 unresponsive — restarting..."
    pkill -f "proxy_server" 2>/dev/null; sleep 2
    nohup /home/rohit/agentharness/venv/bin/python3 -m core.providers.proxy_server \
        --host 0.0.0.0 --port 8080 --data-dir /home/rohit/agentharness/data \
        >> /home/rohit/agentharness/data/logs/proxy.log 2>&1 &
    log "proxy_server restarted (PID $!)"
    restarts=$((restarts + 1))
fi

# --- 3. HTTP health: Local LLM ---
if ! curl -sf --max-time 10 http://localhost:8081/health &>/dev/null; then
    log "Local LLM :8081 unresponsive — restarting..."
    sudo systemctl restart llama-primary 2>/dev/null \
        && log "llama-primary restarted" \
        || log "llama-primary restart FAILED"
    restarts=$((restarts + 1))
fi

# --- 4. Docker: check for exited/unhealthy containers ---
FAILED_CONTAINERS=$(docker ps --filter "status=exited" --format "{{.Names}}" 2>/dev/null || true)
UNHEALTHY_CONTAINERS=$(docker ps --filter "health=unhealthy" --format "{{.Names}}" 2>/dev/null || true)

if [ -n "$FAILED_CONTAINERS" ]; then
    log "Exited containers: $FAILED_CONTAINER"
    for c in $FAILED_CONTAINERS; do
        log "Restoring exited container: $c"
        docker start "$c" 2>/dev/null && restarts=$((restarts + 1)) || log "FAILED to start $c"
    done
fi

if [ -n "$UNHEALTHY_CONTAINERS" ]; then
    log "Unhealthy containers: $UNHEALTHY_CONTAINERS"
    for c in $UNHEALTHY_CONTAINERS; do
        log "Restarting unhealthy container: $c"
        docker restart "$c" 2>/dev/null && restarts=$((restarts + 1)) || log "FAILED to restart $c"
    done
fi

# --- 5. Summary ---
if [ "$restarts" -gt 0 ]; then
    log "Completed with ${restarts} restart(s)"
else
    log "All services healthy"
fi
