#!/usr/bin/env bash
# =============================================================================
# db_backup.sh — Nightly database dumps to USB
#
# Dumps Paperless (Postgres) and Immich (Postgres) databases to
# /mnt/usb/backups/db-dumps/. Keeps 14 days of dumps.
# =============================================================================

set -euo pipefail

BACKUP_DIR="/mnt/usb/backups/db-dumps/$(date +%Y-%m-%d)"
LOG_FILE="/home/rohit/agentharness/logs/db_backup_$(date +%Y%m%d).log"

mkdir -p "$BACKUP_DIR" "$(dirname "$LOG_FILE")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] db_backup: $*" | tee -a "$LOG_FILE"; }

# Check USB is mounted
if ! mountpoint -q /mnt/usb 2>/dev/null; then
    log "ERROR: /mnt/usb is not mounted — aborting"
    exit 1
fi

log "Starting database backups to $BACKUP_DIR"

succeeded=0
failed=0

# Paperless (Postgres 16, on compose_default network)
log "[>] Dumping paperless (Postgres)..."
if PGPASSWORD=paperless_db_pass pg_dump -h 172.18.0.6 -U paperless -d paperless -Fc > "$BACKUP_DIR/paperless.dump" 2>>"$LOG_FILE"; then
    size=$(du -h "$BACKUP_DIR/paperless.dump" 2>/dev/null | cut -f1)
    log "[✓] paperless backed up ($size)"
    succeeded=$((succeeded + 1))
else
    log "[✗] FAILED paperless"
    failed=$((failed + 1))
fi

# Immich (Postgres pgvecto.rs, on immich_default network)
log "[>] Dumping immich (Postgres)..."
if PGPASSWORD=postgres pg_dump -h 172.20.0.2 -U postgres -d immich -Fc > "$BACKUP_DIR/immich.dump" 2>>"$LOG_FILE"; then
    size=$(du -h "$BACKUP_DIR/immich.dump" 2>/dev/null | cut -f1)
    log "[✓] immich backed up ($size)"
    succeeded=$((succeeded + 1))
else
    log "[✗] FAILED immich"
    failed=$((failed + 1))
fi

# SQLite backups — Hermes state DBs
log "[>] Backing up state.db (SQLite)..."
state_backup="$BACKUP_DIR/state-$(date +%Y%m%d).db"
if sqlite3 /home/rohit/.hermes/state.db ".backup '$state_backup'" 2>>"$LOG_FILE"; then
    size=$(du -h "$BACKUP_DIR"/state-*.db 2>/dev/null | tail -1 | cut -f1)
    log "[✓] state.db backed up ($size)"
    succeeded=$((succeeded + 1))
else
    log "[✗] FAILED state.db"
    failed=$((failed + 1))
fi

log "[>] Backing up claudemem.db (SQLite)..."
if sqlite3 /home/rohit/.hermes/claudemem.db ".backup '$BACKUP_DIR/claudemem-$(date +%Y%m%d).db'" 2>>"$LOG_FILE"; then
    size=$(du -h "$BACKUP_DIR"/claudemem-*.db 2>/dev/null | tail -1 | cut -f1)
    log "[✓] claudemem.db backed up ($size)"
    succeeded=$((succeeded + 1))
else
    log "[✗] FAILED claudemem.db"
    failed=$((failed + 1))
fi

# Cleanup: keep 14 days of DB dumps
cleaned=$(find "/mnt/usb/backups/db-dumps" -maxdepth 1 -type d -mtime +14 2>/dev/null | wc -l)
find "/mnt/usb/backups/db-dumps" -maxdepth 1 -type d -mtime +14 -exec rm -rf {} + 2>/dev/null || true
log "Cleaned $cleaned old backup(s)"

log "DB Backup Finished: $succeeded succeeded, $failed failed"
[ "$failed" -eq 0 ] || exit 1
