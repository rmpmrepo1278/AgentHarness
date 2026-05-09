#!/usr/bin/env bash
# =============================================================================
# health_check.sh — Consolidated homelab health check
#
# Replaces: service_watchdog.sh, homelab_monitor.sh (container part)
# Runs every 5 minutes via cron.
#
# Uses flock to prevent concurrent runs. Restart cooldowns prevent
# spamming restarts when a service is genuinely broken.
# =============================================================================

set -euo pipefail

LOG_PREFIX="[$(date "+%Y-%m-%d %H:%M:%S")] health_check"
log() { echo "${LOG_PREFIX}: $*"; }
restarts=0

# -- Concurrency guard: skip if previous run still active --
exec 200>/tmp/health_check.lock
if ! flock -n 200; then
    log "Previous health_check still running — skipping"
    exit 0
fi

# -- Restart cooldown: don't restart same service within 10 minutes --
COOLDOWN_FILE="/tmp/health_check_cooldowns.json"
now=$(date +%s)
declare -A COOLDOWNS
if [ -f "$COOLDOWN_FILE" ]; then
    while IFS='=' read -r svc ts; do
        COOLDOWNS["$svc"]="$ts"
    done < <(python3 -c "
import json, sys
try:
    d = json.load(open('$COOLDOWN_FILE'))
    for k,v in d.items(): print(f'{k}={v}')
except: pass
" 2>/dev/null)
fi

_in_cooldown() {
    local svc="$1"
    local cd="${COOLDOWNS[$svc]+${COOLDOWNS[$svc]}}"
    [ -n "$cd" ] && [ "$((now - cd))" -lt 600 ] 2>/dev/null
}

_set_cooldown() { COOLDOWNS["$1"]="$now"; }

# Persist cooldowns
save_cooldowns() {
    python3 -c "
import json
d = {}
$(for k in "${!COOLDOWNS[@]}"; do echo "d['$k'] = ${COOLDOWNS[$k]};"; done)
json.dump(d, open('$COOLDOWN_FILE', 'w'))
" 2>/dev/null || true
}

# --- 1. systemd user services ---
if ! systemctl --user is-active hermes-gateway &>/dev/null; then
    if _in_cooldown "hermes-gateway"; then
        log "hermes-gateway DOWN but in cooldown — skipping restart"
    else
        log "hermes-gateway is DOWN — restarting..."
        if systemctl --user restart hermes-gateway 2>/dev/null; then
            log "hermes-gateway restarted"
            _set_cooldown "hermes-gateway"
        else
            log "hermes-gateway restart FAILED"
        fi
        restarts=$((restarts + 1))
    fi
else
    # Clear cooldown on healthy service
    unset 'COOLDOWNS["hermes-gateway"]' 2>/dev/null || true
fi

# --- 2. HTTP health: LLM proxy ---
if ! curl -sf --max-time 5 http://localhost:8080/health &>/dev/null; then
    if _in_cooldown "llm-proxy"; then
        log "LLM proxy DOWN but in cooldown — skipping restart"
    else
        log "LLM proxy :8080 unresponsive — restarting..."
        pkill -f "proxy_server" 2>/dev/null; sleep 2
        mkdir -p /home/rohit/agentharness/logs
        nohup /home/rohit/agentharness/venv/bin/python3 -m core.providers.proxy_server \
            --host 0.0.0.0 --port 8080 --data-dir /home/rohit/agentharness/data \
            >> /home/rohit/agentharness/logs/proxy_stdout.log 2>&1 &
        log "proxy_server restarted (PID $!)"
        _set_cooldown "llm-proxy"
        restarts=$((restarts + 1))
    fi
else
    unset 'COOLDOWNS["llm-proxy"]' 2>/dev/null || true
fi

# --- 3. HTTP health: Local LLM ---
if ! curl -sf --max-time 10 http://localhost:8081/health &>/dev/null; then
    if _in_cooldown "local-llm"; then
        log "Local LLM DOWN but in cooldown — skipping restart"
    else
        log "Local LLM :8081 unresponsive — restarting..."
        if sudo systemctl restart llama-primary 2>/dev/null; then
            log "llama-primary restarted"
            _set_cooldown "local-llm"
        else
            log "llama-primary restart FAILED"
        fi
        restarts=$((restarts + 1))
    fi
else
    unset 'COOLDOWNS["local-llm"]' 2>/dev/null || true
fi

# --- 4. Docker: check for exited/unhealthy containers ---
FAILED_CONTAINERS=$(docker ps --filter "status=exited" --format "{{.Names}}" 2>/dev/null || true)
UNHEALTHY_CONTAINERS=$(docker ps --filter "health=unhealthy" --format "{{.Names}}" 2>/dev/null || true)

if [ -n "$FAILED_CONTAINERS" ]; then
    log "Exited containers: $FAILED_CONTAINERS"
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
save_cooldowns
if [ "$restarts" -gt 0 ]; then
    log "Completed with ${restarts} restart(s)"
else
    log "All services healthy"
fi
