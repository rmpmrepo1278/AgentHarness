#!/usr/bin/env bash
# =============================================================================
# backup_volumes.sh — Docker volume backups to USB
#
# Backs up key Docker volumes and data directories to /mnt/usb/backups/.
# Keeps 7 days of backups.
#
# Run manually or via cron. Not in active crontab (was never scheduled).
# =============================================================================

set -euo pipefail

BACKUP_DIR="/mnt/usb/backups/docker-volumes/$(date +%Y-%m-%d)"
LOG_FILE="/home/rohit/agentharness/logs/backup_$(date +%Y%m%d).log"

mkdir -p "$BACKUP_DIR" "$(dirname "$LOG_FILE")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] backup: $*" | tee -a "$LOG_FILE"; }

# Check USB is mounted
if ! mountpoint -q /mnt/usb 2>/dev/null; then
    log "ERROR: /mnt/usb is not mounted — aborting backup"
    exit 1
fi

# Check available space (need at least 1GB)
AVAIL_KB=$(df --output=avail /mnt/usb 2>/dev/null | tail -1 | tr -d ' ')
if [ "${AVAIL_KB:-0}" -lt 1048576 ]; then
    log "ERROR: Less than 1GB free on /mnt/usb — aborting backup"
    exit 1
fi

log "Starting Homelab Backup to $BACKUP_DIR"

# List of targets: {Name} {Path}
targets=(
    "n8n:/var/lib/docker/volumes/n8n_data/_data"
    "nginx-proxy-manager:/var/lib/docker/volumes/npm_npm-data/_data"
    "letsencrypt:/var/lib/docker/volumes/npm_npm-letsencrypt/_data"
    "vaultwarden:/opt/vaultwarden"
    "gitea:/home/rohit/openclaw/data/gitea"
    "paperless:/home/rohit/services/data/paperless"
    "immich_db:/var/lib/docker/volumes/immich_pgdata/_data"
    "pihole:/var/lib/docker/volumes/pihole_pihole_data/_data"
    "portainer:/var/lib/docker/volumes/portainer_data/_data"
    "mnemo:/var/lib/docker/volumes/agentharness_mnemo-data/_data"
)

succeeded=0
failed=0
skipped=0

for target in "${targets[@]}"; do
    name="${target%%:*}"
    path="${target#*:}"

    if [ ! -d "$path" ]; then
        log "[?] Skipping $name (path not found: $path)"
        skipped=$((skipped + 1))
        continue
    fi

    log "[>] Backing up $name ($path)..."
    backup_file="$BACKUP_DIR/${name}.tar.gz"

    if tar czf "$backup_file" -C "$path" . 2>>"$LOG_FILE"; then
        size=$(du -h "$backup_file" 2>/dev/null | cut -f1)
        log "[✓] $name backed up ($size)"
        succeeded=$((succeeded + 1))
    else
        log "[✗] FAILED $name"
        failed=$((failed + 1))
    fi
done

# Cleanup: keep last 7 days
cleaned=$(find "/mnt/usb/backups/docker-volumes" -maxdepth 1 -type d -mtime +7 2>/dev/null | wc -l)
find "/mnt/usb/backups/docker-volumes" -maxdepth 1 -type d -mtime +7 -exec rm -rf {} + 2>/dev/null || true
log "Cleaned $cleaned old backup(s)"

log "Backup Finished: $succeeded succeeded, $failed failed, $skipped skipped"

# Exit with error if any backups failed
if [ "$failed" -gt 0 ]; then
    log "WARNING: $failed backup(s) failed"
    exit 1
fi
