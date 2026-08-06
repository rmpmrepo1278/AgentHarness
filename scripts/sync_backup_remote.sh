#!/bin/bash
# Sync latest homelab backups to Google Drive (alternate account)
# Syncs fresh db-dumps (databases) to keep offsite redundancy current.
set -euo pipefail

BACKUP_BASE=/mnt/usb/backups/db-dumps
REMOTE=gdrive-backup:homelab-backup/db-dumps
LOG_FILE=/home/rohit/agentharness/logs/cloud_sync.log

LATEST=$(ls -1d "$BACKUP_BASE"/*/ 2>/dev/null | sort -r | head -1)
if [ -z "$LATEST" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] No backups to sync" | tee -a "$LOG_FILE"
    exit 0
fi

DATE=$(basename "$LATEST")
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Syncing db-dumps $DATE to Google Drive..." | tee -a "$LOG_FILE"

rclone copy "$LATEST" "$REMOTE/$DATE/" -v 2>&1 | tee -a "$LOG_FILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sync complete" | tee -a "$LOG_FILE"