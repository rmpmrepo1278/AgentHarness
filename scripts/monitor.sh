#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# monitor.sh — Proactive monitoring + alerting + briefings
#
# Three modes:
#   monitor.sh check    — One-shot health check with alerts (called by scheduler)
#   monitor.sh briefing — Morning/evening summary via Telegram
#   monitor.sh alert    — Send a specific alert message
#
# Discovers existing monitoring (Uptime Kuma, Grafana alerts, custom scripts)
# and augments rather than duplicates.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

[ -f /opt/agentharness/.env ] && source /opt/agentharness/.env
[ -f /opt/agentharness/best_config.env ] && source /opt/agentharness/best_config.env

ALERT_QUEUE="/opt/agentharness/alert_queue.json"
MONITOR_STATE="/opt/agentharness/monitor_state.json"
LLM_URL="${LLM_PRIMARY_URL:-http://localhost:8080}"

# Thresholds (configurable via .env)
DISK_WARN_PCT="${DISK_WARN_PCT:-80}"
DISK_CRIT_PCT="${DISK_CRIT_PCT:-90}"
SWAP_WARN_MB="${SWAP_WARN_MB:-500}"
SWAP_CRIT_MB="${SWAP_CRIT_MB:-2000}"
CONTAINER_DOWN_MINUTES="${CONTAINER_DOWN_MINUTES:-5}"
RAM_WARN_PCT="${RAM_WARN_PCT:-85}"

# =============================================================================
# Discover existing monitoring
# =============================================================================
check_existing_monitoring() {
    # Don't alert on things already monitored by other tools
    local existing_monitors=()

    # Check for Uptime Kuma
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qi "uptime.*kuma\|kuma"; then
        existing_monitors+=("uptime_kuma")
    fi

    # Check for Grafana alerting
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qi "grafana"; then
        existing_monitors+=("grafana")
    fi

    # Check for existing monitoring scripts in automation catalog
    if [ -f /opt/agentharness/automation_catalog.json ]; then
        local has_monitoring
        has_monitoring=$(python3 -c "
import json
catalog = json.load(open('/opt/agentharness/automation_catalog.json'))
for item in catalog.get('items', []):
    if 'monitoring' in item.get('capabilities', []):
        print(item.get('path', ''))
" 2>/dev/null)
        [ -n "${has_monitoring}" ] && existing_monitors+=("custom_scripts")
    fi

    echo "${existing_monitors[*]:-}"
}

# =============================================================================
# Send alert via Telegram (or queue if offline)
# =============================================================================
send_alert() {
    local severity="$1"  # INFO, WARN, CRITICAL
    local message="$2"

    local icon=""
    case "${severity}" in
        CRITICAL) icon="🔴" ;;
        WARN)     icon="🟡" ;;
        INFO)     icon="🟢" ;;
    esac

    local full_msg="${icon} [${severity}] ${message}"

    # Try sending immediately
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        if curl -sf --max-time 5 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "text=${full_msg}" &>/dev/null; then
            return 0
        fi
    fi

    # Queue if couldn't send (offline)
    ensure_dir /opt/agentharness
    [ -f "${ALERT_QUEUE}" ] || echo '[]' > "${ALERT_QUEUE}"

    python3 -c "
import json
from datetime import datetime
queue = json.load(open('${ALERT_QUEUE}'))
queue.append({
    'severity': '${severity}',
    'message': '''${message}''',
    'queued_at': datetime.now().isoformat(),
    'sent': False
})
json.dump(queue, open('${ALERT_QUEUE}', 'w'), indent=2)
" 2>/dev/null
    log_info "Alert queued (offline): ${severity} — ${message}"
}

# =============================================================================
# Flush queued alerts (called when coming back online)
# =============================================================================
flush_alert_queue() {
    [ -f "${ALERT_QUEUE}" ] || return 0

    local pending
    pending=$(python3 -c "
import json
queue = json.load(open('${ALERT_QUEUE}'))
pending = [a for a in queue if not a.get('sent')]
print(len(pending))
" 2>/dev/null || echo "0")

    [ "${pending}" = "0" ] && return 0

    log_info "Flushing ${pending} queued alerts..."

    python3 -c "
import json, subprocess
queue = json.load(open('${ALERT_QUEUE}'))
for alert in queue:
    if not alert.get('sent'):
        msg = f\"[Queued {alert['queued_at']}] [{alert['severity']}] {alert['message']}\"
        try:
            subprocess.run([
                'curl', '-sf', '--max-time', '5',
                'https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage',
                '-d', 'chat_id=${TELEGRAM_CHAT_ID}',
                '-d', f'text={msg}'
            ], capture_output=True, timeout=10)
            alert['sent'] = True
        except:
            pass
json.dump(queue, open('${ALERT_QUEUE}', 'w'), indent=2)

# Purge sent alerts older than 7 days
from datetime import datetime, timedelta
cutoff = (datetime.now() - timedelta(days=7)).isoformat()
queue = [a for a in queue if not a.get('sent') or a.get('queued_at', '') > cutoff]
json.dump(queue, open('${ALERT_QUEUE}', 'w'), indent=2)
" 2>/dev/null
}

# =============================================================================
# Health checks
# =============================================================================
check_health() {
    log_info "Running health checks..."

    local alerts=()

    # --- Disk usage ---
    local disk_pct
    disk_pct=$(df / | awk 'NR==2 {gsub(/%/,""); print $5}')
    if [ "${disk_pct}" -ge "${DISK_CRIT_PCT}" ]; then
        alerts+=("CRITICAL|Disk at ${disk_pct}% — critically low!")
    elif [ "${disk_pct}" -ge "${DISK_WARN_PCT}" ]; then
        alerts+=("WARN|Disk at ${disk_pct}%")
    fi

    # --- Swap usage ---
    local swap_mb
    swap_mb=$(free -m | awk '/Swap/ {print $3}')
    if [ "${swap_mb}" -ge "${SWAP_CRIT_MB}" ]; then
        alerts+=("CRITICAL|Swap at ${swap_mb}MB — LLM likely swapping, system unstable")
    elif [ "${swap_mb}" -ge "${SWAP_WARN_MB}" ]; then
        alerts+=("WARN|Swap at ${swap_mb}MB — model may be too large")
    fi

    # --- RAM usage ---
    local ram_pct
    ram_pct=$(free | awk '/Mem/ {printf "%.0f", $3/$2*100}')
    if [ "${ram_pct}" -ge "${RAM_WARN_PCT}" ]; then
        alerts+=("WARN|RAM at ${ram_pct}%")
    fi

    # --- Docker containers ---
    # Unhealthy containers
    local unhealthy
    unhealthy=$(docker ps --filter "health=unhealthy" --format "{{.Names}}" 2>/dev/null || true)
    if [ -n "${unhealthy}" ]; then
        while IFS= read -r c; do
            alerts+=("WARN|Container ${c} is unhealthy")
        done <<< "${unhealthy}"
    fi

    # Containers that were running but stopped unexpectedly
    local exited
    exited=$(docker ps -a --filter "status=exited" --format "{{.Names}} {{.Status}}" 2>/dev/null | \
        grep -v "Exited (0)" | head -5 || true)  # Ignore clean exits
    if [ -n "${exited}" ]; then
        while IFS= read -r line; do
            local name
            name=$(echo "${line}" | awk '{print $1}')
            alerts+=("WARN|Container ${name} exited unexpectedly: ${line}")
        done <<< "${exited}"
    fi

    # --- LLM server health ---
    if ! curl -sf --max-time 5 http://localhost:8080/health &>/dev/null; then
        if ! curl -sf --max-time 5 http://localhost:8081/health &>/dev/null; then
            alerts+=("WARN|No LLM server responding on 8080 or 8081")
        fi
    fi

    # --- CPU temperature (if available) ---
    if command -v sensors &>/dev/null; then
        local temp
        temp=$(sensors 2>/dev/null | grep -oP '\+\K[0-9]+(?=\.[0-9]+°C)' | sort -rn | head -1 || echo "0")
        if [ "${temp}" -gt 85 ]; then
            alerts+=("WARN|CPU temp at ${temp}°C — throttling likely")
        fi
    fi

    # --- Send alerts ---
    for alert in "${alerts[@]:-}"; do
        [ -z "${alert}" ] && continue
        local sev msg
        IFS='|' read -r sev msg <<< "${alert}"
        send_alert "${sev}" "${msg}"
        log_warn "[${sev}] ${msg}"
    done

    # Update state
    python3 -c "
import json
from datetime import datetime
state = {
    'last_check': datetime.now().isoformat(),
    'disk_pct': ${disk_pct},
    'swap_mb': ${swap_mb},
    'ram_pct': ${ram_pct},
    'alerts_count': ${#alerts[@]},
    'status': 'healthy' if ${#alerts[@]} == 0 else 'degraded'
}
json.dump(state, open('${MONITOR_STATE}', 'w'), indent=2)
" 2>/dev/null

    if [ ${#alerts[@]} -eq 0 ]; then
        log_ok "All checks passed"
    else
        log_warn "${#alerts[@]} alert(s) generated"
    fi
}

# =============================================================================
# Morning / Evening briefing
# =============================================================================
generate_briefing() {
    local time_of_day="$1"  # morning or evening

    log_info "Generating ${time_of_day} briefing..."

    local briefing=""
    briefing+="📋 *${time_of_day^} Briefing* — $(date '+%A %B %d, %H:%M')%0A%0A"

    # System status
    local containers_running
    containers_running=$(docker ps --format '{{.Names}}' 2>/dev/null | wc -l)
    local containers_stopped
    containers_stopped=$(docker ps -a --filter "status=exited" --format '{{.Names}}' 2>/dev/null | wc -l)
    local disk_pct
    disk_pct=$(df / | awk 'NR==2 {gsub(/%/,""); print $5}')
    local ram_pct
    ram_pct=$(free | awk '/Mem/ {printf "%.0f", $3/$2*100}')
    local uptime_str
    uptime_str=$(uptime -p 2>/dev/null || uptime | awk -F'up ' '{print $2}' | awk -F',' '{print $1}')

    briefing+="🖥 *System*: Up ${uptime_str}%0A"
    briefing+="💾 Disk: ${disk_pct}%% | RAM: ${ram_pct}%%%0A"
    briefing+="🐳 Containers: ${containers_running} running"
    [ "${containers_stopped}" -gt 0 ] && briefing+=", ${containers_stopped} stopped"
    briefing+="%0A%0A"

    # LLM status
    local llm_status="offline"
    if curl -sf --max-time 3 http://localhost:8080/health &>/dev/null; then
        llm_status="primary (8080)"
    elif curl -sf --max-time 3 http://localhost:8081/health &>/dev/null; then
        llm_status="fast (8081)"
    fi
    briefing+="🧠 LLM: ${llm_status}%0A"

    # Best model info
    if [ -f /opt/agentharness/best_config.env ]; then
        source /opt/agentharness/best_config.env
        briefing+="📊 Best model: ${BEST_MODEL:-?} (score: ${BEST_COMPOSITE:-?}/10)%0A"
    fi
    briefing+="%0A"

    # Overnight activity (morning only)
    if [ "${time_of_day}" = "morning" ]; then
        # Check what ran overnight
        local overnight_reports
        overnight_reports=$(find /opt/agentharness/reports -name "*.md" -newer /tmp/morning_marker 2>/dev/null | wc -l || echo "0")
        touch /tmp/morning_marker

        briefing+="🌙 *Overnight*:%0A"

        # Check if benchmarks ran
        if [ -f /opt/agentharness/benchmark_results.json ]; then
            local bench_age
            bench_age=$(( ($(date +%s) - $(stat -c %Y /opt/agentharness/benchmark_results.json 2>/dev/null || echo "0")) / 3600 ))
            [ "${bench_age}" -lt 12 ] && briefing+="- Benchmarks ran (${bench_age}h ago)%0A"
        fi

        # Check if cleanup ran
        local last_cleanup
        last_cleanup=$(ls -t /opt/agentharness/reports/cleanup_*.md 2>/dev/null | head -1)
        if [ -n "${last_cleanup}" ]; then
            local cleanup_age
            cleanup_age=$(( ($(date +%s) - $(stat -c %Y "${last_cleanup}" 2>/dev/null || echo "0")) / 3600 ))
            [ "${cleanup_age}" -lt 12 ] && briefing+="- Cleanup ran%0A"
        fi

        # Queued alerts from overnight
        local queued
        queued=$(python3 -c "
import json
try:
    queue = json.load(open('${ALERT_QUEUE}'))
    pending = [a for a in queue if not a.get('sent')]
    print(len(pending))
except:
    print(0)
" 2>/dev/null || echo "0")
        [ "${queued}" -gt 0 ] && briefing+="- ⚠️ ${queued} alert(s) queued overnight%0A"

        briefing+="%0A"
    fi

    # GitHub deploy queue
    if [ -f /opt/agentharness/github_queue.json ]; then
        local pending_deploys
        pending_deploys=$(python3 -c "
import json
q = json.load(open('/opt/agentharness/github_queue.json'))
print(len([r for r in q if r.get('status') == 'pending']))
" 2>/dev/null || echo "0")
        [ "${pending_deploys}" -gt 0 ] && briefing+="📦 ${pending_deploys} GitHub repo(s) pending deploy%0A"
    fi

    # Send via Telegram
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -sf "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "text=${briefing}" \
            -d "parse_mode=Markdown" \
            &>/dev/null && log_ok "Briefing sent" || log_warn "Failed to send briefing"
    else
        # Print to stdout
        echo -e "${briefing}" | sed 's/%0A/\n/g'
    fi
}

# =============================================================================
# Main
# =============================================================================
main() {
    local mode="${1:-check}"

    ensure_dir /opt/agentharness

    case "${mode}" in
        check)
            check_health
            ;;
        briefing)
            local tod="${2:-morning}"
            generate_briefing "${tod}"
            # Also flush any queued alerts
            flush_alert_queue
            ;;
        alert)
            local sev="${2:-INFO}"
            local msg="${3:-No message}"
            send_alert "${sev}" "${msg}"
            ;;
        flush)
            flush_alert_queue
            ;;
        *)
            echo "Usage: monitor.sh {check|briefing [morning|evening]|alert SEVERITY MESSAGE|flush}"
            ;;
    esac
}

main "$@"
