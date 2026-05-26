#!/usr/bin/env bash
# =============================================================================
# backup_all.sh — Full homelab backup (Docker volumes + DBs + agent state)
#
# Called by homelab-backup.timer at 2am daily. Retries up to 3 times.
# =============================================================================

set -euo pipefail

LOG="/home/rohit/agentharness/logs/backup_service.log"
MAX_RETRIES=3
RETRY_DELAY=60

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] backup_all: $*" | tee -a "$LOG"; }

run_with_retry() {
    local desc="$1"; shift
    for i in $(seq 1 $MAX_RETRIES); do
        if "$@" >> "$LOG" 2>&1; then
            log "[✓] $desc succeeded (attempt $i)"
            return 0
        fi
        log "[!] $desc failed (attempt $i/$MAX_RETRIES)"
        [ "$i" -lt "$MAX_RETRIES" ] && sleep "$RETRY_DELAY"
    done
    log "[✗] $desc FAILED after $MAX_RETRIES attempts"
    return 1
}

log "=== Full backup starting ==="

# 1. Docker volumes + config directories
run_with_retry "Docker volumes" /usr/bin/sudo /home/rohit/agentharness/scripts/backup_volumes.sh

# 2. Database dumps (Postgres + SQLite)
run_with_retry "Database dumps" /home/rohit/agentharness/scripts/db_backup.sh

log "=== Full backup complete ==="
