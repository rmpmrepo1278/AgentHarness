#!/usr/bin/env bash
# =============================================================================
# docker_ghost_check.sh — Detect ghost containers and stale port bindings
#
# Ghost containers: Docker metadata references that block compose up -d
# Stale ports: Listeners with no owning process (leaked docker-proxy)
#
# Run via cron every 30 min. Logs to agentharness/logs/.
# =============================================================================

set -uo pipefail

LOG="/home/rohit/agentharness/logs/ghost_check.log"
mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

ISSUES=0

# --- 1. Ghost container detection ---
# Compare docker ps -aq (all containers) with what docker compose expects
# Ghosts appear as "Conflict. The container name is already in use" errors

log "=== Ghost container check ==="

# Get all compose projects
COMPOSE_DIR="/home/rohit/services/docker/compose"
MCP_DIR="/home/rohit/agentharness"

# Check compose projects
for f in "$COMPOSE_DIR"/*.yml; do
    [ -f "$f" ] || continue
    project=$(basename "$f" .yml)
    # Try a dry-run to detect conflicts
    result=$(docker compose -f "$f" create --dry-run 2>&1)
    if echo "$result" | grep -q "Conflict"; then
        ghost=$(echo "$result" | grep "container name" | grep -oP '"/[^"]+"' | head -1)
        log "GHOST in $project: $ghost"
        ISSUES=$((ISSUES + 1))
    fi
done

# Check MCP compose
if [ -f "$MCP_DIR/docker-compose.mcp.yml" ]; then
    result=$(docker compose -f "$MCP_DIR/docker-compose.mcp.yml" create --dry-run 2>&1)
    if echo "$result" | grep -q "Conflict"; then
        ghost=$(echo "$result" | grep "container name" | grep -oP '"/[^"]+"' | head -1)
        log "GHOST in mcp: $ghost"
        ISSUES=$((ISSUES + 1))
    fi
fi

# --- 2. Stale port bindings ---
log "=== Stale port check ==="

# Find listeners with no owning PID
STALE_PORTS=$(ss -tlnp 2>/dev/null | awk 'NR>1 && $6=="LISTEN" && $6!~"users:" {print $4}')
if [ -n "$STALE_PORTS" ]; then
    while IFS= read -r port; do
        log "STALE PORT: $port (no owning process)"
        ISSUES=$((ISSUES + 1))
    done <<< "$STALE_PORTS"
fi

# --- 3. Container restart loop detection ---
log "=== Restart loop check ==="

for cid in $(docker ps -aq 2>/dev/null); do
    name=$(docker inspect "$cid" --format '{{.Name}}' 2>/dev/null | sed 's|^/||')
    restarts=$(docker inspect "$cid" --format '{{.State.RestartCount}}' 2>/dev/null)
    status=$(docker inspect "$cid" --format '{{.State.Status}}' 2>/dev/null)

    if [ "$status" = "restarting" ] || [ "${restarts:-0}" -gt 10 ]; then
        log "RESTART LOOP: $name (restarts: $restarts, status: $status)"
        ISSUES=$((ISSUES + 1))
    fi
done

# --- Summary ---
if [ "$ISSUES" -gt 0 ]; then
    log "ALERT: $ISSUES issue(s) detected"
    # Truncate log if too large
    if [ "$(wc -l < "$LOG")" -gt 1000 ]; then
        tail -500 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
    fi
    exit 1
else
    log "OK: No issues detected"
    exit 0
fi
