#!/usr/bin/env bash
# Check if system clock is within 120 seconds of an NTP server
# Exit 0 if OK, exit 1 if drifted
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Try timedatectl first (systemd)
if command -v timedatectl &>/dev/null; then
    synced=$(timedatectl show -p NTPSynchronized --value 2>/dev/null)
    if [ "$synced" = "yes" ]; then
        echo "NTP synchronized"
        exit 0
    fi
fi

# Fallback: compare against a known HTTP server's Date header
remote_time=$(curl -sI https://google.com 2>/dev/null | grep -i "^date:" | cut -d' ' -f2-)
if [ -z "$remote_time" ]; then
    echo "Cannot check time — no internet"
    exit 0  # Don't alert if offline
fi

remote_epoch=$(date -d "$remote_time" +%s 2>/dev/null)
local_epoch=$(date +%s)
drift=$((local_epoch - remote_epoch))
abs_drift=${drift#-}

if [ "$abs_drift" -gt 120 ]; then
    echo "Time drift: ${drift}s (threshold: 120s)"
    exit 1
fi

echo "Time OK (drift: ${drift}s)"
exit 0
