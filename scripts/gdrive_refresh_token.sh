#!/usr/bin/env bash
# gdrive_refresh_token.sh — Proactively refresh GDrive OAuth token
# Run weekly via cron to prevent token expiration
# This uses rclone's built-in token refresh if a refresh_token exists

set -euo pipefail

LOG="[$(date '+%Y-%m-%d %H:%M:%S')] gdrive_refresh"
RCONF="$HOME/.config/rclone/rclone.conf"

# Check if gdrive remote exists
if ! rclone listremotes 2>/dev/null | grep -q "^gdrive:"; then
    echo "$LOG ERROR: gdrive remote not found in rclone config"
    exit 1
fi

# Try a lightweight operation to test token validity
if rclone lsd gdrive: --max-depth 1 --max-files 1 >/dev/null 2>&1; then
    echo "$LOG OK: GDrive token is valid"
    exit 0
fi

echo "$LOG WARN: GDrive token appears expired, attempting refresh..."

# Try backend token refresh
# rclone will attempt to use the refresh_token if it exists in the config
if rclone about gdrive: >/dev/null 2>&1; then
    echo "$LOG OK: Token refreshed successfully"
    exit 0
fi

# If we get here, the token is truly expired and can't be refreshed
# This means the original auth didn't include access_type=offline
echo "$LOG ERROR: Cannot refresh token automatically."
echo "$LOG ERROR: Manual re-authentication required."
echo "$LOG ERROR: Run: bash ~/agentharness/scripts/gdrive_setup.sh"
exit 1
