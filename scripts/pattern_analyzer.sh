#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# pattern_analyzer.sh — Analyze interaction patterns and behavioral trends
#
# Runs weekly during offline hours. Extracts:
#   - Temporal patterns (when does Rohit ask what)
#   - Recurring requests (automate these)
#   - Failure patterns (prevent these)
#   - Preference drift (adapt to changes)
#   - Conversation style (communication preferences)
#
# Feeds results into chaguli_memory.sh as patterns and preferences.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

[ -f /opt/agentharness/.env ] && source /opt/agentharness/.env
[ -f /opt/agentharness/openclaw_paths.env ] && source /opt/agentharness/openclaw_paths.env

LLM_URL="${LLM_PRIMARY_URL:-http://localhost:8080}"
PATTERN_REPORT="/opt/agentharness/reports/patterns_$(timestamp).md"

# =============================================================================
# Collect interaction data from multiple sources
# =============================================================================
collect_data() {
    log_info "Collecting interaction data..."

    local data_file="/opt/agentharness/pattern_data.json"

    python3 << 'PYEOF'
import json, os, glob
from datetime import datetime, timedelta
from collections import defaultdict

data = {
    "interactions": [],
    "scheduler_runs": [],
    "alerts_sent": [],
    "failures": []
}

# Collect from daily reports
for report in sorted(glob.glob("/opt/agentharness/reports/daily_*.md"))[-30:]:
    try:
        content = open(report).read()
        date_str = os.path.basename(report).replace("daily_", "").replace(".md", "")
        data["interactions"].append({
            "date": date_str[:10],
            "content_preview": content[:500],
            "has_failures": "FAILURE" in content.upper(),
            "has_improvements": "IMPROVEMENT" in content.upper()
        })
    except:
        pass

# Collect from scheduler state
state_file = "/opt/agentharness/registry_state.json"
if os.path.exists(state_file):
    try:
        state = json.load(open(state_file))
        for name, info in state.items():
            data["scheduler_runs"].append({
                "task": name,
                "last_run": info.get("last_run", ""),
                "exit_code": info.get("exit_code", -1)
            })
    except:
        pass

# Collect from alert queue
alert_file = "/opt/agentharness/alert_queue.json"
if os.path.exists(alert_file):
    try:
        alerts = json.load(open(alert_file))
        for alert in alerts[-50:]:
            data["alerts_sent"].append({
                "severity": alert.get("severity", ""),
                "message": alert.get("message", "")[:100],
                "time": alert.get("queued_at", "")
            })
    except:
        pass

# Collect from improvement tasks
for task_file in sorted(glob.glob("/opt/agentharness/improvements/tasks_*.json"))[-14:]:
    try:
        tasks = json.load(open(task_file))
        for task in tasks:
            if task.get("category") in ("FAILURE", "RECURRING"):
                data["failures"].append({
                    "category": task["category"],
                    "description": task.get("description", "")[:200],
                    "severity": task.get("severity", "")
                })
    except:
        pass

json.dump(data, open("/opt/agentharness/pattern_data.json", "w"), indent=2)
print(f"Collected: {len(data['interactions'])} daily reports, {len(data['alerts_sent'])} alerts, {len(data['failures'])} failures")
PYEOF
}

# =============================================================================
# Analyze patterns with LLM
# =============================================================================
analyze_patterns() {
    log_info "Analyzing patterns with local LLM..."

    if ! curl -sf "${LLM_URL}/health" &>/dev/null; then
        log_warn "LLM not available. Skipping AI analysis."
        return 0
    fi

    local data
    data=$(cat /opt/agentharness/pattern_data.json)

    local analysis
    analysis=$(curl -sf --max-time 600 "${LLM_URL}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "
import json
data = open('/opt/agentharness/pattern_data.json').read()[:4000]
print(json.dumps({
    'messages': [
        {'role': 'system', 'content': '''Analyze these homelab interaction patterns. Extract:

1. TEMPORAL PATTERNS: When do specific types of requests happen? (day of week, time of day)
2. RECURRING REQUESTS: Things asked repeatedly that could be automated
3. FAILURE PATTERNS: Services or issues that keep happening
4. PREFERENCE SIGNALS: Communication style preferences, response length preferences, topics of interest
5. AUTOMATION OPPORTUNITIES: Things the agent should start doing proactively

Output as JSON:
{
  \"temporal_patterns\": [{\"pattern\": \"...\", \"confidence\": \"high/medium/low\"}],
  \"recurring_requests\": [{\"request\": \"...\", \"frequency\": \"...\", \"automate\": true/false}],
  \"failure_patterns\": [{\"service\": \"...\", \"pattern\": \"...\", \"prevention\": \"...\"}],
  \"preferences\": [{\"preference\": \"...\", \"evidence\": \"...\"}],
  \"automation_opportunities\": [{\"what\": \"...\", \"why\": \"...\", \"how\": \"...\"}]
}'''},
        {'role': 'user', 'content': data}
    ],
    'max_tokens': 1000,
    'temperature': 0.2
}))
" 2>/dev/null)" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    content = d['choices'][0]['message']['content'].strip()
    if content.startswith('\`\`\`'):
        content = content.split('\n', 1)[1].rsplit('\`\`\`', 1)[0].strip()
    parsed = json.loads(content)
    print(json.dumps(parsed, indent=2))
except Exception as e:
    print(json.dumps({'error': str(e)}))
" 2>/dev/null || echo '{"error": "analysis failed"}')

    echo "${analysis}" > /opt/agentharness/latest_patterns.json

    # Feed patterns into memory
    python3 -c "
import json, subprocess

try:
    patterns = json.loads('''${analysis}''')
except:
    exit(0)

def save(mem_type, content, source='pattern_analyzer'):
    subprocess.run(['bash', '/opt/agentharness/scripts/chaguli_memory.sh', 'add', mem_type, content, source],
                   capture_output=True, timeout=10)

# Save temporal patterns
for p in patterns.get('temporal_patterns', []):
    if p.get('confidence') in ('high', 'medium'):
        save('patterns', p['pattern'])

# Save recurring requests as automation candidates
for r in patterns.get('recurring_requests', []):
    if r.get('automate'):
        save('patterns', f\"Recurring: {r['request']} ({r.get('frequency', '?')}). Should automate.\")

# Save failure patterns
for f in patterns.get('failure_patterns', []):
    save('incidents', f\"{f.get('service', '?')}: {f['pattern']}. Prevention: {f.get('prevention', '?')}\")

# Save preferences
for p in patterns.get('preferences', []):
    save('preferences', p['preference'])

print('Patterns saved to memory')
" 2>/dev/null

    log_ok "Patterns analyzed and saved to memory"
}

# =============================================================================
# Generate report
# =============================================================================
generate_report() {
    cat > "${PATTERN_REPORT}" << EOF
# Pattern Analysis Report
**Date**: $(date '+%Y-%m-%d %H:%M')

---

EOF

    if [ -f /opt/agentharness/latest_patterns.json ]; then
        python3 -c "
import json

patterns = json.load(open('/opt/agentharness/latest_patterns.json'))

if 'error' in patterns:
    print(f'Analysis error: {patterns[\"error\"]}')
    exit(0)

print('## Temporal Patterns')
for p in patterns.get('temporal_patterns', []):
    print(f\"- [{p.get('confidence', '?')}] {p['pattern']}\")

print('\n## Recurring Requests')
for r in patterns.get('recurring_requests', []):
    auto = 'AUTOMATE' if r.get('automate') else 'manual'
    print(f\"- [{auto}] {r['request']} (freq: {r.get('frequency', '?')})\")

print('\n## Failure Patterns')
for f in patterns.get('failure_patterns', []):
    print(f\"- {f.get('service', '?')}: {f['pattern']}\")
    print(f\"  Prevention: {f.get('prevention', '?')}\")

print('\n## Preferences Detected')
for p in patterns.get('preferences', []):
    print(f\"- {p['preference']}\")
    if p.get('evidence'):
        print(f\"  Evidence: {p['evidence']}\")

print('\n## Automation Opportunities')
for a in patterns.get('automation_opportunities', []):
    print(f\"- **{a['what']}**: {a['why']}\")
    print(f\"  How: {a.get('how', '?')}\")
" 2>/dev/null >> "${PATTERN_REPORT}"
    fi

    log_ok "Report: ${PATTERN_REPORT}"
}

# =============================================================================
main() {
    log_header "Pattern Analysis"
    ensure_dir /opt/agentharness/reports

    collect_data
    analyze_patterns
    generate_report
}

main "$@"
