#!/usr/bin/env bash
# kopia_backup.sh — Run Kopia backups for DB and volumes
# Replaces the old backup_all.sh (tar-based) with deduplicated, encrypted Kopia snapshots
set -euo pipefail

LOG="/home/rohit/agentharness/logs/backup_service.log"
KOPIA="/usr/local/bin/kopia"
RETENTION_DB="14d:4w:12m"
RETENTION_VOL="7d:4w"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

log "=== Kopia backup starting ==="

# ── 1. DB snapshot (SQLite DBs, configs, scripts) ──
log "Snapshotting DB repo..."
$KOPIA snapshot create \
    /home/rohit/.hermes/claudemem.db \
    /home/rohit/.hermes/decisions.db \
    /home/rohit/.hermes/goals.db \
    /home/rohit/.hermes/state.db \
    /home/rohit/.hermes/config.yaml \
    /home/rohit/.hermes/topic_routes.json \
    /home/rohit/agentharness/data/ \
    /home/rohit/.claude/ \
    --description "daily-db-$(date +%Y%m%d)" \
    2>&1 | tee -a "$LOG" || log "WARN: DB snapshot had errors"

# ── 2. Volumes snapshot (Docker volume data) ──
log "Snapshotting volumes repo..."
$KOPIA snapshot create \
    /var/lib/docker/volumes/ \
    --description "daily-vol-$(date +%Y%m%d)" \
    2>&1 | tee -a "$LOG" || log "WARN: Volume snapshot had errors"

# ── 3. Maintenance (garbage collection) ──
log "Running maintenance..."
$KOPIA maintenance run --full 2>&1 | tee -a "$LOG" || log "WARN: Maintenance had errors"

# ── 4. Verify ──
log "Verifying latest snapshot..."
$KOPIA snapshot list -l 2>&1 | tail -5 | tee -a "$LOG"

log "=== Kopia backup complete ==="
