#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# daily_improve.sh — Analyze OpenClaw / Telegram interactions daily
#                    Identify failures, slow responses, missed intents
#                    Auto-fix issues or queue improvement tasks
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

REPORT_DIR="/opt/agentharness/reports"
IMPROVEMENTS_DIR="/opt/agentharness/improvements"
LLM_URL="${LLM_PRIMARY_URL:-http://localhost:8080}"
DAILY_REPORT="/opt/agentharness/reports/daily_$(timestamp).md"

# Load environment
[ -f /opt/agentharness/.env ] && source /opt/agentharness/.env
[ -f /opt/agentharness/best_config.env ] && source /opt/agentharness/best_config.env

# OpenClaw paths — discovered, not hardcoded
[ -f /opt/agentharness/openclaw_paths.env ] && source /opt/agentharness/openclaw_paths.env
OPENCLAW_HOME="${OPENCLAW_HOME:-}"
OPENCLAW_LOGS_DIR="${OPENCLAW_HOME:+${OPENCLAW_HOME}/logs}"

# =============================================================================
# PHASE 1: Collect interaction data
# =============================================================================

# -----------------------------------------------------------------------------
# Extract recent interactions from OpenClaw session history
# -----------------------------------------------------------------------------
extract_openclaw_sessions() {
    local hours_back="${1:-24}"

    log_info "Extracting OpenClaw sessions from last ${hours_back} hours..."

    local output_file="/opt/agentharness/daily_conversations.json"

    # Method 1: Use openclaw CLI if available
    if command -v openclaw &>/dev/null; then
        openclaw sessions list --json 2>/dev/null | python3 -c "
import sys, json
from datetime import datetime, timedelta

cutoff = (datetime.now() - timedelta(hours=${hours_back})).isoformat()
try:
    sessions = json.load(sys.stdin)
    recent = [s for s in sessions if s.get('updatedAt', '') > cutoff or s.get('updated_at', '') > cutoff]

    conversations = []
    for s in recent[:50]:
        sid = s.get('id', s.get('sessionId', ''))
        conversations.append({
            'id': sid,
            'created': s.get('createdAt', s.get('created_at', '')),
            'updated': s.get('updatedAt', s.get('updated_at', '')),
            'channel': s.get('channel', 'unknown'),
            'message_count': s.get('messageCount', 0)
        })

    json.dump(conversations, open('${output_file}', 'w'), indent=2)
    print(f'Extracted {len(conversations)} sessions via CLI')
except Exception as e:
    print(f'CLI extraction failed: {e}')
    json.dump([], open('${output_file}', 'w'))
" 2>/dev/null && return 0
    fi

    # Method 2: Read OpenClaw log files directly
    local log_files=()
    for logdir in "${OPENCLAW_LOGS_DIR}" "${OPENCLAW_HOME}" /var/log/openclaw; do
        if [ -d "${logdir}" ]; then
            while IFS= read -r f; do
                log_files+=("$f")
            done < <(find "${logdir}" -name "*.log" -o -name "*.jsonl" -newer /tmp -type f 2>/dev/null | head -20)
        fi
    done

    # Method 3: Check OpenClaw Gateway WebSocket logs
    local gateway_log="${OPENCLAW_HOME}/gateway.log"
    [ -f "${gateway_log}" ] && log_files+=("${gateway_log}")

    if [ ${#log_files[@]} -gt 0 ]; then
        local combined="/opt/agentharness/daily_openclaw_logs.txt"
        > "${combined}"
        for f in "${log_files[@]}"; do
            # Get last 24h of logs
            local cutoff_ts
            cutoff_ts=$(date -d "-${hours_back} hours" +%s 2>/dev/null || date -v-${hours_back}H +%s 2>/dev/null || echo "0")
            awk -v cutoff="${cutoff_ts}" '{ print }' "${f}" >> "${combined}" 2>/dev/null || true
        done
        local line_count
        line_count=$(wc -l < "${combined}")
        log_info "Collected ${line_count} log lines from OpenClaw"
    fi

    # Method 4: Check Docker logs for openclaw container
    local openclaw_container
    openclaw_container=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -i "openclaw" | head -1 || true)
    if [ -n "${openclaw_container}" ]; then
        docker logs --since "${hours_back}h" "${openclaw_container}" 2>&1 | \
            grep -i "message\|session\|error\|tool\|exec\|telegram" \
            >> /opt/agentharness/daily_openclaw_logs.txt 2>/dev/null || true
        log_info "Collected Docker logs from ${openclaw_container}"
    fi

    # Create minimal conversations file if nothing found
    [ -f "${output_file}" ] || echo '[]' > "${output_file}"
}

# -----------------------------------------------------------------------------
# Extract Telegram bot interactions (if bot logs exist)
# -----------------------------------------------------------------------------
extract_telegram_logs() {
    log_info "Looking for Telegram bot logs..."

    local log_locations=(
        "/var/log/telegram-bot"
        "/opt/agentharness/logs/telegram"
    )

    # Check Docker logs for telegram and openclaw containers
    local telegram_containers
    telegram_containers=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -i "telegram\|openclaw" || true)

    if [ -n "${telegram_containers}" ]; then
        local combined_logs="/opt/agentharness/daily_telegram_logs.txt"
        > "${combined_logs}"

        while IFS= read -r container; do
            log_info "Extracting logs from container: ${container}"
            docker logs --since 24h "${container}" 2>&1 | \
                grep -i "telegram\|message\|chat\|error\|timeout\|failed" \
                >> "${combined_logs}" 2>/dev/null || true
        done <<< "${telegram_containers}"

        local line_count
        line_count=$(wc -l < "${combined_logs}")
        log_info "Extracted ${line_count} relevant log lines"
    fi
}

# =============================================================================
# PHASE 2: Analyze interactions
# =============================================================================

analyze_interactions() {
    log_info "Analyzing interactions with local LLM..."

    local conversations_file="/opt/agentharness/daily_conversations.json"
    local telegram_logs="/opt/agentharness/daily_telegram_logs.txt"

    if [ ! -f "${conversations_file}" ] && [ ! -f "${telegram_logs}" ]; then
        log_warn "No interaction data found to analyze"
        echo "## Analysis" >> "${DAILY_REPORT}"
        echo "No interaction data available for today." >> "${DAILY_REPORT}"
        return
    fi

    # Build context for LLM analysis
    local context=""

    if [ -f "${conversations_file}" ]; then
        # Summarize conversations (don't send entire history to LLM)
        context+=$(python3 << 'PYEOF'
import json

try:
    convos = json.load(open("/opt/agentharness/daily_conversations.json"))
    summary = []

    for c in convos[:20]:  # Limit to 20 conversations
        msgs = c.get('messages', [])
        msg_summary = []

        for m in msgs:
            role = m.get('role', 'unknown')
            content = m.get('content', '')
            if isinstance(content, str):
                # Truncate long messages
                content = content[:200] + ('...' if len(content) > 200 else '')
                msg_summary.append(f"{role}: {content}")

        if msg_summary:
            summary.append({
                'id': c['id'][:8],
                'messages': len(msgs),
                'preview': msg_summary[:6]  # First 6 messages
            })

    print(json.dumps(summary, indent=2))
except Exception as e:
    print(f"Error summarizing: {e}")
PYEOF
)
    fi

    if [ -f "${telegram_logs}" ]; then
        context+="\n\nTelegram logs (last 24h):\n"
        context+=$(tail -100 "${telegram_logs}" 2>/dev/null || echo "(no logs)")
    fi

    # Ask LLM to analyze
    local analysis_prompt="Analyze these user interactions from the last 24 hours and identify:

1. FAILURES: Any errors, timeouts, or failed requests. Quote the specific error.
2. SLOW RESPONSES: Any interaction that seemed to take too long or where the user had to repeat themselves.
3. MISSED INTENTS: Cases where the user asked for something and the assistant didn't understand or gave a wrong answer.
4. IMPROVEMENT OPPORTUNITIES: Patterns that suggest a new tool, automation, or system prompt improvement would help.
5. RECURRING REQUESTS: Things the user asks repeatedly that could be automated.

For each issue found, provide:
- Category (FAILURE/SLOW/MISSED/IMPROVEMENT/RECURRING)
- Severity (HIGH/MEDIUM/LOW)
- Description
- Suggested fix (be specific: what file to change, what tool to add, what prompt to modify)

If no issues found, say so.

Interaction data:
${context}"

    local analysis
    analysis=$(curl -sf --max-time 600 "${LLM_URL}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "
import json
prompt = '''${analysis_prompt}'''
print(json.dumps({
    'messages': [
        {'role': 'system', 'content': 'You are a system reliability engineer analyzing chatbot interactions to improve the user experience. Be specific and actionable.'},
        {'role': 'user', 'content': prompt}
    ],
    'max_tokens': 1000,
    'temperature': 0.2
}))
" 2>/dev/null)" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d['choices'][0]['message']['content'])
except:
    print('(LLM analysis unavailable)')
" 2>/dev/null || echo "(LLM analysis unavailable)")

    echo "## Interaction Analysis" >> "${DAILY_REPORT}"
    echo "" >> "${DAILY_REPORT}"
    echo "${analysis}" >> "${DAILY_REPORT}"
    echo "" >> "${DAILY_REPORT}"
}

# =============================================================================
# PHASE 3: Auto-fix and queue improvements
# =============================================================================

auto_fix_issues() {
    log_info "Checking for auto-fixable issues..."

    echo "## Auto-Fix Actions Taken" >> "${DAILY_REPORT}"
    echo "" >> "${DAILY_REPORT}"

    local actions_taken=0

    # --- Check 1: LLM server health ---
    if ! curl -sf http://localhost:8080/health &>/dev/null; then
        log_warn "Primary LLM server is down. Restarting..."
        sudo systemctl restart llama-primary 2>/dev/null && {
            echo "- Restarted llama-primary service (was down)" >> "${DAILY_REPORT}"
            ((actions_taken++))
        }
    fi

    # --- Check 2: SearXNG health ---
    if ! curl -sf "${SEARXNG_URL:-http://localhost:8888}/healthz" &>/dev/null 2>&1; then
        log_warn "SearXNG is down. Restarting..."
        (cd /opt/searxng && docker compose restart 2>/dev/null) && {
            echo "- Restarted SearXNG container (was down)" >> "${DAILY_REPORT}"
            ((actions_taken++))
        }
    fi

    # --- Check 3: Swap pressure (indicates model is too large) ---
    local swap_used_mb
    swap_used_mb=$(free -m | awk '/Swap/ {print $3}')
    if [ "${swap_used_mb}" -gt 500 ]; then
        echo "- WARNING: High swap usage (${swap_used_mb}MB). Consider using a smaller model quant." >> "${DAILY_REPORT}"
        ((actions_taken++))

        # If swap is extreme, auto-switch to fast (smaller) model
        if [ "${swap_used_mb}" -gt 2000 ]; then
            log_warn "Extreme swap (${swap_used_mb}MB). Switching to fast model to prevent system instability..."
            sudo systemctl stop llama-primary 2>/dev/null
            sudo systemctl start llama-fast 2>/dev/null && {
                echo "- AUTO-SWITCHED to fast model due to extreme swap pressure (${swap_used_mb}MB)" >> "${DAILY_REPORT}"
                ((actions_taken++))
            }
        fi
    fi

    # --- Check 4: Disk space ---
    local disk_pct
    disk_pct=$(df / | awk 'NR==2 {gsub(/%/,""); print $5}')
    if [ "${disk_pct}" -gt 90 ]; then
        # Clean up old reports
        local cleaned
        cleaned=$(find /opt/agentharness/reports -name "*.html" -mtime +30 -delete -print 2>/dev/null | wc -l)
        cleaned=$((cleaned + $(find /opt/agentharness/reports -name "*.md" -mtime +60 -delete -print 2>/dev/null | wc -l)))
        if [ "${cleaned}" -gt 0 ]; then
            echo "- Cleaned ${cleaned} old report files (disk at ${disk_pct}%)" >> "${DAILY_REPORT}"
            ((actions_taken++))
        fi

        # Clean Docker
        local docker_cleaned
        docker_cleaned=$(docker system prune -f 2>/dev/null | tail -1 || echo "0B")
        echo "- Docker prune reclaimed: ${docker_cleaned}" >> "${DAILY_REPORT}"
        ((actions_taken++))
    fi

    # --- Check 5: OpenClaw container/process health ---
    local openclaw_container
    openclaw_container=$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -i "openclaw" | head -1 || true)
    if [ -n "${openclaw_container}" ]; then
        local oc_status
        oc_status=$(docker inspect --format='{{.State.Status}}' "${openclaw_container}" 2>/dev/null || echo "unknown")
        if [ "${oc_status}" != "running" ]; then
            log_warn "OpenClaw container (${openclaw_container}) is ${oc_status}. Restarting..."
            docker restart "${openclaw_container}" 2>/dev/null && {
                echo "- Restarted OpenClaw container (was ${oc_status})" >> "${DAILY_REPORT}"
                ((actions_taken++))
            }
        fi
    elif command -v openclaw &>/dev/null; then
        # Check if OpenClaw Gateway process is running
        if ! pgrep -f "openclaw" &>/dev/null; then
            echo "- WARNING: OpenClaw Gateway process not running" >> "${DAILY_REPORT}"
            ((actions_taken++))
        fi
    fi

    # --- Check 6: All Docker containers health ---
    local unhealthy
    unhealthy=$(docker ps --filter "health=unhealthy" --format "{{.Names}}" 2>/dev/null || true)
    if [ -n "${unhealthy}" ]; then
        while IFS= read -r container; do
            log_warn "Unhealthy container: ${container}. Restarting..."
            docker restart "${container}" 2>/dev/null && {
                echo "- Restarted unhealthy container: ${container}" >> "${DAILY_REPORT}"
                ((actions_taken++))
            }
        done <<< "${unhealthy}"
    fi

    if [ "${actions_taken}" -eq 0 ]; then
        echo "- No auto-fix actions needed. All systems healthy." >> "${DAILY_REPORT}"
    fi

    echo "" >> "${DAILY_REPORT}"
}

# -----------------------------------------------------------------------------
# Queue improvement tasks (write to a file Chaguli can read)
# -----------------------------------------------------------------------------
queue_improvements() {
    log_info "Queuing improvement tasks..."

    ensure_dir "${IMPROVEMENTS_DIR}"

    local task_file="${IMPROVEMENTS_DIR}/tasks_$(date +%Y%m%d).json"

    # Ask LLM to extract structured improvement tasks from the report
    local report_content
    report_content=$(cat "${DAILY_REPORT}")

    local tasks_json
    tasks_json=$(curl -sf --max-time 300 "${LLM_URL}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "
import json
report = '''${report_content}'''
print(json.dumps({
    'messages': [
        {'role': 'system', 'content': 'Extract improvement tasks from this report as a JSON array. Each task: {\"title\": \"...\", \"severity\": \"HIGH|MEDIUM|LOW\", \"category\": \"FAILURE|SLOW|MISSED|IMPROVEMENT|RECURRING\", \"description\": \"...\", \"suggested_action\": \"...\", \"auto_fixable\": true|false}. Return ONLY valid JSON array, no other text.'},
        {'role': 'user', 'content': report}
    ],
    'max_tokens': 500,
    'temperature': 0.1
}))
" 2>/dev/null)" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    content = d['choices'][0]['message']['content'].strip()
    # Strip markdown code fences
    if content.startswith('\`\`\`'):
        content = content.split('\n', 1)[1].rsplit('\`\`\`', 1)[0].strip()
    # Validate it's valid JSON
    tasks = json.loads(content)
    print(json.dumps(tasks, indent=2))
except:
    print('[]')
" 2>/dev/null || echo "[]")

    echo "${tasks_json}" > "${task_file}"

    local task_count
    task_count=$(echo "${tasks_json}" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

    echo "## Queued Improvement Tasks" >> "${DAILY_REPORT}"
    echo "" >> "${DAILY_REPORT}"
    echo "Wrote ${task_count} task(s) to: ${task_file}" >> "${DAILY_REPORT}"
    echo "" >> "${DAILY_REPORT}"

    if [ "${task_count}" -gt 0 ]; then
        echo '```json' >> "${DAILY_REPORT}"
        echo "${tasks_json}" >> "${DAILY_REPORT}"
        echo '```' >> "${DAILY_REPORT}"
    fi

    log_ok "Queued ${task_count} improvement task(s)"
}

# -----------------------------------------------------------------------------
# Send daily summary notification
# -----------------------------------------------------------------------------
notify_summary() {
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        # Build concise summary for Telegram
        local summary
        summary=$(python3 << 'PYEOF'
import json, os

report_lines = open(os.environ.get('DAILY_REPORT', '/dev/null')).readlines()

# Count issues by type
issues = {'FAILURE': 0, 'SLOW': 0, 'MISSED': 0, 'IMPROVEMENT': 0, 'RECURRING': 0}
for line in report_lines:
    for key in issues:
        if key in line.upper():
            issues[key] += 1

# Build summary
parts = []
if issues['FAILURE']: parts.append(f"Failures: {issues['FAILURE']}")
if issues['SLOW']: parts.append(f"Slow: {issues['SLOW']}")
if issues['MISSED']: parts.append(f"Missed: {issues['MISSED']}")
if issues['IMPROVEMENT']: parts.append(f"Improvements: {issues['IMPROVEMENT']}")

if parts:
    print(f"Daily Report: {', '.join(parts)}")
else:
    print("Daily Report: All systems healthy, no issues found")
PYEOF
)

        DAILY_REPORT="${DAILY_REPORT}" curl -sf "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "text=${summary}" \
            &>/dev/null || true
    fi
}

# =============================================================================
# Main
# =============================================================================
main() {
    log_header "Daily Interaction Analysis & Improvement"

    ensure_dir "${REPORT_DIR}"
    ensure_dir "${IMPROVEMENTS_DIR}"

    # Initialize report
    cat > "${DAILY_REPORT}" << EOF
# AgentHarness Daily Improvement Report
**Date**: $(date '+%Y-%m-%d %H:%M')
**Current Setup**: ${BEST_MODEL:-unknown} on ${BEST_ENGINE:-unknown}

---

EOF

    # Phase 1: Collect data from OpenClaw
    extract_openclaw_sessions 24

    extract_telegram_logs

    # Phase 2: Analyze
    analyze_interactions

    # Phase 3: Fix and improve
    auto_fix_issues
    queue_improvements

    # Notify
    notify_summary

    # Footer
    cat >> "${DAILY_REPORT}" << EOF

---
*Report generated by AgentHarness daily_improve.sh*
EOF

    log_ok "Daily report saved to: ${DAILY_REPORT}"
}

main "$@"
