#!/usr/bin/env bash
# =============================================================================
# start_llm_server.sh — Start LLM server and proxy on boot
#
# Called from cron: @reboot sleep 30 && bash /home/rohit/agentharness/scripts/start_llm_server.sh
# =============================================================================

LOG=/home/rohit/agentharness/logs/startup.log
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] startup: $*" >> "$LOG"; }

mkdir -p /home/rohit/agentharness/logs

# 1. LLM server is managed by systemd (llama-primary.service)
# Just verify it's running
log "Checking llama-primary service..."
if systemctl is-active llama-primary &>/dev/null; then
    log "llama-primary already running"
else
    log "Starting llama-primary..."
    sudo -n systemctl start llama-primary 2>/dev/null || log "Failed to start llama-primary"
fi

# Wait for LLM to be ready
for i in {1..30}; do
    curl -sf --max-time 5 http://localhost:8081/health &>/dev/null && break
    sleep 2
done
if curl -sf --max-time 5 http://localhost:8081/health &>/dev/null; then
    log "LLM server healthy on port 8081"
else
    log "WARNING: LLM server not responding after 60s"
fi

# 2. Start LLM proxy
log "Starting LLM proxy on port 8080..."
cd /home/rohit/agentharness && set -a && source data/.env && set +a
export PYTHONUNBUFFERED=1
nohup /home/rohit/agentharness/venv/bin/python3 -m core.providers.proxy_server     --host 0.0.0.0 --port 8080 --data-dir /home/rohit/agentharness/data     > /home/rohit/agentharness/logs/proxy_stdout.log 2>&1 &

sleep 5
if curl -sf --max-time 5 http://localhost:8080/health &>/dev/null; then
    log "LLM proxy healthy on port 8080"
else
    log "WARNING: LLM proxy not responding"
fi

log "Startup complete"
