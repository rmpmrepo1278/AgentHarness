#!/usr/bin/env bash
# =============================================================================
# alert.sh — File-based alert sender
#
# Writes alerts to alerts_inbox.jsonl for the agent to consume and deliver.
# AgentHarness does NOT talk to Telegram directly.
#
# Usage: alert.sh SEVERITY "message" [SOURCE]
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

severity="${1:-INFO}"
message="${2:-}"
source_name="${3:-bash_script}"
approval_id="${4:-}"
actions_json="${5:-[]}"

echo "alert.sh received:"
echo "  Severity: $severity"
echo "  Message: $message"
echo "  Source: $source_name"

[ -z "${message}" ] && exit 0

# Write alert via the Python alert sender
ALERT_PAYLOAD=$(python3 -c "
import json, sys
severity = sys.argv[1]
message = sys.argv[2]
source = sys.argv[3]
approval = sys.argv[4]
actions = sys.argv[5]

# Validate actions is valid JSON
try:
    actions_list = json.loads(actions)
except Exception:
    actions_list = []

payload = {
    'severity': severity.lower(),
    'message': message,
    'source': source,
    'timestamp': __import__('datetime').datetime.now().isoformat(),
    'delivered': False,
    'requires_approval': bool(approval),
    'approval_id': approval,
    'actions': actions_list
}
print(json.dumps(payload))
" "$severity" "$message" "$source_name" "$approval_id" "$actions_json" 2>/dev/null)

if [ -n "$ALERT_PAYLOAD" ]; then
    # Try Python sender first
    SENDER_RESULT=$(python3 -c "
import sys
sys.path.insert(0, '$(dirname "$SCRIPT_DIR")')
try:
    from core.alerts.sender import get_alert_sender
    import json
    payload = json.loads(sys.argv[1])
    sender = get_alert_sender()
    sender.send(
        payload['severity'], payload['message'],
        source=payload['source'],
        requires_approval=payload['requires_approval'],
        approval_id=payload['approval_id'],
        actions=payload['actions']
    )
    print('SENT')
except Exception as e:
    print(f'ERROR:{e}')
" "$ALERT_PAYLOAD" 2>/dev/null)

    if [ "$SENDER_RESULT" = "SENT" ]; then
        echo "Alert sent via Python sender"
        exit 0
    fi
    echo "Python sender failed ($SENDER_RESULT), falling back to JSONL"
fi

# Fallback: append directly to JSONL
alert_file="${AH_DATA_DIR}/alerts_inbox.jsonl"
ensure_dir "${AH_DATA_DIR}"
echo "$ALERT_PAYLOAD" >> "$alert_file" 2>/dev/null || {
    echo "ERROR: Could not write alert to $alert_file" >&2
    exit 1
}
echo "Alert written to $alert_file"
