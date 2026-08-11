#!/usr/bin/env bash
# =============================================================================
# watch_compose_changes.sh — Watch compose dirs for file changes and trigger
# post_compose_change.sh to keep all dependent systems in sync.
#
# Runs as a daemon (started via @reboot cron). Exits gracefully on signal.
# =============================================================================
set -uo pipefail

LOG=/home/rohit/agentharness/logs/watch_compose_changes.log
COMPOSE_DIR=/home/rohit/services/docker/compose
HOOK=/home/rohit/agentharness/scripts/post_compose_change.sh

mkdir -p "$(dirname "$LOG")"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] watch_compose_changes started (watching $COMPOSE_DIR)" >> "$LOG"

# Debounce: coalesce rapid bursts of events (e.g. bulk edits) into one run.
trap 'echo "[$(date "+%Y-%m-%d %H:%M:%S")] watch_compose_changes stopping" >> "$LOG"; exit 0' TERM INT

inotifywait -m -e create -e moved_to -e delete -e modify -e attrib \
    --format '%e %w%f' \
    "$COMPOSE_DIR" \
    --exclude '\.swp$|~$' \
    2>> "$LOG" | while read -r _event _file; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] compose file changed: ${_file}" >> "$LOG"
    # Debounce: wait for 10s of quiescence
    sleep 10
    "$HOOK" >> "$LOG" 2>&1 || echo "[$(date '+%Y-%m-%d %H:%M:%S')] hook FAILED" >> "$LOG"
done
