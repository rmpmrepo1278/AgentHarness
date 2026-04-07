#!/usr/bin/env bash
# =============================================================================
# alert.sh — Lightweight alert sender (replaces monitor.sh alert functionality)
#
# Usage: alert.sh SEVERITY "message"
#        alert.sh flush  (send queued alerts)
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f /opt/agentharness/.env ] && source /opt/agentharness/.env

ALERT_QUEUE="/opt/agentharness/alert_queue.json"

case "${1:-}" in
    flush)
        [ -f "${ALERT_QUEUE}" ] || exit 0
        python3 -c "
import json, subprocess
queue = json.load(open('${ALERT_QUEUE}'))
for a in queue:
    if not a.get('sent') and '${TELEGRAM_BOT_TOKEN:-}' and '${TELEGRAM_CHAT_ID:-}':
        try:
            subprocess.run(['curl','-sf','--max-time','5',
                'https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage',
                '-d','chat_id=${TELEGRAM_CHAT_ID}',
                '-d',f'text=[{a[\"severity\"]}] {a[\"message\"]}'],
                capture_output=True,timeout=10)
            a['sent']=True
        except: pass
json.dump(queue, open('${ALERT_QUEUE}','w'), indent=2)
" 2>/dev/null
        ;;
    *)
        local severity="${1:-INFO}"
        local message="${2:-}"
        [ -z "${message}" ] && exit 0

        # Try sending immediately
        if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
            curl -sf --max-time 5 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
                -d "chat_id=${TELEGRAM_CHAT_ID}" \
                -d "text=[${severity}] ${message}" &>/dev/null && exit 0
        fi

        # Queue if offline
        mkdir -p /opt/agentharness
        [ -f "${ALERT_QUEUE}" ] || echo '[]' > "${ALERT_QUEUE}"
        python3 -c "
import json
from datetime import datetime
q = json.load(open('${ALERT_QUEUE}'))
q.append({'severity':'${severity}','message':'''${message}''','queued_at':datetime.now().isoformat(),'sent':False})
json.dump(q, open('${ALERT_QUEUE}','w'), indent=2)
" 2>/dev/null
        ;;
esac
