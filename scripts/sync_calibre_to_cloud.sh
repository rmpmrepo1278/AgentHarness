#!/usr/bin/env bash
# =============================================================================
# sync_calibre_to_cloud.sh — Sync Calibre library to Google Drive
#
# Backs up the Calibre ebook library from /mnt/usb/ebooks/Books/ to Google
# Drive using rclone. Runs incrementally — only syncs new/changed files.
#
# Usage:
#   ./sync_calibre_to_cloud.sh          # Sync to default gdrive
#   ./sync_calibre_to_cloud.sh onedrive # Sync to onedrive (if configured)
#
# Cron: Runs daily at 3:30 AM via cron
# =============================================================================

set -euo pipefail

REMOTE="${1:-gdrive}"
REMOTE_PATH="calibre-library"
SOURCE_DIR="/mnt/usb/ebooks/Books"
LOG_FILE="/home/rohit/agentharness/logs/calibre_sync_$(date +%Y%m%d).log"
LOCK_FILE="/tmp/calibre_sync.lock"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] calibre-sync: $*" | tee -a "$LOG_FILE"
}

# Prevent concurrent runs
if [ -f "$LOCK_FILE" ]; then
    PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        log "Another sync is already running (PID=$PID) — exiting"
        exit 0
    fi
    log "Stale lock file found — removing"
    rm -f "$LOCK_FILE"
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# Check USB is mounted
if ! mountpoint -q /mnt/usb 2>/dev/null; then
    log "ERROR: /mnt/usb is not mounted — aborting sync"
    exit 1
fi

# Check rclone remote exists
if ! rclone listremotes 2>/dev/null | grep -q "^${REMOTE}:$"; then
    log "ERROR: rclone remote '$REMOTE' not found — aborting"
    exit 1
fi

# Check available USB space (need at least 1GB free for temp operations)
AVAIL_KB=$(df --output=avail /mnt/usb 2>/dev/null | tail -1 | tr -d ' ')
if [ "${AVAIL_KB:-0}" -lt 1048576 ]; then
    log "ERROR: Less than 1GB free on /mnt/usb — aborting"
    exit 1
fi

log "Starting Calibre library sync to $REMOTE:$REMOTE_PATH"
log "Source: $SOURCE_DIR ($(du -sh "$SOURCE_DIR" 2>/dev/null | awk '{print $1}'))"

# Count files before sync
BEFORE_COUNT=$(rclone size "${REMOTE}:${REMOTE_PATH}" 2>/dev/null | grep "files:" | awk '{print $2}' || echo "0")

# Sync using rclone
# --fast-list: reduce API calls for large directories
# --transfers: parallel file transfers
# --checkers: parallel checksum checks
# --log-file: detailed log
RCLONE_LOG="/tmp/rclone_calibre_$(date +%Y%m%d_%H%M%M).log"

rclone sync "$SOURCE_DIR" "${REMOTE}:${REMOTE_PATH}" \
    --fast-list \
    --transfers 8 \
    --checkers 16 \
    --log-file "$RCLONE_LOG" \
    --log-level INFO \
    --stats 60s \
    --stats-log-level NOTICE \
    2>&1 | tee -a "$LOG_FILE"

SYNC_EXIT=$?

# Count files after sync
AFTER_COUNT=$(rclone size "${REMOTE}:${REMOTE_PATH}" 2>/dev/null | grep "files:" | awk '{print $2}' || echo "0")

log "Sync complete: $BEFORE_COUNT → $AFTER_COUNT files on $REMOTE"

# Log new files added
if [ -f "$RCLONE_LOG" ]; then
    NEW_FILES=$(grep -c "Copied (new)" "$RCLONE_LOG" 2>/dev/null || echo "0")
    UPDATED_FILES=$(grep -c "Copied (replaced existing)" "$RCLONE_LOG" 2>/dev/null || echo "0")
    log "New files: $NEW_FILES, Updated files: $UPDATED_FILES"

    # Clean up old rclone logs (keep 7 days)
    find /tmp -name "rclone_calibre_*.log" -mtime +7 -delete 2>/dev/null || true
fi

# Clean up old sync logs (keep 30 days)
find "$(dirname "$LOG_FILE")" -name "calibre_sync_*.log" -mtime +30 -delete 2>/dev/null || true

if [ $SYNC_EXIT -ne 0 ]; then
    log "WARNING: rclone exited with code $SYNC_EXIT"
fi

log "Calibre sync to $REMOTE complete"
exit $SYNC_EXIT
