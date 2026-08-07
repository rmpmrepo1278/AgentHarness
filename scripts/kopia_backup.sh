#!/bin/bash
# Kopia snapshot wrapper using kopia CLI
# Uses root-maintained repo config (same repo root cron uses for Docker volumes).
set -euo pipefail

LOG=/home/rohit/agentharness/logs/kopia_backup.log
KOPIA=/usr/local/bin/kopia
CFG=/root/.config/kopia/repository.config
export KOPIA_PASSWORD=kopia-homelab-home-hp

FAILURES=0
FAILED_NAMES=""

# Send a Telegram alert via the same bot token the gateway-preflight uses.
alert_telegram() {
    local msg="$1"
    local TOKEN_FILE="/home/rohit/.hermes/.telegram_token"
    local CHAT_ID="${TELEGRAM_HOME_CHANNEL:--1003976074764}"
    [ -f "$TOKEN_FILE" ] || return 1
    local TOKEN
    TOKEN=$(cat "$TOKEN_FILE" | tr -d ' \n\r')
    [ -n "$TOKEN" ] || return 1
    curl -sf --connect-timeout 6 -X POST \
        "https://api.telegram.org/bot${TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${CHAT_ID}" \
        --data-urlencode "text=${msg}" \
        >/dev/null 2>&1 || echo "[$(date)] Telegram alert send failed" >> "$LOG"
}

# Wrap a snapshot attempt: on failure, count it and remember the label.
# Paths that don't exist are skipped (config drift, not a backup failure).
snapshot_one() {
    local label="$1" path="$2" tags="$3"
    [ -e "$path" ] || { echo "[$(date)] Skipping ${label} (path missing: ${path})" >> "$LOG"; return 0; }
    echo "[$(date)] Snapshotting ${label}..." >> "$LOG"
    if sudo $KOPIA --config-file=$CFG snapshot create "$path" --tags "$tags" 2>> "$LOG"; then
        echo "[$(date)] Snapshot OK: ${label}" >> "$LOG"
    else
        echo "[$(date)] SNAPSHOT FAILED: ${label}" >> "$LOG"
        FAILURES=$((FAILURES + 1))
        FAILED_NAMES="${FAILED_NAMES} ${label}"
    fi
}

echo "[$(date)] Starting kopia snapshots..." >> "$LOG"

# Retention policy (apply once; safe to re-apply)
echo "[$(date)] Applying retention policy..." >> "$LOG"
sudo $KOPIA --config-file=$CFG policy set /home/rohit \
  --keep-latest=30 --keep-hourly=24 --keep-daily=30 --keep-weekly=12 --keep-monthly=12 2>> "$LOG" || true

# Ignore policy for agentharness (heavy dirs)
echo "[$(date)] Setting ignore policy for agentharness..." >> "$LOG"
sudo $KOPIA --config-file=$CFG policy set /home/rohit/agentharness \
  --add-ignore=venv --add-ignore=.cache --add-ignore=__pycache__ 2>> "$LOG" || true

snapshot_one "compose files"   /home/rohit/services          kind:compose
snapshot_one "hermes config"   /home/rohit/.hermes             kind:hermes
snapshot_one "agentharness"    /home/rohit/agentharness        kind:agentharness
snapshot_one "homepage config" /home/rohit/services/homepage   kind:homepage

# Delete snapshots superseded beyond retention, then compact
echo "[$(date)] Pruning old snapshots..." >> "$LOG"
sudo $KOPIA --config-file=$CFG snapshot prune 2>> "$LOG" || \
  sudo $KOPIA --config-file=$CFG maintenance run --full 2>> "$LOG" || true

echo "[$(date)] Kopia snapshots complete" >> "$LOG"

# Alert if anything failed
if [ "$FAILURES" -gt 0 ]; then
    echo "[$(date)] KOPIA FAILURES (${FAILURES}):${FAILED_NAMES}" >> "$LOG"
    alert_telegram "⚠️ Kopia backup failed for ${FAILURES} source(s):${FAILED_NAMES}"
    exit 1
else
    echo "[$(date)] Kopia backup: all snapshots OK" >> "$LOG"
fi