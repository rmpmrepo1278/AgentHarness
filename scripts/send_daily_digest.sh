#!/usr/bin/env bash
# Send daily doctor digest via Chaguli inbox
# Add to cron: 0 8 * * * /home/rohit/agentharness/scripts/send_daily_digest.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"
source "${PROJECT_DIR}/venv/bin/activate" 2>/dev/null || true

python3 -c "
from core.doctor.notify import NotificationRouter
import os

data_dir = os.environ.get('AH_DATA_DIR', '$PROJECT_DIR/data')
inbox_dir = os.path.expanduser('~/agentharness/data/insights_inbox')

router = NotificationRouter(
    data_dir=data_dir,
    chaguli_inbox_dir=inbox_dir,
    alert_script='$SCRIPT_DIR/alert.sh',
)
sent = router.send_digest()
print('Digest sent' if sent else 'Nothing to report')
"
