#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# chaguli_memory.sh — Long-term memory store for Chaguli
#
# Manages a persistent knowledge base that survives across sessions.
# Chaguli can write to it (via a skill) and AgentHarness feeds patterns
# from daily analysis back into it.
#
# Memory types:
#   - preferences: How Rohit likes things done
#   - patterns: Recurring events/requests
#   - incidents: Past issues and their resolutions
#   - knowledge: Facts about the homelab
#   - tasks: Pending items and reminders
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

MEMORY_FILE="/opt/agentharness/chaguli_memory.json"
MEMORY_SKILL_DIR=""  # Set from discovered paths

[ -f /opt/agentharness/.env ] && source /opt/agentharness/.env
[ -f /opt/agentharness/openclaw_paths.env ] && source /opt/agentharness/openclaw_paths.env

MEMORY_SKILL_DIR="${OPENCLAW_SKILLS_DIR:-}/agentharness-memory"

# =============================================================================
# Initialize memory store
# =============================================================================
init_memory() {
    if [ ! -f "${MEMORY_FILE}" ]; then
        python3 -c "
import json
from datetime import datetime
memory = {
    'created_at': datetime.now().isoformat(),
    'updated_at': datetime.now().isoformat(),
    'preferences': [],
    'patterns': [],
    'incidents': [],
    'knowledge': [],
    'tasks': []
}
json.dump(memory, open('${MEMORY_FILE}', 'w'), indent=2)
print('Memory store initialized')
" 2>/dev/null
    fi
}

# =============================================================================
# Add a memory entry
# =============================================================================
add_memory() {
    local type="$1"      # preferences, patterns, incidents, knowledge, tasks
    local content="$2"
    local source="${3:-manual}"  # manual, daily_improve, interaction, system

    python3 -c "
import json
from datetime import datetime

memory = json.load(open('${MEMORY_FILE}'))

# Check for duplicates (fuzzy — same type + similar content)
existing = memory.get('${type}', [])
for entry in existing:
    if entry.get('content', '').lower() == '''${content}'''.lower():
        print('Duplicate — skipping')
        exit(0)

entry = {
    'content': '''${content}''',
    'source': '${source}',
    'created_at': datetime.now().isoformat(),
    'active': True
}

memory.setdefault('${type}', []).append(entry)
memory['updated_at'] = datetime.now().isoformat()
json.dump(memory, open('${MEMORY_FILE}', 'w'), indent=2)
print(f'Added {\"${type}\"} memory: ${content:.50}...')
" 2>/dev/null
}

# =============================================================================
# Feed patterns from daily analysis into memory
# =============================================================================
ingest_daily_patterns() {
    log_info "Ingesting patterns from daily analysis..."

    local tasks_dir="/opt/agentharness/improvements"
    [ -d "${tasks_dir}" ] || return 0

    # Find the latest improvement tasks file
    local latest
    latest=$(ls -t "${tasks_dir}"/tasks_*.json 2>/dev/null | head -1)
    [ -z "${latest}" ] && return 0

    python3 -c "
import json

tasks = json.load(open('${latest}'))
memory = json.load(open('${MEMORY_FILE}'))

for task in tasks:
    category = task.get('category', '')
    description = task.get('description', '')
    severity = task.get('severity', '')

    if not description:
        continue

    # Map task categories to memory types
    mem_type = 'knowledge'
    if category == 'RECURRING':
        mem_type = 'patterns'
    elif category == 'FAILURE':
        mem_type = 'incidents'
    elif category == 'IMPROVEMENT':
        mem_type = 'knowledge'

    # Check for duplicates
    existing_contents = [e.get('content', '').lower() for e in memory.get(mem_type, [])]
    if description.lower() not in existing_contents:
        memory.setdefault(mem_type, []).append({
            'content': description,
            'source': 'daily_improve',
            'severity': severity,
            'created_at': __import__('datetime').datetime.now().isoformat(),
            'active': True
        })

from datetime import datetime
memory['updated_at'] = datetime.now().isoformat()
json.dump(memory, open('${MEMORY_FILE}', 'w'), indent=2)

total = sum(len(memory.get(t, [])) for t in ('preferences', 'patterns', 'incidents', 'knowledge', 'tasks'))
print(f'Memory: {total} total entries')
" 2>/dev/null

    log_ok "Patterns ingested into memory"
}

# =============================================================================
# Generate memory context for Chaguli's prompt
# =============================================================================
generate_memory_context() {
    python3 -c "
import json
from datetime import datetime, timedelta

memory = json.load(open('${MEMORY_FILE}'))

context = '## What I Remember\n\n'

# Preferences (always include all)
prefs = [e for e in memory.get('preferences', []) if e.get('active')]
if prefs:
    context += '### Your Preferences\n'
    for p in prefs[-10:]:
        context += f\"- {p['content']}\n\"
    context += '\n'

# Recent patterns
patterns = [e for e in memory.get('patterns', []) if e.get('active')]
if patterns:
    context += '### Patterns I\'ve Noticed\n'
    for p in patterns[-5:]:
        context += f\"- {p['content']}\n\"
    context += '\n'

# Recent incidents (last 30 days)
cutoff = (datetime.now() - timedelta(days=30)).isoformat()
incidents = [e for e in memory.get('incidents', []) if e.get('created_at', '') > cutoff]
if incidents:
    context += '### Recent Incidents\n'
    for i in incidents[-5:]:
        context += f\"- {i['content']}\n\"
    context += '\n'

# Active tasks/reminders
tasks = [e for e in memory.get('tasks', []) if e.get('active')]
if tasks:
    context += '### Pending Tasks\n'
    for t in tasks:
        context += f\"- {t['content']}\n\"
    context += '\n'

print(context)
" 2>/dev/null
}

# =============================================================================
# Generate OpenClaw skill for memory management
# =============================================================================
generate_memory_skill() {
    [ -z "${MEMORY_SKILL_DIR}" ] && return 0
    mkdir -p "${MEMORY_SKILL_DIR}"

    cat > "${MEMORY_SKILL_DIR}/SKILL.md" << 'SKILL'
---
name: agentharness-memory
description: Remember things across conversations and manage reminders
requires:
  binaries: ["bash"]
---

# Memory Management

You can remember things across conversations using these commands.

## Remember a Preference

When Rohit tells you he prefers something a certain way:
```bash
bash /opt/agentharness/scripts/chaguli_memory.sh add preferences "WHAT TO REMEMBER" interaction
```

## Remember a Pattern

When you notice something recurring:
```bash
bash /opt/agentharness/scripts/chaguli_memory.sh add patterns "PATTERN DESCRIPTION" interaction
```

## Log an Incident

When something breaks and gets fixed:
```bash
bash /opt/agentharness/scripts/chaguli_memory.sh add incidents "WHAT HAPPENED AND HOW IT WAS FIXED" interaction
```

## Add a Task/Reminder

When Rohit asks you to remember to do something:
```bash
bash /opt/agentharness/scripts/chaguli_memory.sh add tasks "TASK DESCRIPTION" interaction
```

## Recall What You Know

To see all stored memories:
```bash
bash /opt/agentharness/scripts/chaguli_memory.sh context
```

## When to Use Memory

- Rohit says "remember that I prefer X" → add preference
- You notice Rohit asks the same thing every week → add pattern
- A service broke and was fixed → add incident with resolution
- "Remind me to check X tomorrow" → add task
SKILL

    log_ok "Memory skill generated at ${MEMORY_SKILL_DIR}/SKILL.md"
}

# =============================================================================
# Main
# =============================================================================
main() {
    local cmd="${1:-context}"

    ensure_dir /opt/agentharness
    init_memory

    case "${cmd}" in
        add)
            local type="${2:-knowledge}"
            local content="${3:-}"
            local source="${4:-manual}"
            [ -z "${content}" ] && { echo "Usage: chaguli_memory.sh add TYPE CONTENT [SOURCE]"; exit 1; }
            add_memory "${type}" "${content}" "${source}"
            ;;
        ingest)
            ingest_daily_patterns
            ;;
        context)
            generate_memory_context
            ;;
        skill)
            generate_memory_skill
            ;;
        *)
            echo "Usage: chaguli_memory.sh {add TYPE CONTENT|ingest|context|skill}"
            ;;
    esac
}

main "$@"
