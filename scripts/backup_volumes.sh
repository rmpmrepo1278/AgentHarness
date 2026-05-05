#!/bin/bash
set -euo pipefail

BACKUP_DIR="/mnt/usb/backups/docker-volumes/$(date +%Y-%m-%d)"
mkdir -p "$BACKUP_DIR"

echo "Starting Homelab Backup: $(date)"

# List of targets: {Name} {Path}
# We use a mix of local paths and Docker volume paths
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

for target in "${targets[@]}"; do
    name=${target%%:*}
    path=${target#*:}
    
    if [ -d "$path" ]; then
        echo "[✓] Backing up $name..."
        tar czf "$BACKUP_DIR/${name}.tar.gz" -C "$path" . 2>/dev/null && succeeded=$((succeeded+1)) || { echo "[!] Failed $name"; failed=$((failed+1)); }
    else
        echo "[?] Skipping $name (path not found: $path)"
    fi
done

# Cleanup: keep last 7 days
find "/mnt/usb/backups/docker-volumes" -maxdepth 1 -type d -mtime +7 -exec rm -rf {} \; 2>/dev/null || true

echo "Backup Finished: $succeeded succeeded, $failed failed"
