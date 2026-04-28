#!/usr/bin/env bash
# =============================================================================
# alert.sh — File-based alert sender
#
# Writes alerts to alerts_inbox.jsonl for the agent (Chaguli/OpenClaw) to
# consume and deliver via its own channels (Telegram, email, etc.).
#
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
echo "  Approval ID: $approval_id"
echo "  Actions JSON: $actions_json"

[ -z "${message}" ] && exit 0

# Write alert via the Python alert sender
python3 -c "
import sys
sys.path.insert(0, '$(dirname "$SCRIPT_DIR")')
from core.alerts.sender import get_alert_sender
import json
sender = get_alert_sender()
actions = json.loads('''${actions_json}''')
sender.send('${severity,,}', '''${message}''', source='${source_name}', requires_approval=bool('${approval_id}'), approval_id='${approval_id}', actions=actions)
" 2>/dev/null || {
    # Fallback: append directly to JSONL if Python fails
    local alert_file="${AH_DATA_DIR}/alerts_inbox.jsonl"
    ensure_dir "${AH_DATA_DIR}"
    echo "{\"severity\":\"${severity,,}\",\"message\":\"${message}\",\"source\":\"${source_name}\",\"timestamp\":\"$(date -Iseconds)\",\"delivered\":false, \"requires_approval\":${approval_id:+true}, \"approval_id\":\"${approval_id}\", \"actions\":${actions_json}}" >> "${alert_file}" 2>/dev/null
}
