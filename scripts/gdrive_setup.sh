#!/usr/bin/env bash
# gdrive_setup.sh — One-time GDrive OAuth setup with offline access
# Run this to set up or re-authenticate GDrive with a persistent refresh token
#
# Usage:
#   GOOGLE_OAUTH_CLIENT_ID=xxx GOOGLE_OAUTH_CLIENT_SECRET=yyy bash gdrive_setup.sh
#
# Or set them in ~/.hermes/.env and run: bash gdrive_setup.sh

set -euo pipefail

# Load credentials from environment or .env file
if [ -z "${GOOGLE_OAUTH_CLIENT_ID:-}" ] || [ -z "${GOOGLE_OAUTH_CLIENT_SECRET:-}" ]; then
    for env_file in ~/.hermes/.env ~/.env; do
        if [ -f "$env_file" ]; then
            while IFS='=' read -r key val; do
                key=$(echo "$key" | tr -d '[:space:]')
                val=$(echo "$val" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | sed 's/^"//;s/"$//')
                case "$key" in
                    GOOGLE_OAUTH_CLIENT_ID) GOOGLE_OAUTH_CLIENT_ID="$val" ;;
                    GOOGLE_OAUTH_CLIENT_SECRET) GOOGLE_OAUTH_CLIENT_SECRET="$val" ;;
                esac
            done < <(grep -E '^(GOOGLE_OAUTH_CLIENT_ID|GOOGLE_OAUTH_CLIENT_SECRET)=' "$env_file" 2>/dev/null)
        fi
    done
fi

if [ -z "${GOOGLE_OAUTH_CLIENT_ID:-}" ] || [ -z "${GOOGLE_OAUTH_CLIENT_SECRET:-}" ]; then
    echo "ERROR: Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET."
    echo "       Add them to ~/.hermes/.env or pass as environment variables."
    exit 1
fi

CLIENT_ID="$GOOGLE_OAUTH_CLIENT_ID"
CLIENT_SECRET="$GOOGLE_OAUTH_CLIENT_SECRET"

echo "============================================"
echo "  GDrive OAuth Setup (Offline Access)"
echo "============================================"
echo ""
echo "Step 1: Open this URL in your browser:"
echo ""

AUTH_URL="https://accounts.google.com/o/oauth2/v2/auth?client_id=${CLIENT_ID}&redirect_uri=urn:ietf:wg:oauth:2.0:oob&response_type=code&scope=https://www.googleapis.com/auth/drive&access_type=offline&prompt=consent"

echo "$AUTH_URL"
echo ""
echo "Step 2: Sign in with your Google account and grant access."
echo ""
echo "Step 3: Copy the authorization code and paste it here:"
echo ""
read -p "Authorization code: " AUTH_CODE

if [ -z "$AUTH_CODE" ]; then
    echo "ERROR: No authorization code provided."
    exit 1
fi

echo ""
echo "Step 4: Exchanging code for tokens..."

TOKEN_RESPONSE=$(curl -s -X POST https://oauth2.googleapis.com/token \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "code=${AUTH_CODE}" \
    -d "client_id=${CLIENT_ID}" \
    -d "client_secret=${CLIENT_SECRET}" \
    -d "redirect_uri=urn:ietf:wg:oauth:2.0:oob" \
    -d "grant_type=authorization_code")

if echo "$TOKEN_RESPONSE" | grep -q '"error"'; then
    echo "ERROR: Token exchange failed:"
    echo "$TOKEN_RESPONSE"
    exit 1
fi

REFRESH_TOKEN=$(echo "$TOKEN_RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('refresh_token',''))")
ACCESS_TOKEN=$(echo "$TOKEN_RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('access_token',''))")
EXPIRES_IN=$(echo "$TOKEN_RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('expires_in',3599))")

if [ -z "$REFRESH_TOKEN" ]; then
    echo "ERROR: No refresh_token in response. The auth may not have included offline access."
    echo "Response: $TOKEN_RESPONSE"
    exit 1
fi

echo "✓ Got refresh_token (persistent)"
echo "✓ Got access_token (valid for ${EXPIRES_IN}s)"

TOKEN_JSON=$(python3 -c "
import json
token = {
    'access_token': '${ACCESS_TOKEN}',
    'refresh_token': '${REFRESH_TOKEN}',
    'token_type': 'Bearer',
    'expires_in': ${EXPIRES_IN},
    'refresh_token_expires_in': 588757
}
print(json.dumps(token))
")

rclone config update gdrive token="$TOKEN_JSON" 2>&1

echo ""
echo "Step 5: Testing connection..."
if rclone lsd gdrive: --max-depth 1 >/dev/null 2>&1; then
    echo "✓ GDrive connection successful!"
    echo ""
    echo "Setup complete. GDrive will now auto-refresh its token."
else
    echo "⚠ Token saved but connection test failed. You may need to wait a moment."
fi
