#!/usr/bin/env bash
# =============================================================================
# setup_onedrive.sh — One-time setup for Microsoft OneDrive sync
#
# Run this script once to authenticate with your Microsoft OneDrive account.
# It will open a browser (or give you a URL) for OAuth authorization.
#
# After setup, the daily sync will run automatically.
# =============================================================================

set -euo pipefail

echo "============================================="
echo "  Microsoft OneDrive Setup for Calibre Sync"
echo "============================================="
echo ""
echo "This will configure rclone to access your OneDrive account."
echo "You'll need to authenticate with your Microsoft account (nickynrohit@live.com)."
echo ""

# Remove old msonedrive remote if it exists
rclone config delete msonedrive 2>/dev/null || true

# Create new Microsoft OneDrive remote
echo "Creating Microsoft OneDrive remote 'msonedrive'..."
echo ""
echo "A browser window will open for you to sign in to your Microsoft account."
echo "After signing in, paste the authorization token back here."
echo ""

if command -v xdg-open &>/dev/null; then
    # Desktop environment available - will open browser
    rclone config create msonedrive onedrive
else
    # Headless - use authorize method
    echo "No browser available. Using authorize method..."
    AUTH_URL=$(rclone authorize "onedrive" 2>&1 | grep -oP 'https://[^\s]+' | head -1)
    if [ -n "$AUTH_URL" ]; then
        echo ""
        echo "Open this URL in your browser:"
        echo "$AUTH_URL"
        echo ""
        echo "After authorizing, paste the token code here:"
        read -r AUTH_TOKEN
        # Create remote with the token
        rclone config create msonedrive onedrive token "$AUTH_TOKEN" 2>/dev/null || {
            echo "Failed to create remote with token. Trying interactive mode..."
            rclone config create msonedrive onedrive
        }
    else
        echo "Falling back to interactive configuration..."
        rclone config create msonedrive onedrive
    fi
echo ""
echo "Testing connection..."
if rclone lsd msonedrive:/ 2>/dev/null; then
    echo "✓ OneDrive connection successful!"
    echo ""
    echo "Creating eBooks folder if it doesn't exist..."
    rclone mkdir msonedrive:/Nicky/eBooks 2>/dev/null || true
    echo "✓ Setup complete!"
    echo ""
    echo "You can now run the daily sync:"
    echo "  bash /home/rohit/agentharness/scripts/sync_calibre_to_onedrive.sh"
else
    echo "✗ Connection failed. Please check your credentials and try again."
    exit 1
fi
