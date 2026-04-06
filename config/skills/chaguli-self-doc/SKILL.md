---
name: chaguli-self-doc
description: Tell the user everything Chaguli can do — dynamically reads installed skills, services, and capabilities
requires:
  binaries: ["bash", "curl"]
---

# Self-Documentation

When the user asks "what can you do?", "help", "capabilities", or "what skills do you have?" — use this skill to give a comprehensive, dynamic answer.

## List All Installed Skills

```bash
if command -v clawhub &>/dev/null; then
  echo "=== Installed ClawHub Skills ==="
  clawhub list 2>/dev/null || true
fi
echo ""
echo "=== Workspace Skills ==="
find ~/.openclaw/workspace/skills -name "SKILL.md" -exec sh -c 'echo "- $(basename $(dirname {})): $(head -20 {} | grep "description:" | sed "s/description: //")"' \; 2>/dev/null || echo "(skills directory not found)"
```

## List All Homelab Services Chaguli Can Manage

```bash
if [ -f /opt/agentharness/service_registry.json ]; then
  python3 -c "
import json
reg = json.load(open('/opt/agentharness/service_registry.json'))
print(f'I manage {reg[\"total_services\"]} services with {reg[\"total_api_endpoints\"]} API endpoints.')
print()
for svc in reg['services']:
    caps = ', '.join(svc.get('chaguli_capabilities', [])) or 'docker management'
    print(f'  • {svc[\"container\"]} ({svc.get(\"category\", \"other\")}): {caps}')
print()
print(f'Capabilities: {chr(10).join(\"  • \" + c for c in reg[\"all_capabilities\"])}')
"
else
  echo "Service registry not built yet. Run: bash /opt/agentharness/scripts/service_registry.sh"
fi
```

## List AgentHarness Maintenance Commands

```bash
echo "=== Maintenance Commands ==="
echo "  • Health check:     bash /opt/agentharness/scripts/validate.sh"
echo "  • Diagnose issues:  bash /opt/agentharness/scripts/doctor.sh"
echo "  • System cleanup:   bash /opt/agentharness/scripts/cleanup.sh"
echo "  • Run benchmarks:   bash /opt/agentharness/scripts/benchmark.sh"
echo "  • Deploy a repo:    bash /opt/agentharness/scripts/github_deploy.sh <url>"
echo "  • View reports:     ls -lt /opt/agentharness/reports/ | head -10"
echo "  • Plugin registry:  python3 /opt/agentharness/scripts/registry_engine.py list"
echo "  • My memory:        bash /opt/agentharness/scripts/chaguli_memory.sh context"
```

## List Monitoring Checks

```bash
if [ -f /opt/agentharness/scripts/registry_engine.py ]; then
  python3 /opt/agentharness/scripts/registry_engine.py list
fi
```

## How to Respond

Combine the outputs above into a friendly, concise summary. Group by category:

1. **Homelab Services** — what you can monitor, query, restart
2. **Media** — arr stack, Jellyfin, Immich capabilities
3. **Search & Research** — web search, article summarization
4. **Maintenance** — cleanup, benchmarks, backups, deployments
5. **Personal** — reminders, memory, calendar, email
6. **Self-Improvement** — what you learn and optimize automatically

End with: "Ask me about any of these, or say 'add a check for X' to teach me something new."
