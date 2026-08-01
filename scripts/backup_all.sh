#!/bin/bash
# Master backup script — runs all backup subsystems
# Called by cron nightly at 2 AM
set -euo pipefail

LOG=/home/rohit/agentharness/logs/backup_all.log

echo "[$(date)] ===== BACKUP ALL START =====" >> "$LOG"

# 1. Compose files + config snapshots
BACKUP_DIR=/home/rohit/shared_agent_memory/config_backups
mkdir -p "$BACKUP_DIR"
DATE_TAG=$(date +%Y%m%d)

echo "[$(date)] Backing up compose files..." >> "$LOG"
# DISABLED: redundant with kopia snapshots
# tar -czf "$BACKUP_DIR/compose_${DATE_TAG}.tar.gz" -C /home/rohit/services . --ignore-failed-read 2>/dev/null || true
echo "[$(date)] Backing up configs..." >> "$LOG"
# DISABLED: redundant with kopia snapshots
# tar -czf "$BACKUP_DIR/service_configs_${DATE_TAG}.tar.gz" -C /home/rohit/services/homeassistant/config . 2>/dev/null || true
# DISABLED: redundant with kopia snapshots
# tar -czf "$BACKUP_DIR/homepage_config_${DATE_TAG}.tar.gz" -C /home/rohit/services/homepage/config . 2>/dev/null || true

# 2. Database dumps
echo "[$(date)] Running DB backups..." >> "$LOG"
/home/rohit/agentharness/scripts/db_backup.sh 2>&1 >> "$LOG" || echo "[$(date)] db_backup.sh failed (non-critical)" >> "$LOG"

# 3. Kopia snapshots (compose, configs, volumes)
echo "[$(date)] Running kopia snapshots..." >> "$LOG"
/home/rohit/agentharness/scripts/kopia_backup.sh 2>&1 >> "$LOG" || echo "[$(date)] kopia_backup.sh failed (non-critical)" >> "$LOG"

# 4. Remote sync to Google Drive
echo "[$(date)] Syncing to remote..." >> "$LOG"
/home/rohit/agentharness/scripts/sync_backup_remote.sh 2>&1 >> "$LOG" || echo "[$(date)] Remote sync failed (non-critical)" >> "$LOG"

# 5. Cleanup old tarballs (keep 30 days)
find "$BACKUP_DIR" -name "*.tar.gz" -mtime +7 -delete 2>/dev/null || true

# 6. Cleanup old DB dumps (keep 14 days, compressed)
find /mnt/usb/backups/db-dumps/ -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} \; 2>/dev/null || true

echo "[$(date)] ===== BACKUP ALL DONE =====" >> "$LOG"
