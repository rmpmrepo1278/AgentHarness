#!/usr/bin/env bash
# =============================================================================
# sync_calibre_to_onedrive.sh — Two-way sync between Calibre and OneDrive
#
# Sync direction:
# 1. Upload: Any new/changed files in Calibre → OneDrive (nickynrohit@live.com/Nicky/eBooks)
# 2. Download: Any new files in OneDrive that aren't in Calibre → import to Calibre
#
# This ensures both locations stay in sync:
# - If you add a book to OneDrive manually, it appears in Calibre
# - If you add a book to Calibre, it's backed up to OneDrive
#
# Schedule: Daily at 1:00 PM PT (20:00 UTC)
# =============================================================================

set -euo pipefail

REMOTE="msonedrive"
REMOTE_PATH="Nicky/eBooks"
SOURCE_DIR="/mnt/usb/ebooks/Books"
LOG_FILE="/home/rohit/agentharness/logs/calibre_onedrive_sync_$(date +%Y%m%d).log"
LOCK_FILE="/tmp/calibre_onedrive_sync.lock"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] calibre-onedrive: $*" | tee -a "$LOG_FILE"
}

# Prevent concurrent runs
if [ -f "$LOCK_FILE" ]; then
    PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        log "Another sync is running (PID=$PID) — exiting"
        exit 0
    fi
    rm -f "$LOCK_FILE"
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# Check USB is mounted
if ! mountpoint -q /mnt/usb 2>/dev/null; then
    log "ERROR: /mnt/usb not mounted — aborting"
    exit 1
fi

# Check remote exists
if ! rclone listremotes 2>/dev/null | grep -q "^${REMOTE}:$"; then
    log "ERROR: rclone remote '$REMOTE' not found. Run setup_onedrive.sh first."
    exit 1
fi

log "Starting two-way Calibre ↔ OneDrive sync"
log "Calibre: $SOURCE_DIR ↔ OneDrive: $REMOTE_PATH"

# Step 1: Upload new/changed files from Calibre to OneDrive
log "Step 1: Uploading Calibre → OneDrive..."
rclone sync "$SOURCE_DIR" "${REMOTE}:${REMOTE_PATH}" \
    --fast-list \
    --transfers 4 \
    --checkers 8 \
    --log-level INFO \
    --stats 30s \
    2>&1 | tee -a "$LOG_FILE"

UPLOAD_EXIT=$?

# Step 2: Download new files from OneDrive that aren't in Calibre
log "Step 2: Downloading OneDrive → Calibre..."
# Create a temp dir for new files
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"; rm -f "$LOCK_FILE"' EXIT

# Copy from OneDrive to temp, then import to Calibre
rclone copy "${REMOTE}:${REMOTE_PATH}" "$TEMP_DIR" \
    --fast-list \
    --transfers 4 \
    --max-depth 1 \
    --include "*.pdf" \
    --include "*.epub" \
    --include "*.mobi" \
    --include "*.azw3" \
    2>&1 | tee -a "$LOG_FILE"

# Import any new files into Calibre
NEW_COUNT=0
for f in "$TEMP_DIR"/*.{pdf,epub,mobi,azw3} 2>/dev/null; do
    [ -f "$f" ] || continue
    filename=$(basename "$f")
    # Check if file already exists in Calibre
    if docker exec calibre-web calibredb list --library-path=/books/Books --search="$filename" 2>/dev/null | grep -q "$filename"; then
        log "  ~ $filename (already in Calibre)"
    else
        log "  ✓ Importing new file: $filename"
        docker cp "$f" calibre-web:/books/Books/
        docker exec calibre-web calibredb add --library-path=/books/Books "/books/Books/$filename" 2>/dev/null
        docker exec calibre-web rm -f "/books/Books/$filename"
        NEW_COUNT=$((NEW_COUNT + 1))
    fi
done

log "Sync complete. New files imported: $NEW_COUNT"

# Clean up old logs (keep 30 days)
find "$(dirname "$LOG_FILE")" -name "calibre_onedrive_sync_*.log" -mtime +30 -delete 2>/dev/null || true

log "Two-way sync finished"
exit 0
