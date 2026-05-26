#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# personal_agent_cron.sh — Proactive personal agent tasks
#
# Usage: personal_agent_cron.sh {wellness-check|habit-review|finance-report|subscription-audit}
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

LOG_FILE="${AH_LOGS_DIR:-/home/rohit/agentharness/logs}/personal_agent.log"
HERMES_ROOT="/home/rohit/.hermes"
TODAY=$(date +%Y-%m-%d)

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "${LOG_FILE}" 2>/dev/null
}

# -----------------------------------------------------------------------------
# Daily Wellness Check-in
# -----------------------------------------------------------------------------
wellness_check() {
    log "Running daily wellness check..."

    CHECK_FILE="${HERMES_ROOT}/wellness/.last_checkin"

    if [ -f "${CHECK_FILE}" ] && [ "$(cat "${CHECK_FILE}")" = "${TODAY}" ]; then
        log "Wellness check already sent today. Skipping."
        return 0
    fi

    cat > "${HERMES_ROOT}/wellness/pending_checkin.json" << ENDJSON
{
    "type": "wellness_checkin",
    "date": "${TODAY}",
    "prompt": "Daily wellness check-in for ${TODAY}. Ask Rohit about his mood (1-10), energy (1-10), sleep quality last night (1-10), stress level (1-10), and any notes about his day. Be conversational and brief.",
    "created_at": "$(date -Iseconds)"
}
ENDJSON

    echo "${TODAY}" > "${CHECK_FILE}"
    log "Wellness check-in queued for ${TODAY}"
}

# -----------------------------------------------------------------------------
# Weekly Habit Review
# -----------------------------------------------------------------------------
habit_review() {
    log "Running weekly habit review..."
    python3 "${SCRIPT_DIR}/habit_review.py" "${HERMES_ROOT}/habits/habits.json"
    log "Weekly habit review generated"
}

# -----------------------------------------------------------------------------
# Monthly Finance Report
# -----------------------------------------------------------------------------
finance_report() {
    log "Running monthly finance report..."
    python3 "${SCRIPT_DIR}/finance_report.py" \
        "${HERMES_ROOT}/finance/subscriptions.json" \
        "${HERMES_ROOT}/personal/bills.json" \
        "${HERMES_ROOT}/finance/budget.json"
    log "Monthly finance report generated"
}

# -----------------------------------------------------------------------------
# Subscription Audit
# -----------------------------------------------------------------------------
subscription_audit() {
    log "Running subscription audit..."
    python3 "${SCRIPT_DIR}/subscription_audit.py" "${HERMES_ROOT}/finance/subscriptions.json"
    log "Subscription audit complete"
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
ACTION="${1:-help}"

case "${ACTION}" in
    wellness-check)     wellness_check ;;
    habit-review)       habit_review ;;
    finance-report)     finance_report ;;
    subscription-audit) subscription_audit ;;
    *)
        echo "Usage: $0 {wellness-check|habit-review|finance-report|subscription-audit}"
        exit 1
        ;;
esac
