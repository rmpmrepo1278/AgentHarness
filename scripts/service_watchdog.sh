#!/usr/bin/env bash
set -euo pipefail
LOG_PREFIX="[$(date "+%Y-%m-%d %H:%M:%S")] watchdog"
log() { echo "${LOG_PREFIX}: $*"; }
restarts=0
if ! systemctl is-active hermes-gateway &>/dev/null; then
    log "hermes-gateway is DOWN — restarting..."
    sudo systemctl restart hermes-gateway 2>/dev/null && log "hermes-gateway restarted" || log "hermes-gateway restart FAILED"
    restarts=$((restarts + 1))
fi
if ! systemctl is-active agentharness-llm-proxy &>/dev/null; then
    log "agentharness-llm-proxy is DOWN — restarting..."
    sudo systemctl restart agentharness-llm-proxy 2>/dev/null && log "agentharness-llm-proxy restarted" || log "agentharness-llm-proxy restart FAILED"
    restarts=$((restarts + 1))
elif ! curl -sf --max-time 5 http://localhost:8080/health &>/dev/null; then
    log "LLM proxy unresponsive on :8080 — restarting..."
    sudo systemctl restart agentharness-llm-proxy 2>/dev/null && log "agentharness-llm-proxy restarted" || log "agentharness-llm-proxy restart FAILED"
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
