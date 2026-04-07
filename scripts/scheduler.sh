#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# scheduler.sh — Network-aware task scheduler
#
# Knows when wifi is up vs. down. Routes tasks to the right time window.
# Runs as a systemd service or cron every 15 minutes.
#
# OFFLINE (11 PM - 7:15 AM PT): benchmarks, cleanup, log analysis, local tasks
# ONLINE  (7:15 AM - 11 PM PT): downloads, web searches, git pulls, updates
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

SCHEDULER_STATE="${AH_DATA_DIR}/scheduler_state.json"
TASK_QUEUE="${AH_DATA_DIR}/task_queue.json"
LOG_FILE="${AH_LOGS_DIR}/scheduler.log"

# Network schedule (PT timezone)
# These can be overridden in .env
OFFLINE_START_HOUR="${OFFLINE_START_HOUR:-23}"    # 11 PM PT
ONLINE_START_HOUR="${ONLINE_START_HOUR:-7}"       # 7 AM PT (conservative)
TIMEZONE="America/Los_Angeles"

# Load Chaguli paths
[ -f "${AH_DATA_DIR}/chaguli_paths.env" ] && source "${AH_DATA_DIR}/chaguli_paths.env"

# -----------------------------------------------------------------------------
# Determine current network state
# -----------------------------------------------------------------------------
get_network_state() {
    local current_hour
    current_hour=$(TZ="${TIMEZONE}" date +%H | sed 's/^0//')

    # Check actual connectivity (more reliable than clock)
    local has_internet=false
    if ping -c 1 -W 3 8.8.8.8 &>/dev/null || ping -c 1 -W 3 1.1.1.1 &>/dev/null; then
        has_internet=true
    fi

    # Check if we're in the expected offline window
    local expected_offline=false
    if [ "${current_hour}" -ge "${OFFLINE_START_HOUR}" ] || [ "${current_hour}" -lt "${ONLINE_START_HOUR}" ]; then
        expected_offline=true
    fi

    # Check ethernet link to mini PC (if configured)
    local has_ethernet=false
    local minipc_ip="${MINIPC_IP:-}"
    if [ -n "${minipc_ip}" ] && ping -c 1 -W 2 "${minipc_ip}" &>/dev/null; then
        has_ethernet=true
    fi

    # Return state
    if [ "${has_internet}" = true ]; then
        echo "online"
    elif [ "${has_ethernet}" = true ]; then
        echo "lan_only"  # No internet but can reach mini PC
    else
        echo "offline"
    fi
}

# -----------------------------------------------------------------------------
# Get current window type
# -----------------------------------------------------------------------------
get_window() {
    local state
    state=$(get_network_state)

    case "${state}" in
        online)   echo "online" ;;
        lan_only) echo "offline_lan" ;;  # Can do local + cross-machine tasks
        offline)  echo "offline" ;;
    esac
}

# -----------------------------------------------------------------------------
# Task definitions — what runs in each window
# -----------------------------------------------------------------------------

run_offline_tasks() {
    log_info "[OFFLINE WINDOW] Running local tasks..."

    local tasks_run=0

    # 1. Benchmarks (heavy, no internet needed)
    local last_bench
    last_bench=$(stat -c %Y "${AH_DATA_DIR}/benchmark_results.json" 2>/dev/null || echo "0")
    local now
    now=$(date +%s)
    local bench_age=$(( (now - last_bench) / 86400 ))

    if [ "${bench_age}" -gt 7 ]; then
        log_info "Benchmarks are ${bench_age} days old. Re-running..."
        bash "${SCRIPT_DIR}/benchmark.sh" >> "${LOG_FILE}" 2>&1 && ((tasks_run++)) || true
    fi

    # 2. System cleanup
    local last_cleanup
    last_cleanup=$(stat -c %Y "${AH_REPORTS_DIR}"/cleanup_*.md 2>/dev/null | sort -rn | head -1 || echo "0")
    local cleanup_age=$(( (now - last_cleanup) / 86400 ))

    if [ "${cleanup_age}" -gt 3 ]; then
        log_info "Running system cleanup (last: ${cleanup_age} days ago)..."
        bash "${SCRIPT_DIR}/cleanup.sh" >> "${LOG_FILE}" 2>&1 && ((tasks_run++)) || true
    fi

    # 3. Backup (nightly, during offline window)
    local last_backup
    last_backup=$(stat -c %Y "${AH_REPORTS_DIR}"/backup_*.md 2>/dev/null | sort -rn | head -1 || echo "0")
    local backup_age=$(( (now - last_backup) / 86400 ))
    if [ "${backup_age}" -gt 0 ]; then
        log_info "Running nightly backup..."
        bash "${SCRIPT_DIR}/backup.sh" >> "${LOG_FILE}" 2>&1 && ((tasks_run++)) || true
    fi

    # 6. Proactive health checks (every run)

    # 7. Security audit (weekly)
    local last_security
    last_security=$(stat -c %Y "${AH_REPORTS_DIR}"/security_*.md 2>/dev/null | sort -rn | head -1 || echo "0")
    local security_age=$(( (now - last_security) / 86400 ))
    if [ "${security_age}" -gt 7 ]; then
        log_info "Running weekly security audit..."
        bash "${SCRIPT_DIR}/security_audit.sh" >> "${LOG_FILE}" 2>&1 && ((tasks_run++)) || true
    fi

    # 8. Process any queued tasks that are local-only
    process_queue "offline"

    # 9. Run registry-defined checks and harnesses (pluggable)
    if [ -f "${SCRIPT_DIR}/registry_engine.py" ]; then
        log_info "Running registry-defined checks..."
        python3 "${SCRIPT_DIR}/registry_engine.py" run_checks --window offline >> "${LOG_FILE}" 2>&1 || true
        log_info "Running registry-defined harnesses..."
        python3 "${SCRIPT_DIR}/registry_engine.py" run_harnesses --window offline >> "${LOG_FILE}" 2>&1 || true
    fi

    log_ok "[OFFLINE] Completed ${tasks_run} tasks"
}

run_offline_lan_tasks() {
    log_info "[OFFLINE+LAN] Running local + cross-machine tasks..."

    # Everything from offline, plus:
    run_offline_tasks

    # Cross-machine tasks (mini PC reachable via ethernet)
    if [ -n "${MINIPC_IP:-}" ]; then
        log_info "Mini PC reachable. Running cross-machine tasks..."
        # Future: distributed benchmarks, model sync, etc.
    fi
}

run_online_tasks() {
    log_info "[ONLINE WINDOW] Running internet-dependent tasks..."

    local tasks_run=0

    # Chaguli handles its own briefings via briefings.py — we don't duplicate.

    # 1. Weekly optimization search (if due)
    local last_weekly
    last_weekly=$(stat -c %Y "${AH_REPORTS_DIR}"/weekly_*.md 2>/dev/null | sort -rn | head -1 || echo "0")
    local now
    now=$(date +%s)
    local weekly_age=$(( (now - last_weekly) / 86400 ))

    if [ "${weekly_age}" -gt 7 ]; then
        log_info "Weekly optimization scan is due (last: ${weekly_age} days ago)..."
        bash "${SCRIPT_DIR}/weekly_optimize.sh" >> "${LOG_FILE}" 2>&1 && ((tasks_run++)) || true
    fi

    # 2. Check for inference engine updates (git fetch)
    for dir in /opt/ik_llama /opt/llama.cpp; do
        if [ -d "${dir}/.git" ]; then
            cd "${dir}"
            local before
            before=$(git rev-parse HEAD)
            git fetch --quiet 2>/dev/null || true
            local behind
            behind=$(git rev-list --count HEAD..@{u} 2>/dev/null || echo "0")
            if [ "${behind}" -gt 0 ]; then
                log_info "$(basename ${dir}) has ${behind} new commit(s) upstream"
                # Queue rebuild for next offline window
                add_to_queue "rebuild_$(basename ${dir})" "offline" \
                    "cd ${dir} && git pull && cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DGGML_AVX2=ON -DLLAMA_CURL=ON && cmake --build build -j\$(nproc)"
            fi
        fi
    done

    # 3. Process any queued tasks that need internet
    process_queue "online"

    # 4. Process GitHub repo install requests
    process_github_queue

    # Run registry-defined checks and harnesses (pluggable)
    if [ -f "${SCRIPT_DIR}/registry_engine.py" ]; then
        python3 "${SCRIPT_DIR}/registry_engine.py" run_checks --window online >> "${LOG_FILE}" 2>&1 || true
        python3 "${SCRIPT_DIR}/registry_engine.py" run_harnesses --window online >> "${LOG_FILE}" 2>&1 || true
    fi

    log_ok "[ONLINE] Completed ${tasks_run} tasks"
}

# -----------------------------------------------------------------------------
# Task queue management
# -----------------------------------------------------------------------------
init_queue() {
    if [ ! -f "${TASK_QUEUE}" ]; then
        echo '[]' > "${TASK_QUEUE}"
    fi
}

add_to_queue() {
    local task_id="$1"
    local window="$2"   # "online", "offline", or "any"
    local command="$3"
    local priority="${4:-5}"  # 1=highest, 10=lowest

    init_queue

    python3 << PYEOF
import json
from datetime import datetime

queue = json.load(open("${TASK_QUEUE}"))

# Don't add duplicates
if not any(t['id'] == "${task_id}" and t['status'] == 'pending' for t in queue):
    queue.append({
        'id': '${task_id}',
        'window': '${window}',
        'command': '''${command}''',
        'priority': ${priority},
        'status': 'pending',
        'queued_at': datetime.now().isoformat(),
        'attempts': 0
    })
    json.dump(queue, open("${TASK_QUEUE}", 'w'), indent=2)
    print(f"Queued: ${task_id} (window: ${window})")
else:
    print(f"Already queued: ${task_id}")
PYEOF
}

process_queue() {
    local current_window="$1"
    init_queue

    python3 << PYEOF
import json, subprocess, os
from datetime import datetime

queue = json.load(open("${TASK_QUEUE}"))
current_window = "${current_window}"

# Find tasks for this window, sorted by priority
pending = [t for t in queue if t['status'] == 'pending' and t['window'] in (current_window, 'any')]
pending.sort(key=lambda x: x['priority'])

for task in pending[:5]:  # Max 5 tasks per run
    task['status'] = 'running'
    task['started_at'] = datetime.now().isoformat()
    task['attempts'] += 1
    json.dump(queue, open("${TASK_QUEUE}", 'w'), indent=2)

    print(f"Running queued task: {task['id']}")
    try:
        result = subprocess.run(
            task['command'], shell=True,
            capture_output=True, text=True, timeout=1800  # 30 min max
        )
        if result.returncode == 0:
            task['status'] = 'completed'
            task['completed_at'] = datetime.now().isoformat()
            print(f"  Completed: {task['id']}")
        else:
            task['status'] = 'failed' if task['attempts'] >= 3 else 'pending'
            task['error'] = result.stderr[:500]
            print(f"  Failed: {task['id']} - {result.stderr[:100]}")
    except subprocess.TimeoutExpired:
        task['status'] = 'pending'  # Retry next time
        task['error'] = 'Timed out after 30 minutes'
        print(f"  Timed out: {task['id']}")

json.dump(queue, open("${TASK_QUEUE}", 'w'), indent=2)

# Purge completed tasks older than 7 days
cutoff = datetime.now().timestamp() - 7 * 86400
queue = [t for t in queue if not (t['status'] == 'completed' and
    datetime.fromisoformat(t.get('completed_at', datetime.now().isoformat())).timestamp() < cutoff)]
json.dump(queue, open("${TASK_QUEUE}", 'w'), indent=2)
PYEOF
}

# -----------------------------------------------------------------------------
# GitHub repo install queue
# -----------------------------------------------------------------------------
process_github_queue() {
    local github_queue="${AH_DATA_DIR}/github_queue.json"
    [ -f "${github_queue}" ] || return 0

    local pending
    pending=$(python3 -c "
import json
q = json.load(open('${github_queue}'))
pending = [r for r in q if r.get('status') == 'pending']
print(len(pending))
" 2>/dev/null || echo "0")

    if [ "${pending}" -gt 0 ]; then
        log_info "Processing ${pending} GitHub repo install request(s)..."
        bash "${SCRIPT_DIR}/github_deploy.sh" >> "${LOG_FILE}" 2>&1 || true
    fi
}

# -----------------------------------------------------------------------------
# Update scheduler state
# -----------------------------------------------------------------------------
update_state() {
    local window="$1"
    local network_state="$2"

    cat > "${SCHEDULER_STATE}" << EOF
{
    "last_run": "$(date -Iseconds)",
    "window": "${window}",
    "network_state": "${network_state}",
    "timezone": "${TIMEZONE}",
    "offline_start": "${OFFLINE_START_HOUR}:00",
    "online_start": "${ONLINE_START_HOUR}:00"
}
EOF
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
main() {
    ensure_dir "${AH_LOGS_DIR}"
    init_queue

    local network_state
    network_state=$(get_network_state)
    local window
    window=$(get_window)

    local ts
    ts=$(TZ="${TIMEZONE}" date '+%Y-%m-%d %H:%M %Z')
    log_info "Scheduler run: ${ts} | Network: ${network_state} | Window: ${window}"

    case "${window}" in
        online)
            run_online_tasks
            ;;
        offline_lan)
            run_offline_lan_tasks
            ;;
        offline)
            run_offline_tasks
            ;;
    esac

    update_state "${window}" "${network_state}"
}

main "$@"
