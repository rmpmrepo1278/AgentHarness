#!/usr/bin/env bash
# =============================================================================
# start_llm_server.sh — Start LLM server and proxy on boot (FIXED)
# =============================================================================

# Export DBUS session bus so systemctl --user works from cron
_UID=$(id -u)
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/${_UID}/bus}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${_UID}}"

LOG=/home/rohit/agentharness/logs/startup.log
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] startup: $*" >> "$LOG"; }

mkdir -p /home/rohit/agentharness/logs

# 1. Start Local LLM (llama-local on port 18090)
log "Starting llama-local..."
sudo systemctl start llama-local 2>/dev/null || log "Failed to start llama-local"

# Wait for LLM to be ready
for i in {1..30}; do
    curl -sf --max-time 5 http://localhost:18090/health &>/dev/null && break
    sleep 2
done

# 2. Start LLM Proxy via Systemd
log "Starting LLM proxy via systemd..."
systemctl --user start llm-proxy 2>/dev/null || log "Failed to start llm-proxy"

sleep 5
if curl -sf --max-time 5 http://localhost:8080/health &>/dev/null; then
    log "LLM proxy healthy on port 8080"
else
    log "WARNING: LLM proxy not responding"
fi

log "Startup complete"
