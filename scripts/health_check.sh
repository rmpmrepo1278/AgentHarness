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

# DBUS for systemctl --user from cron
_UID=$(id -u)
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/${_UID}/bus}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${_UID}}"

# -- Concurrency guard: skip if previous run still active --
exec 200>/tmp/health_check.lock
if ! flock -n 200; then
    log "Previous health_check still running — skipping"
    exit 0
fi

# -- Restart cooldown: don't restart same service within 10 minutes --
COOLDOWN_FILE="/tmp/health_check_cooldowns.json"
now=$(date +%s)

_in_cooldown() {
    local svc="$1"
    python3 - "$svc" "$COOLDOWN_FILE" "$now" <<'PY'
import json, sys
svc, path, now = sys.argv[1], sys.argv[2], int(sys.argv[3])
try:
    data = json.load(open(path))
except Exception:
    data = {}
ts = data.get(svc, 0)
print("yes" if ts and (now - int(ts)) < 600 else "no")
PY
}

_set_cooldown() {
    local svc="$1"
    python3 - "$svc" "$COOLDOWN_FILE" "$now" <<'PY'
import json, sys
svc, path, now = sys.argv[1], sys.argv[2], int(sys.argv[3])
try:
    data = json.load(open(path))
except Exception:
    data = {}
data[svc] = now
json.dump(data, open(path, "w"))
PY
}

_clear_cooldown() {
    local svc="$1"
    python3 - "$svc" "$COOLDOWN_FILE" <<'PY'
import json, sys
svc, path = sys.argv[1], sys.argv[2]
try:
    data = json.load(open(path))
except Exception:
    data = {}
data.pop(svc, None)
json.dump(data, open(path, "w"))
PY
}

# --- 1. systemd user services ---
if ! systemctl --user is-active hermes-gateway &>/dev/null; then
    if [ "$(_in_cooldown hermes-gateway)" = "yes" ]; then
        log "hermes-gateway DOWN but in cooldown — skipping restart"
    else
        log "hermes-gateway is DOWN — restarting..."
        if systemctl --user restart hermes-gateway 2>/dev/null; then
            log "hermes-gateway restarted"
            _set_cooldown hermes-gateway
        else
            log "hermes-gateway restart FAILED"
        fi
        restarts=$((restarts + 1))
    fi
else
    _clear_cooldown hermes-gateway
fi

# --- 2. HTTP health: LLM proxy (systemd unit) ---
if ! curl -sf --max-time 5 http://localhost:8080/health &>/dev/null; then
    if [ "$(_in_cooldown llm-proxy)" = "yes" ]; then
        log "LLM proxy DOWN but in cooldown — skipping restart"
    else
        log "LLM proxy :8080 unresponsive — restarting via systemd..."
        if sudo systemctl restart agentharness-llm-proxy.service 2>/dev/null; then
            log "agentharness-llm-proxy restarted"
        else
            log "agentharness-llm-proxy restart FAILED"
        fi
        _set_cooldown llm-proxy
        restarts=$((restarts + 1))
    fi
else
    _clear_cooldown llm-proxy
fi

# --- 3. HTTP health: Local LLM ---
if ! curl -sf --max-time 10 http://localhost:18090/health &>/dev/null; then
    if [ "$(_in_cooldown local-llm)" = "yes" ]; then
        log "Local LLM DOWN but in cooldown — skipping restart"
    else
        log "Local LLM :18090 unresponsive — restarting..."
        if sudo systemctl restart llama-local 2>/dev/null; then
            log "llama-local restarted"
            _set_cooldown local-llm
        else
            log "llama-local restart FAILED"
        fi
        restarts=$((restarts + 1))
    fi
else
    _clear_cooldown local-llm
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

# --- 5. HTTP health: Hermes Memory MCP ---
if ! curl -sf --max-time 5 http://localhost:8091/health &>/dev/null; then
    if [ "$(_in_cooldown hermes-memory-mcp)" = "yes" ]; then
        log "hermes-memory-mcp DOWN but in cooldown — skipping restart"
    else
        log "hermes-memory-mcp :8091 unresponsive — restarting container..."
        if docker restart hermes-memory-mcp 2>/dev/null; then
            log "hermes-memory-mcp restarted"
            _set_cooldown hermes-memory-mcp
        else
            log "hermes-memory-mcp restart FAILED"
        fi
        restarts=$((restarts + 1))
    fi
else
    _clear_cooldown hermes-memory-mcp
fi

# --- 6. HTTP health: Portainer ---
if ! curl -sf --max-time 5 http://localhost:9000/ &>/dev/null; then
    if [ "$(_in_cooldown portainer)" = "yes" ]; then
        log "portainer DOWN but in cooldown — skipping restart"
    else
        log "portainer :9000 unresponsive — restarting container..."
        if docker restart portainer 2>/dev/null; then
            log "portainer restarted"
            _set_cooldown portainer
        else
            log "portainer restart FAILED"
        fi
        restarts=$((restarts + 1))
    fi
else
    _clear_cooldown portainer
fi

# --- 7. HTTP health: Stump (ebook UI) ---
if ! curl -sf --max-time 5 http://localhost:10801/ &>/dev/null; then
    if [ "$(_in_cooldown stump)" = "yes" ]; then
        log "stump DOWN but in cooldown — skipping restart"
    else
        log "stump :10801 unresponsive — restarting container..."
        if docker restart stump 2>/dev/null; then
            log "stump restarted"
            _set_cooldown stump
        else
            log "stump restart FAILED"
        fi
        restarts=$((restarts + 1))
    fi
else
    _clear_cooldown stump
fi

# --- 6. Context Harvester heartbeat ---
HB_FILE="/home/rohit/agentharness/data/harvester_heartbeat.json"
if [ -f "$HB_FILE" ]; then
    hb_age=$(( $(date +%s) - $(stat -c %Y "$HB_FILE") ))
    if [ "$hb_age" -gt 3600 ]; then
        log "Context harvester heartbeat STALE (${hb_age}s old — expected < 3600s)"
    fi
else
    log "No harvester heartbeat file found — harvester may not have run yet"
fi

# --- 7. Summary ---
if [ "$restarts" -gt 0 ]; then
    log "Completed with ${restarts} restart(s)"
else
    log "All services healthy"
fi
