#!/usr/bin/env bash
set -euo pipefail
LOG_PREFIX="[$(date "+%Y-%m-%d %H:%M:%S")] watchdog"
log() { echo "${LOG_PREFIX}: $*"; }
restarts=0
# hermes-gateway is a user-level systemd service — must use --user flag
if ! systemctl --user is-active hermes-gateway &>/dev/null; then
    log "hermes-gateway is DOWN — restarting..."
    systemctl --user restart hermes-gateway 2>/dev/null && log "hermes-gateway restarted" || log "hermes-gateway restart FAILED"
    restarts=$((restarts + 1))
fi
# proxy runs as a process, not a systemd service — check via HTTP health
if ! curl -sf --max-time 5 http://localhost:8080/health &>/dev/null; then
    log "LLM proxy unresponsive on :8080 — killing and restarting..."
    pkill -f "proxy_server" 2>/dev/null; sleep 2
    nohup /home/rohit/agentharness/venv/bin/python3 -m core.providers.proxy_server \
        --host 0.0.0.0 --port 8080 --data-dir /home/rohit/agentharness/data \
        >> /home/rohit/agentharness/data/logs/proxy.log 2>&1 &
    log "proxy_server restarted (PID $!)"
    restarts=$((restarts + 1))
fi
if ! curl -sf --max-time 10 http://localhost:8081/health &>/dev/null; then
    log "Local LLM :8081 unresponsive — restarting..."
    sudo systemctl restart llama-primary 2>/dev/null && log "llama-primary restarted" || log "llama-primary restart FAILED"
    restarts=$((restarts + 1))
fi
if [ "${restarts}" -gt 0 ]; then
    log "Completed with ${restarts} restart(s)"
else
    log "All services healthy"
fi
