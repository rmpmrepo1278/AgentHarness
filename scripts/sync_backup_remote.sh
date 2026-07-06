#!/bin/bash
# Sync latest homelab backup to Google Drive (alternate account)
set -euo pipefail

BACKUP_BASE=/mnt/usb/backups/docker-volumes
REMOTE=gdrive-backup:homelab-backup/docker-volumes
LOG_FILE=/home/rohit/agentharness/logs/cloud_sync.log

LATEST=$(ls -1d "$BACKUP_BASE"/*/ 2>/dev/null | sort -r | head -1)
if [ -z "$LATEST" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] No backups to sync" | tee -a "$LOG_FILE"
    exit 0
fi

DATE=$(basename "$LATEST")
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Syncing $DATE to Google Drive..." | tee -a "$LOG_FILE"

# Exclude large calibre-web files that exceed Drive quota
rclone copy "$LATEST" "$REMOTE/$DATE/" -v --exclude "calibre-web-config.tar.gz" 2>&1 | tee -a "$LOG_FILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sync complete" | tee -a "$LOG_FILE"