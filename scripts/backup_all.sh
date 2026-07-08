#!/bin/bash
LOG=/home/rohit/agentharness/logs/backup_all.log
echo "[$(date)] START backup_all" >> "$LOG"

# Backup compose files
BACKUP_DIR=/home/rohit/shared_agent_memory/config_backups
mkdir -p "$BACKUP_DIR"
tar -czf "$BACKUP_DIR/compose_20260707.tar.gz" -C /home/rohit/services . --ignore-failed-read 2>/dev/null || true

# Backup configs
tar -czf "$BACKUP_DIR/service_configs_20260707.tar.gz" -C /home/rohit/services/homeassistant/config . 2>/dev/null || true
tar -czf "$BACKUP_DIR/homepage_config_20260707.tar.gz" -C /home/rohit/services/homepage/config . 2>/dev/null || true

# Cleanup (keep 30 days)
find "$BACKUP_DIR" -name "*.tar.gz" -mtime +30 -delete 2>/dev/null || true
echo "[$(date)] DONE backup_all" >> "$LOG"
