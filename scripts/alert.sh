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

[ -z "${message}" ] && exit 0

# Write alert via the Python alert sender
python3 -c "
import sys
sys.path.insert(0, '$(dirname "$SCRIPT_DIR")')
from core.alerts.sender import get_alert_sender
sender = get_alert_sender()
sender.send('${severity,,}', '''${message}''', source='${source_name}')
" 2>/dev/null || {
    # Fallback: append directly to JSONL if Python fails
    local alert_file="${AH_DATA_DIR}/alerts_inbox.jsonl"
    ensure_dir "${AH_DATA_DIR}"
    echo "{\"severity\":\"${severity,,}\",\"message\":\"${message}\",\"source\":\"${source_name}\",\"timestamp\":\"$(date -Iseconds)\",\"delivered\":false}" >> "${alert_file}" 2>/dev/null
}
