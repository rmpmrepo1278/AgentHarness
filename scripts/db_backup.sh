#!/bin/bash
# Database backup script using pg_dump
set -euo pipefail

BACKUP_DIR=/mnt/usb/backups/db-dumps
DATE_TAG=$(date +%Y%m%d_%H%M%S)
LOG=/home/rohit/agentharness/logs/db_backup.log

mkdir -p "$BACKUP_DIR"

echo "[$(date)] Starting DB backups..." >> "$LOG"

# Paperless PostgreSQL
echo "[$(date)] Backing up paperless-db..." >> "$LOG"
docker exec paperless-db pg_dump -U paperless paperless > "$BACKUP_DIR/paperless_${DATE_TAG}.sql" 2>> "$LOG" || echo "[$(date)] paperless-db backup failed" >> "$LOG"

# Immich PostgreSQL
echo "[$(date)] Backing up immich_database..." >> "$LOG"
docker exec immich_database pg_dump -U postgres immich > "$BACKUP_DIR/immich_${DATE_TAG}.sql" 2>> "$LOG" || echo "[$(date)] immich_database backup failed" >> "$LOG"

# Redis dump
echo "[$(date)] Backing up redis..." >> "$LOG"
docker exec redis redis-cli BGSAVE > /dev/null 2>&1 || true
sleep 1
docker cp redis:/data/dump.rdb "$BACKUP_DIR/redis_${DATE_TAG}.rdb" 2>> "$LOG" || echo "[$(date)] redis backup failed" >> "$LOG"

# Compress and cleanup old dumps
find "$BACKUP_DIR" -name "*.sql" -mtime +14 -delete 2>/dev/null || true
find "$BACKUP_DIR" -name "*.rdb" -mtime +14 -delete 2>/dev/null || true

echo "[$(date)] DB backups complete" >> "$LOG"
