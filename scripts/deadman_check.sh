#!/usr/bin/env bash
# =============================================================================
# deadman_check.sh — Dead man's switch for AgentHarness scheduler
#
# Checks the heartbeat file written by the scheduler every 15 minutes.
# If the heartbeat is stale, attempts restart then escalates to alert.
#
# This script runs via cron, NOT via the scheduler itself.
# Cron example (every 10 minutes):
#   */10 * * * * /home/rohit/agentharness/scripts/deadman_check.sh
#
# Dependencies: bash, date, grep, sed — no Python required.
# =============================================================================

set -euo pipefail

# --- Config ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HEARTBEAT_FILE="$BASE_DIR/data/heartbeat.json"
ALERT_SCRIPT="$BASE_DIR/scripts/alert.sh"
LOCKFILE="/tmp/deadman_restart_attempted.lock"

STALE_THRESHOLD=1800   # 30 minutes — trigger restart
ALERT_THRESHOLD=3600   # 60 minutes — escalate to alert
SERVICE_NAME="agentharness-scheduler"

# --- Functions ---

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] deadman_check: $*"
}

die() {
    log "FATAL: $*"
    exit 1
}

get_heartbeat_epoch() {
    # Extract the "timestamp" value from heartbeat.json using pure text tools.
    # Format: "timestamp": 1712345678.123
    local ts
    ts=$(grep -o '"timestamp"[[:space:]]*:[[:space:]]*[0-9.]*' "$HEARTBEAT_FILE" \
         | sed 's/.*:[[:space:]]*//' \
         | sed 's/\..*//')
    if [ -z "$ts" ]; then
        return 1
    fi
    echo "$ts"
}

send_alert() {
    local subject="$1"
    local body="$2"
    if [ -x "$ALERT_SCRIPT" ]; then
        "$ALERT_SCRIPT" "$subject" "$body"
        log "Alert sent: $subject"
    else
        log "WARNING: alert.sh not found or not executable at $ALERT_SCRIPT"
        log "ALERT (undelivered): $subject — $body"
    fi
}

# --- Main ---

# Heartbeat file must exist
if [ ! -f "$HEARTBEAT_FILE" ]; then
    send_alert "Scheduler heartbeat MISSING" \
        "Heartbeat file does not exist at $HEARTBEAT_FILE. Scheduler may have never started."
    exit 1
fi

# Parse heartbeat timestamp
HB_EPOCH=$(get_heartbeat_epoch) || die "Could not parse timestamp from $HEARTBEAT_FILE"
NOW_EPOCH=$(date +%s)
AGE=$(( NOW_EPOCH - HB_EPOCH ))

log "Heartbeat age: ${AGE}s (threshold: restart=${STALE_THRESHOLD}s, alert=${ALERT_THRESHOLD}s)"

# --- Healthy ---
if [ "$AGE" -le "$STALE_THRESHOLD" ]; then
    # Heartbeat is fresh — clean up any previous restart lock
    rm -f "$LOCKFILE"
    exit 0
fi

# --- Stale: attempt restart ---
if [ "$AGE" -gt "$STALE_THRESHOLD" ] && [ "$AGE" -le "$ALERT_THRESHOLD" ]; then
    if [ -f "$LOCKFILE" ]; then
        log "Restart already attempted (lock exists). Waiting for recovery or escalation."
        exit 0
    fi

    log "Heartbeat stale (${AGE}s). Attempting systemctl restart $SERVICE_NAME..."
    if sudo /usr/bin/systemctl restart "$SERVICE_NAME" 2>&1; then
        log "Restart command succeeded. Will verify on next check."
    else
        log "Restart command failed."
    fi

    # Write lock so we don't spam restarts every cron tick
    date +%s > "$LOCKFILE"
    exit 0
fi

# --- Critical: stale beyond alert threshold ---
if [ "$AGE" -gt "$ALERT_THRESHOLD" ]; then
    send_alert "Scheduler DEAD — heartbeat ${AGE}s stale" \
        "Heartbeat last updated $(date -d "@$HB_EPOCH" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -r "$HB_EPOCH" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "epoch $HB_EPOCH"). Restart was attempted but scheduler did not recover. Manual intervention required."
    # Clean the lock so a future restart can be attempted if someone fixes the root cause
    rm -f "$LOCKFILE"
    exit 1
fi
