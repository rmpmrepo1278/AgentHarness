#!/bin/bash
# Kopia snapshot wrapper using kopia CLI
set -euo pipefail

LOG=/home/rohit/agentharness/logs/kopia_backup.log
KOPIA=/usr/local/bin/kopia

echo "[$(date)] Starting kopia snapshots..." >> "$LOG"

# Snapshot compose files
echo "[$(date)] Snapshotting compose files..." >> "$LOG"
$KOPIA snapshot create /home/rohit/services --tags compose,config 2>> "$LOG" || echo "[$(date)] Compose snapshot failed" >> "$LOG"

# Snapshot hermes config
echo "[$(date)] Snapshotting hermes config..." >> "$LOG"
$KOPIA snapshot create /home/rohit/.hermes --tags hermes,config 2>> "$LOG" || echo "[$(date)] Hermes config snapshot failed" >> "$LOG"

# Snapshot agentharness configs
echo "[$(date)] Snapshotting agentharness configs..." >> "$LOG"
$KOPIA snapshot create /home/rohit/agentharness --tags agentharness,config --exclude-dir=venv --exclude-dir=.cache --exclude-dir=__pycache__ 2>> "$LOG" || echo "[$(date)] Agentharness snapshot failed" >> "$LOG"

# Snapshot homepage config
echo "[$(date)] Snapshotting homepage config..." >> "$LOG"
$KOPIA snapshot create /home/rohit/services/homepage --tags homepage 2>> "$LOG" || echo "[$(date)] Homepage snapshot failed" >> "$LOG"

# Cleanup old snapshots (keep last 30 days)
echo "[$(date)] Cleaning old snapshots..." >> "$LOG"
$KOPIA snapshot prune --keep-last 30d 2>> "$LOG" || true

echo "[$(date)] Kopia snapshots complete" >> "$LOG"
