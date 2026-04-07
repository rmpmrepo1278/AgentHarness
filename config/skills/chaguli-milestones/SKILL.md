---
name: chaguli-milestones
description: Track homelab and personal milestones, celebrate achievements
requires:
  binaries: ["python3", "docker"]
---

# Milestones & Celebrations

Track meaningful milestones and celebrate them in the morning briefing.

## Check Homelab Milestones

```bash
python3 -c "
from datetime import datetime

milestones = []

# System uptime
import subprocess
result = subprocess.run(['cat', '/proc/uptime'], capture_output=True, text=True)
uptime_seconds = float(result.stdout.split()[0])
uptime_days = int(uptime_seconds / 86400)

for threshold in [7, 30, 60, 90, 180, 365]:
    if uptime_days == threshold:
        milestones.append(f'System uptime: {threshold} days! Rock solid.')

# Container count
result = subprocess.run(['docker', 'ps', '-q'], capture_output=True, text=True)
container_count = len(result.stdout.strip().split('\n')) if result.stdout.strip() else 0
for threshold in [10, 15, 20, 25, 30]:
    if container_count == threshold:
        milestones.append(f'Running {threshold} containers. The homelab grows.')

# Docker images managed
result = subprocess.run(['docker', 'images', '-q'], capture_output=True, text=True)
image_count = len(set(result.stdout.strip().split('\n'))) if result.stdout.strip() else 0

# AgentHarness age
import os, json
if os.path.exists('/opt/agentharness/chaguli_memory.json'):
    mem = json.load(open('/opt/agentharness/chaguli_memory.json'))
    created = mem.get('created_at', '')
    if created:
        age_days = (datetime.now() - datetime.fromisoformat(created)).days
        for threshold in [7, 30, 60, 90]:
            if age_days == threshold:
                milestones.append(f'AgentHarness has been running for {threshold} days. Getting smarter every day.')

# Memory entries
if os.path.exists('/opt/agentharness/chaguli_memory.json'):
    mem = json.load(open('/opt/agentharness/chaguli_memory.json'))
    total = sum(len(mem.get(t, [])) for t in ('preferences', 'patterns', 'incidents', 'knowledge', 'tasks'))
    for threshold in [10, 25, 50, 100, 200]:
        if total == threshold:
            milestones.append(f'I now have {threshold} memories. Your chief of staff is learning.')

# Benchmark improvements
if os.path.exists('/opt/agentharness/benchmark_results.json'):
    results = json.load(open('/opt/agentharness/benchmark_results.json'))
    if results:
        best_score = results[0].get('composite_score', 0)
        if best_score > 8:
            milestones.append(f'LLM benchmark score: {best_score}/10. Impressive for local inference.')

for m in milestones:
    print(m)
"
```

## Include in Morning Briefing

If any milestones are detected, add them to the briefing with a celebration tone:
> "Milestone: Your homelab has been running 90 days straight. 99.4% uptime. Not bad at all."

Keep it brief and genuine. One milestone per briefing max.
