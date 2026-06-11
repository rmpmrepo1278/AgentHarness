#!/bin/bash
# =============================================================================
# backup_and_push.sh — Push latest local backup to Google Drive (off-site)
#
# Pushes only critical/compact backups to Google Drive:
#   - DB dumps (paperless, immich, state.db, claudemec.db)
#   - Service configs (n8n, npm, vaultwarden, pihole, netdata, agentharness)
#   - Excludes multi-GB dirs (hermes-config, calibre-web) — those stay local-only
#
# Called by backup_all.sh after local backup + DB dump complete.
# =============================================================================

set -euo pipefail

LOG_FILE="/home/rohit/agentharness/logs/backup_push_$(date +%Y%m%d).log"
DRIVE_REMOTE="gdrive-backup"
DRIVE_BASE="homelab-backup"
TODAY="$(date +%Y-%m-%d)"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] backup_push: $*" | tee -a "$LOG_FILE" 2>/dev/null
}

mkdir -p "$(dirname "$LOG_FILE")"

log "=== Starting Google Drive push ==="

# ── Files to push (lightweight, critical) ──────────────────────────────────
# Format: "local_path|drive_subdir" — empty drive_subdir = push to day root
PUSH_TARGETS=(
    "/mnt/usb/backups/docker-volumes/${TODAY}/n8n.tar.gz|docker-volumes/${TODAY}"
    "/mnt/usb/backups/docker-volumes/${TODAY}/nginx-proxy-manager.tar.gz|docker-volumes/${TODAY}"
    "/mnt/usb/backups/docker-volumes/${TODAY}/letsencrypt.tar.gz|docker-volumes/${TODAY}"
    "/mnt/usb/backups/docker-volumes/${TODAY}/vaultwarden.tar.gz|docker-volumes/${TODAY}"
    "/mnt/usb/backups/docker-volumes/${TODAY}/pihole.tar.gz|docker-volumes/${TODAY}"
    "/mnt/usb/backups/docker-volumes/${TODAY}/portainer.tar.gz|docker-volumes/${TODAY}"
    "/mnt/usb/backups/docker-volumes/${TODAY}/netdata-config.tar.gz|docker-volumes/${TODAY}"
    "/mnt/usb/backups/docker-volumes/${TODAY}/netdata-lib.tar.gz|docker-volumes/${TODAY}"
    "/mnt/usb/backups/docker-volumes/${TODAY}/agentharness-data.tar.gz|docker-volumes/${TODAY}"
    "/mnt/usb/backups/docker-volumes/${TODAY}/homepage-config.tar.gz|docker-volumes/${TODAY}"
    "/mnt/usb/backups/docker-volumes/${TODAY}/stump-config.tar.gz|docker-volumes/${TODAY}"
    "/mnt/usb/backups/db-dumps/${TODAY}/paperless.dump|db-dumps/${TODAY}"
    "/mnt/usb/backups/db-dumps/${TODAY}/immich.dump|db-dumps/${TODAY}"
    "/mnt/usb/backups/db-dumps/${TODAY}/state-$(date +%Y%m%d).db|db-dumps/${TODAY}"
    "/mnt/usb/backups/db-dumps/${TODAY}/claudemem-$(date +%Y%m%d).db|db-dumps/${TODAY}"
)

# ── Upload each file ───────────────────────────────────────────────────────
ok=0
fail=0
skip=0

for target in "${PUSH_TARGETS[@]}"; do
    local_path="${target%%|*}"
    drive_subdir="${target##*|}"

    if [ ! -f "$local_path" ]; then
        log "[?] Skip $(basename "$local_path") — not found locally"
        skip=$((skip + 1))
        continue
    fi

    file_size=$(du -h "$local_path" 2>/dev/null | cut -f1)
    log "[>] Pushing $(basename "$local_path") ($file_size) → ${DRIVE_BASE}/${drive_subdir}/"

    if rclone copy "$local_path" "${DRIVE_REMOTE}:${DRIVE_BASE}/${drive_subdir}/" \
        --log-file "$LOG_FILE" --log-level INFO --retries 2 2>/dev/null; then
        log "[✓] $(basename "$local_path") pushed OK"
        ok=$((ok + 1))
    else
        log "[✗] $(basename "$local_path") FAILED"
        fail=$((fail + 1))
    fi
done

# ── Prune old backups on Drive (keep 3 most recent days) ──────────────────
log "Pruning old Drive backups (keep 3 most recent)..."

for subdir in docker-volumes db-dumps; do
    rclone lsd "${DRIVE_REMOTE}:${DRIVE_BASE}/${subdir}/" 2>/dev/null | \
        awk '{print $NF}' | \
        sort -r | \
        tail -n +4 | \
        while read -r old_dir; do
            log "  Deleting: ${subdir}/${old_dir}"
            rclone purge "${DRIVE_REMOTE}:${DRIVE_BASE}/${subdir}/${old_dir}" \
                --log-file "$LOG_FILE" 2>/dev/null || true
        done
done

# ── Empty Drive trash to reclaim space ─────────────────────────────────────
rclone cleanup "${DRIVE_REMOTE}:" 2>/dev/null || true

log "=== Push complete: $ok OK, $fail failed, $skip skipped ==="

[ "$fail" -gt 0 ] && exit 1
exit 0
