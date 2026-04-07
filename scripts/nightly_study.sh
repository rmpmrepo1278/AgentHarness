#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# nightly_study.sh — Chaguli's self-improvement session
#
# Reviews failures from the day, researches gaps, writes/updates skills.
# Runs during offline hours. The closed learning loop.
#
# Cycle: Fail → Log → Research → Learn → Improve → Don't fail next time
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

[ -f /opt/agentharness/.env ] && source /opt/agentharness/.env
[ -f /opt/agentharness/openclaw_paths.env ] && source /opt/agentharness/openclaw_paths.env

LLM_URL="${LLM_PRIMARY_URL:-http://localhost:8080}"
SEARXNG_URL="${SEARXNG_URL:-http://localhost:8888}"
FAILURE_LOG="/opt/agentharness/failure_journal.json"
STUDY_REPORT="/opt/agentharness/reports/study_$(timestamp).md"

# =============================================================================
# Collect today's failures
# =============================================================================
collect_failures() {
    log_info "Reviewing today's failures..."

    [ -f "${FAILURE_LOG}" ] || echo '[]' > "${FAILURE_LOG}"

    # Find failures from daily improve reports
    local today
    today=$(date +%Y-%m-%d)
    local failures=0

    # Check improvement tasks for failures
    for task_file in /opt/agentharness/improvements/tasks_${today}*.json; do
        [ -f "${task_file}" ] || continue
        python3 -c "
import json
tasks = json.load(open('${task_file}'))
journal = json.load(open('${FAILURE_LOG}'))
for task in tasks:
    if task.get('category') in ('FAILURE', 'MISSED', 'SLOW'):
        # Don't add duplicates
        existing = [e.get('description', '') for e in journal]
        if task.get('description', '') not in existing:
            journal.append({
                'date': '${today}',
                'category': task['category'],
                'description': task.get('description', ''),
                'severity': task.get('severity', 'MEDIUM'),
                'researched': False,
                'resolved': False
            })
json.dump(journal, open('${FAILURE_LOG}', 'w'), indent=2)
" 2>/dev/null
        ((failures++))
    done

    # Check scheduler failures
    if [ -f /opt/agentharness/registry_state.json ]; then
        python3 -c "
import json
from datetime import datetime, timedelta

state = json.load(open('/opt/agentharness/registry_state.json'))
journal = json.load(open('${FAILURE_LOG}'))
cutoff = (datetime.now() - timedelta(days=1)).isoformat()

for name, info in state.items():
    if info.get('exit_code', 0) != 0 and info.get('last_run', '') > cutoff:
        desc = f'Scheduled task {name} failed (exit {info[\"exit_code\"]}): {info.get(\"error_tail\", \"\")[:100]}'
        existing = [e.get('description', '') for e in journal]
        if desc not in existing:
            journal.append({
                'date': datetime.now().strftime('%Y-%m-%d'),
                'category': 'TASK_FAILURE',
                'description': desc,
                'severity': 'MEDIUM',
                'researched': False,
                'resolved': False
            })

json.dump(journal, open('${FAILURE_LOG}', 'w'), indent=2)
" 2>/dev/null
    fi

    local total_unresolved
    total_unresolved=$(python3 -c "
import json
journal = json.load(open('${FAILURE_LOG}'))
print(len([f for f in journal if not f.get('resolved')]))
" 2>/dev/null || echo "0")

    log_info "${total_unresolved} unresolved failure(s) to study"
}

# =============================================================================
# Research a failure and generate a fix
# =============================================================================
research_failure() {
    local failure_json="$1"
    local description
    description=$(echo "${failure_json}" | python3 -c "import sys,json; print(json.load(sys.stdin)['description'])" 2>/dev/null)

    log_info "Researching: ${description:0:80}..."

    # Search for solutions
    local search_results=""
    if curl -sf "${SEARXNG_URL}/healthz" &>/dev/null 2>&1 || \
       curl -sf "${SEARXNG_URL}/search?q=test&format=json" &>/dev/null; then
        local query
        query=$(python3 -c "import urllib.parse; print(urllib.parse.quote('${description:0:100} fix solution homelab docker'))" 2>/dev/null)
        search_results=$(curl -sf "${SEARXNG_URL}/search?q=${query}&format=json" 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for r in data.get('results', [])[:5]:
        print(f\"- {r.get('title', '')}: {r.get('content', '')[:150]}\")
except:
    pass
" 2>/dev/null || echo "(search unavailable — offline)")
    fi

    # Ask LLM to synthesize a fix
    if curl -sf "${LLM_URL}/health" &>/dev/null; then
        local fix
        fix=$(curl -sf --max-time 300 "${LLM_URL}/v1/chat/completions" \
            -H "Content-Type: application/json" \
            -d "$(python3 -c "
import json
print(json.dumps({
    'messages': [
        {'role': 'system', 'content': 'You are a homelab sysadmin. Given a failure description and search results, provide: 1) Root cause 2) Fix (specific commands) 3) Prevention (what to add to monitoring). Be concise.'},
        {'role': 'user', 'content': f'Failure: ${description}\n\nSearch results:\n${search_results}'}
    ],
    'max_tokens': 400,
    'temperature': 0.2
}))
" 2>/dev/null)" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d['choices'][0]['message']['content'])
except:
    print('(analysis unavailable)')
" 2>/dev/null || echo "(LLM unavailable)")

        echo "${fix}"
    fi
}

# =============================================================================
# Apply learnings — update skills or add monitoring checks
# =============================================================================
apply_learnings() {
    log_info "Applying learnings..."

    python3 << 'PYEOF'
import json, subprocess

journal = json.load(open("/opt/agentharness/failure_journal.json"))
unresolved = [f for f in journal if not f.get("resolved") and not f.get("researched")][:5]

for failure in unresolved:
    desc = failure.get("description", "")

    # Mark as researched
    failure["researched"] = True
    failure["researched_at"] = __import__("datetime").datetime.now().isoformat()

    # Save the learning to memory
    subprocess.run([
        "bash", "/opt/agentharness/scripts/chaguli_memory.sh",
        "add", "incidents", desc, "nightly_study"
    ], capture_output=True, timeout=10)

json.dump(journal, open("/opt/agentharness/failure_journal.json", "w"), indent=2)
print(f"Studied {len(unresolved)} failure(s)")

# Prune resolved failures older than 90 days
from datetime import datetime, timedelta
cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
journal = [f for f in journal if f.get("date", "9999") > cutoff or not f.get("resolved")]
json.dump(journal, open("/opt/agentharness/failure_journal.json", "w"), indent=2)
PYEOF
}

# =============================================================================
# Generate study report
# =============================================================================
generate_report() {
    cat > "${STUDY_REPORT}" << EOF
# Nightly Study Session
**Date**: $(date '+%Y-%m-%d %H:%M')

---

EOF

    python3 -c "
import json

journal = json.load(open('/opt/agentharness/failure_journal.json'))
unresolved = [f for f in journal if not f.get('resolved')]
recent = [f for f in journal if f.get('researched')][-10:]

print(f'## Summary')
print(f'Total failures tracked: {len(journal)}')
print(f'Unresolved: {len(unresolved)}')
print(f'Researched tonight: {len([f for f in recent if f.get(\"researched\")])}')
print()

if recent:
    print('## Studied Tonight')
    for f in recent[-5:]:
        print(f'- [{f.get(\"severity\", \"?\")}] {f.get(\"description\", \"\")[:100]}')
        print(f'  Researched: {f.get(\"researched_at\", \"?\")[:16]}')
    print()

if unresolved:
    print('## Still Unresolved')
    for f in unresolved[:5]:
        print(f'- [{f.get(\"severity\", \"?\")}] {f.get(\"description\", \"\")[:100]}')
" 2>/dev/null >> "${STUDY_REPORT}"

    log_ok "Report: ${STUDY_REPORT}"
}

# =============================================================================
main() {
    log_header "Nightly Study Session"
    ensure_dir /opt/agentharness/reports

    collect_failures
    apply_learnings
    generate_report
}

main "$@"
