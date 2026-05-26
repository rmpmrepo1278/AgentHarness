#!/bin/bash
# Inbox Watcher — inotifywait-based dispatch monitor
# Replaces the polling inbox_watcher.py with event-driven filesystem notifications
# Watches shared_agent_memory/dispatch for new task files

DISPATCH_DIR="/home/rohit/shared_agent_memory/dispatch"
VENV_PYTHON="/home/rohit/agentharness/venv/bin/python3"
WATCHER_SCRIPT="/home/rohit/agentharness/core/agents/inbox_watcher.py"
LOG="/home/rohit/agentharness/data/logs/inbox_watcher.log"

mkdir -p "$DISPATCH_DIR" "$(dirname "$LOG")"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [inotify] $1" >> "$LOG"
}

log "Starting inotifywait watcher on $DISPATCH_DIR"

# Run initial catch-up for any files that arrived while we were down
if [ -f "$WATCHER_SCRIPT" ]; then
    . /home/rohit/agentharness/data/.env 2>/dev/null
    "$VENV_PYTHON" "$WATCHER_SCRIPT" --inbox-dir /home/rohit/agentharness/data --once >> "$LOG" 2>&1
fi

# Event-driven: watch for new files in dispatch dir
inotifywait -m -r -e create,moved_to --format '%w%f' "$DISPATCH_DIR" 2>/dev/null | \
while read -r filepath; do
    # Debounce: small delay to let writers finish
    sleep 1

    # Skip non-JSON/task files
    case "$filepath" in
        *.json|*.jsonl|*.txt) ;;
        *) continue ;;
    esac

    log "New file detected: $filepath"

    # Process via existing watcher (keeps all injection logic)
    if [ -f "$WATCHER_SCRIPT" ]; then
        . /home/rohit/agentharness/data/.env 2>/dev/null
        "$VENV_PYTHON" "$WATCHER_SCRIPT" --inbox-dir /home/rohit/agentharness/data --once >> "$LOG" 2>&1
        log "Processed: $filepath"
    fi
done
