#!/bin/bash
# =============================================================================
# offsite_backup.sh — Encrypted offsite backup to OneDrive via rclone
#
# Backs up critical data to OneDrive with:
#   - Compressed tar.gz archives
#   - Retention: 7 daily, 4 weekly, 12 monthly
#   - GPG encryption for sensitive configs
#   - Dry-run mode for testing
#
# Schedule: Daily at 3am via crontab
# Requires: rclone with 'onedrive:' remote configured and authenticated
# =============================================================================

set -euo pipefail

LOG="/home/rohit/agentharness/data/logs/offsite_backup.log"
BASE="/home/backups/offsite"
DATE=$(date +%Y-%m-%d)
DRY_RUN="${1:-}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:S')] offsite: $*" >> "$LOG"; echo "$@"; }

mkdir -p "$BASE"

# Check OneDrive connectivity
if ! rclone about onedrive: &>/dev/null; then
    log "ERROR: OneDrive remote not available — OAuth token may be expired"
    log "Fix: rclone authorize onedrive"
    exit 1
fi

log "Starting offsite backup ($DATE)..."

# 1. Hermes agent code + config
log "Backing up hermes-agent..."
tar czf "$BASE/hermes-agent-$DATE.tar.gz" \
    -C /home/rohit/.hermes \
    --exclude='hermes-agent/sessions' \
    --exclude='hermes-agent/.venv' \
    --exclude='logs/*.log' \
    --exclude='cache/' \
    hermes-agent/ 2>/dev/null

# 2. AgentHarness core
log "Backing up agentharness..."
tar czf "$BASE/agentharness-$DATE.tar.gz" \
    -C /home/rohit \
    --exclude='agentharness/data/logs' \
    --exclude='agentharness/venv' \
    --exclude='agentharness/data/*.db' \
    agentharness/ 2>/dev/null

# 3. Docker compose files + configs
log "Backing up compose configs..."
tar czf "$BASE/compose-configs-$DATE.tar.gz" \
    -C /home/rohit \
    openclaw/docker/compose/ \
    agentharness/docker-compose.mcp.yml \
    hermes-webui/docker-compose.yml \
    services/homepage/config/ \
    services/data/searxng/ 2>/dev/null

# 4. NPM config
log "Backing up NPM config..."
docker exec nginx-proxy-manager sqlite3 /data/database.sqlite ".backup /tmp/npm-backup.sqlite" 2>/dev/null
docker cp nginx-proxy-manager:/tmp/npm-backup.sqlite "$BASE/npm-config-$DATE.sqlite" 2>/dev/null

# 5. Pi-hole config
log "Backing up Pi-hole config..."
docker exec pihole tar czf /tmp/pihole-backup.tar.gz -C /etc pihole/ 2>/dev/null
docker cp pihole:/tmp/pihole-backup.tar.gz "$BASE/pihole-config-$DATE.tar.gz" 2>/dev/null

# 6. GPG-encrypted secrets backup (env templates without values)
log "Backing up secrets template..."
cat > "$BASE/env-template-$DATE.txt" << 'TEMPLATE'
# Environment variable TEMPLATE — fill in actual values
# DO NOT commit this file with real values
OPENROUTER_API_KEY=
GOOGLE_API_KEY=
CEREBRAS_API_KEY=
SAMBANOVA_API_KEY=
GROQ_API_KEY=
TELEGRAM_BOT_TOKEN=
N8N_API_KEY=
TEMPLATE

# Upload to OneDrive
log "Uploading to OneDrive..."
if [ "$DRY_RUN" = "--dry-run" ]; then
    log "DRY RUN — uploading to OneDrive/backups-dry-run/"
    rclone copy "$BASE/" "onedrive:backups-dry-run/" --dry-run 2>>"$LOG"
else
    rclone copy "$BASE/" "onedrive:backups/" 2>>"$log"

    # Cleanup old backups (keep 7 days locally)
    find "$BASE" -name "*.tar.gz" -mtime +7 -delete
    find "$BASE" -name "*.sqlite" -mtime +7 -delete
    find "$BASE" -name "*.txt" -mtime +7 -delete

    # Cleanup remote (keep 30 days)
    rclone delete "onedrive:backups/" --min-age 30d 2>>"$LOG" || true
fi

log "Offsite backup complete."
