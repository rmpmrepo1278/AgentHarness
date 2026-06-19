#!/usr/bin/env bash
set -euo pipefail

# Healthchecks ping
source /home/rohit/.hermes/scripts/hc_uuids.sh 2>/dev/null || true
/home/rohit/.hermes/scripts/hc_ping.sh "$HC_UUID_VERIFY_BACKUPS" start 2>/dev/null || true

BACKUP_DIR="/mnt/usb/backups/docker-volumes"
SENTINEL_LOG="/home/rohit/.hermes/hermes-agent/data/sentinel_findings.jsonl"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

latest=$(ls -1td "$BACKUP_DIR"/*/ 2>/dev/null | head -1)
if [[ -z "$latest" ]]; then
    echo "{\"timestamp\":\"$TIMESTAMP\",\"source\":\"verify_backups\",\"label\":\"backup_verify_skip\",\"severity\":\"info\",\"description\":\"No backup directories found\"}" >> "$SENTINEL_LOG"
    exit 0
fi

archive=$(find "$latest" -name '*.tar.gz' -o -name '*.tar' 2>/dev/null | head -1)
if [[ -z "$archive" ]]; then
    echo "{\"timestamp\":\"$TIMESTAMP\",\"source\":\"verify_backups\",\"label\":\"backup_verify_skip\",\"severity\":\"info\",\"description\":\"No archive file in $latest\"}" >> "$SENTINEL_LOG"
    exit 0
fi

if tar tf "$archive" >/dev/null 2>&1; then
    echo "{\"timestamp\":\"$TIMESTAMP\",\"source\":\"verify_backups\",\"label\":\"backup_verify_pass\",\"severity\":\"info\",\"description\":\"Integrity OK: $archive\"}" >> "$SENTINEL_LOG"
    echo "PASS: $archive"
    /home/rohit/.hermes/scripts/hc_ping.sh "$HC_UUID_VERIFY_BACKUPS" success 2>/dev/null || true
else
    echo "{\"timestamp\":\"$TIMESTAMP\",\"source\":\"verify_backups\",\"label\":\"backup_verify_fail\",\"severity\":\"critical\",\"description\":\"Corrupt backup: $archive\",\"auto_fix_hint\":\"manual_restore_from_previous\"}" >> "$SENTINEL_LOG"
    echo "FAIL: $archive"
    /home/rohit/.hermes/scripts/hc_ping.sh "$HC_UUID_VERIFY_BACKUPS" fail 2>/dev/null || true
    exit 1
fi
