---
name: chaguli-error-explainer
description: When the user pastes an error, stack trace, or log snippet — diagnose it with context about their specific homelab setup
requires:
  binaries: ["curl", "docker"]
---

# Error Explainer

When the user pastes an error message, stack trace, log output, or says "why is X broken" — use this skill to diagnose it.

## Step 1: Identify the Source

Determine which service/container the error comes from. Check:

```bash
# List running containers to match the error to a service
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

## Step 2: Get More Context

Once you know which container, pull recent logs:

```bash
# Replace CONTAINER with the identified container name
docker logs --tail 100 CONTAINER 2>&1 | tail -50
```

Check if the container is healthy:

```bash
docker inspect --format='{{.State.Status}} {{.State.Health.Status}}' CONTAINER 2>/dev/null
```

Check resource pressure:

```bash
docker stats --no-stream CONTAINER
free -h
df -h /
```

## Step 3: Check Incident History

See if this error has happened before:

```bash
bash /opt/agentharness/scripts/chaguli_memory.sh context | grep -i "incident\|error\|fix"
```

## Step 4: Search for Solutions

If the error is unfamiliar, search the web:

```bash
SEARXNG_URL="${SEARXNG_URL:-http://localhost:8888}"
# URL-encode the key part of the error message
ERROR_KEY="the distinctive part of the error message"
curl -sf "${SEARXNG_URL}/search?q=$(python3 -c "import urllib.parse; print(urllib.parse.quote('${ERROR_KEY} fix solution'))")&format=json" | \
  python3 -c "
import sys, json
data = json.load(sys.stdin)
for r in data.get('results', [])[:5]:
    print(f'• {r.get(\"title\", \"\")}')
    print(f'  {r.get(\"url\", \"\")}')
    print(f'  {r.get(\"content\", \"\")[:150]}')
    print()
"
```

## Step 5: Explain and Suggest Fix

Structure your response as:

1. **What happened**: Plain English explanation of the error
2. **Why it happened**: Root cause based on the logs and context
3. **What I checked**: Show the diagnostic commands you ran
4. **How to fix it**: Specific commands or steps
5. **Prevention**: How to prevent this from recurring (if applicable)

## Step 6: Log the Incident

After diagnosing, save it to memory so you remember next time:

```bash
bash /opt/agentharness/scripts/chaguli_memory.sh add incidents "SERVICE: BRIEF DESCRIPTION OF ERROR AND RESOLUTION" interaction
```

## Common Homelab Error Patterns

### Container won't start
1. Check logs: `docker logs CONTAINER`
2. Common causes: port conflict, missing volume, bad env var, OOM killed
3. Fix: `docker compose down && docker compose up -d` from the service directory

### 502 Bad Gateway (NPM)
1. Target container is down or wrong port
2. Check: `docker ps` to verify the target is running
3. Check NPM proxy host config points to correct container:port

### DNS not resolving (Pi-hole)
1. Check Pi-hole is running: `docker ps | grep pihole`
2. Check upstream DNS: `docker exec pihole dig @127.0.0.1 google.com`
3. Restart: `docker restart pihole`

### Out of disk space (ENOSPC)
1. Check: `df -h /`
2. Quick fix: `docker system prune -f`
3. Better: `bash /opt/agentharness/scripts/cleanup.sh`

### LLM server not responding
1. Check: `curl -sf http://localhost:8080/health`
2. Check logs: `sudo journalctl -u llama-primary --no-pager -n 30`
3. Common: OOM (model too large for RAM), swap death
4. Fix: restart service or switch to smaller model

### Container OOM Killed
1. Check: `docker inspect CONTAINER | grep -i oom`
2. Check: `dmesg | grep -i oom | tail -5`
3. Fix: increase container memory limit or reduce workload
