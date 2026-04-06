---
name: agentharness-manage
description: Add, list, and manage AgentHarness monitoring checks and scheduled tasks
requires:
  binaries: ["python3"]
---

# AgentHarness Management

You can add new monitoring checks and scheduled tasks to AgentHarness without editing scripts.

## List All Checks and Harnesses

```bash
python3 /opt/agentharness/scripts/registry_engine.py list
```

## View Run Status

```bash
python3 /opt/agentharness/scripts/registry_engine.py status
```

## Add a New Monitor Check

When Rohit asks you to monitor something new:

```bash
python3 /opt/agentharness/scripts/registry_engine.py add_check "check_name" \
  --command "the shell command that outputs a number or text" \
  --type threshold \
  --warn 80 --critical 90 \
  --unit "%" \
  --message "Description: {value}%"
```

Check types:
- `threshold` — alert when output number exceeds warn/critical
- `command_output` — alert when command produces any output
- `http_probe` — alert when URL is unreachable
- `regex_match` — alert when output doesn't match expected pattern (use `--expected "pattern"`)
- `command_exit` — alert when command exits non-zero

### Examples

Monitor Jellyfin active streams:
```bash
python3 /opt/agentharness/scripts/registry_engine.py add_check "jellyfin_streams" \
  --command "curl -sf http://localhost:8096/Sessions | python3 -c \"import sys,json; print(len([s for s in json.load(sys.stdin) if s.get('NowPlayingItem')]))\"" \
  --type threshold --warn 3 --critical 5 \
  --message "Jellyfin has {value} active streams"
```

Monitor a specific container is running:
```bash
python3 /opt/agentharness/scripts/registry_engine.py add_check "immich_running" \
  --command "docker inspect --format '{{.State.Running}}' immich_server 2>/dev/null || echo false" \
  --type regex_match --expected "true" \
  --message "Immich server is not running!"
```

## Add a New Scheduled Task

When Rohit asks you to run something on a schedule:

```bash
python3 /opt/agentharness/scripts/registry_engine.py add_harness "task_name" \
  --script "path/to/script.sh" \
  --window offline \
  --frequency daily \
  --description "What this task does"
```

Windows: `offline` (11PM-7AM), `online` (7AM-11PM), `any`
Frequencies: `hourly`, `daily`, `weekly`, `monthly`, `3d`, `6h`, `30m`

### Example

Run a custom backup every 3 days:
```bash
python3 /opt/agentharness/scripts/registry_engine.py add_harness "custom_backup" \
  --script "/opt/agentharness/custom/my_backup.sh" \
  --window offline --frequency 3d \
  --description "Backup my custom data"
```

## Drop Custom Scripts

Place custom scripts in `/opt/agentharness/custom/` and reference them in add_harness.
They'll have access to all discovered paths via:
```bash
source /opt/agentharness/scripts/common.sh
source /opt/agentharness/.env
source /opt/agentharness/openclaw_paths.env
```
