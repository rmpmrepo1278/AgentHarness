#!/usr/bin/env bash
# =============================================================================
# start_llm_server.sh — Start LLM proxy on boot
# =============================================================================

# Export DBUS session bus so systemctl --user works from cron
_UID=$(id -u)
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/${_UID}/bus}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${_UID}}"

LOG=/home/rohit/agentharness/logs/startup.log
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] startup: $*" >> "$LOG"; }

mkdir -p /home/rohit/agentharness/logs

# 1. Verify Ollama is running (local LLM on 11434)
log "Checking Ollama..."
for i in {1..15}; do
    curl -sf --max-time 3 http://localhost:11434/ &>/dev/null && break
    sleep 2
done
if curl -sf --max-time 3 http://localhost:11434/ &>/dev/null; then
    log "Ollama healthy on port 11434"
else
    log "WARNING: Ollama not responding on port 11434"
fi

# 2. Start LLM Proxy via Systemd
log "Starting LLM proxy via systemd..."
systemctl start agentharness-llm-proxy 2>/dev/null || log "Failed to start agentharness-llm-proxy"

sleep 5
if curl -sf --max-time 5 http://localhost:8080/health &>/dev/null; then
    log "LLM proxy healthy on port 8080"
else
    log "WARNING: LLM proxy not responding"
fi

log "Startup complete"
