# Homelab Doctor — Design Spec

**Date:** 2026-04-10
**Status:** Approved (brainstorm complete)
**Author:** Claude Code + Rohit

## Problem Statement

When the homelab breaks (misconfigured service, crashed LLM server, disk pressure, breaking engine update), Rohit currently depends on Claude Code to diagnose and fix it — copy-pasting outputs between a remote Mac and the homelab terminal. This is not sustainable. The homelab needs to diagnose and repair itself autonomously, with Rohit notified after the fact.

Today's incident: `llama-primary` systemd service had 4 bugs (wrong port, wrong model, wrong user, broken sandbox). The LLM was dead for 3 days. Chaguli showed "LLM is offline" but couldn't fix it.

## Design Decisions (from brainstorm)

1. **Fully autonomous (Mode A)** — fixes happen without asking. Rohit sees a Telegram notification after the fact.
2. **Full autonomy with guardrails (Mode C)** — engine can do anything but must snapshot before destructive actions and log everything.
3. **Lives in AgentHarness (Architecture C)** — autonomous mode doesn't depend on Chaguli being alive. Interactive mode via Chaguli `/doctor` command. Runbook files shared.
4. **Hybrid runbook format (Format B)** — deterministic YAML decision trees for known failures, LLM interpretation for novel/unexpected output.
5. **Notifications (Mode C)** — critical alerts direct to Telegram (bypasses Chaguli), FYI through Chaguli inbox.
6. **Deterministic first, LLM as fallback** — shell commands don't need an LLM. Only use LLM to interpret unexpected log output.

## Architecture

```
+------------------------------------------------------+
|                    Telegram                           |
+-------+----------------------+-----------------------+
        | critical alerts      | FYI (batched)
        | (direct bot)         | (via Chaguli inbox)
        v                      v
+----------------+    +-----------------------------+
| alert.sh       |    | Chaguli (OpenClaw)          |
| (direct TG)    |    | /doctor command             |
+----------------+    | interactive mode            |
        ^              +-------------+--------------+
        |                            |
+-------+----------------------------+-----------------+
|           AgentHarness - Doctor Engine                |
|                                                      |
|  +------------+  +------------+  +----------------+  |
|  | Watchdog   |  | Registry   |  | Runbook        |  |
|  | (existing) +->| Engine     +->| Executor       |  |
|  | detects    |  | (existing) |  | (NEW)          |  |
|  +------------+  | alerts     |  |                |  |
|                  +------------+  | +------------+ |  |
|                                  | | YAML       | |  |
|  +------------+                  | | runbooks   | |  |
|  | Scheduler  |                  | +------------+ |  |
|  | (existing) +----------------->|                |  |
|  | triggers   |                  | +------------+ |  |
|  +------------+                  | | LLM        | |  |
|                                  | | fallback   | |  |
|  +------------+                  | +------------+ |  |
|  | Snapshot   |<-----------------+                |  |
|  | Manager    |  (before fixes)  +----------------+  |
|  | (NEW)      |                                      |
|  +------------+                                      |
|                                                      |
|  +------------------------------------------------+  |
|  | Service Registry (extends harness_registry)    |  |
|  | health endpoints, priorities, runbook refs     |  |
|  | auto-discovered from docker ps + systemd       |  |
|  +------------------------------------------------+  |
+------------------------------------------------------+
```

## Components

### 1. Runbook Executor — `core/doctor/engine.py` (NEW, ~300 lines)

The core execution engine. Reads YAML runbooks and executes them step by step.

**Responsibilities:**
- Load and validate runbook YAML files
- Execute steps sequentially: check -> diagnose -> snapshot -> fix -> verify
- Handle branching: on_fail, on_known, on_unknown
- Delegate to LLM for `interpret: llm` steps
- Enforce locking: one runbook per service at a time (prevents concurrent fixes)
- Log every action to `data/doctor_log.jsonl`
- Report results to notification router

**Key Functions (reusing existing patterns):**
```python
class RunbookExecutor:
    def __init__(self, data_dir: str, runbooks_dir: str)
    
    def execute(self, runbook_name: str, trigger_context: dict = None) -> RunbookResult
        """Execute a named runbook. Returns structured result."""
    
    def execute_step(self, step: dict, context: dict) -> StepResult
        """Execute one step: run check command, evaluate, branch."""
    
    def dry_run(self, runbook_name: str) -> list[StepResult]
        """Walk through all steps without executing fixes. Validates preconditions."""
    
    def list_runbooks(self) -> list[dict]
        """Return metadata for all available runbooks."""
```

**Reuses:**
- `registry_engine.run_command(command, timeout)` for shell execution
- `CircuitBreaker` for tracking repeated failures per service
- `DiagnosticCollector` for gathering LLM context when `interpret: llm`
- `AutoFixer._call_llm()` pattern for LLM routing
- `atomic_write_json` for safe state persistence

**Execution Lock:**
- File-based lock per service: `data/doctor_locks/{service_name}.lock`
- Contains PID + timestamp
- Stale lock recovery reuses `watchdog.recover_stale_lock()`

### 2. Snapshot Manager — `core/doctor/snapshot.py` (NEW, ~100 lines)

Captures file state before destructive/config actions.

**Responsibilities:**
- Copy target file to `data/snapshots/{filename}.bak.{timestamp}`
- Track snapshots in `data/snapshots.json` (file, timestamp, runbook that triggered it)
- Cleanup policy: keep last 5 snapshots per file, delete older
- Provide rollback: restore a snapshot by name

**Key Functions:**
```python
class SnapshotManager:
    def __init__(self, data_dir: str, max_per_file: int = 5)
    
    def snapshot(self, file_path: str, runbook_name: str) -> str
        """Create snapshot. Returns snapshot path."""
    
    def rollback(self, file_path: str, snapshot_id: str = "latest") -> bool
        """Restore a snapshot. Defaults to most recent."""
    
    def cleanup(self) -> int
        """Remove snapshots beyond max_per_file. Returns count removed."""
    
    def list_snapshots(self, file_path: str = None) -> list[dict]
        """List all snapshots, optionally filtered by file."""
```

### 3. Notification Router — `core/doctor/notify.py` (NEW, ~80 lines)

Routes notifications based on severity tier.

**Tiers:**
- **Silent** — log to `data/doctor_log.jsonl` only. Used for: routine restarts, cache clears.
- **FYI** — write to Chaguli inbox (batched). Used for: fixed issues worth knowing, resource cleanup summaries.
- **Critical** — direct Telegram via `alert.sh`. Used for: fix failed after 3 attempts, manual intervention needed, data-affecting changes.

**Key Functions:**
```python
class NotificationRouter:
    def __init__(self, data_dir: str, chaguli_inbox_dir: str, alert_script: str)
    
    def notify(self, level: str, title: str, body: str, runbook: str = None) -> None
        """Route notification to appropriate channel."""
    
    def send_critical(self, title: str, body: str) -> None
        """Direct to Telegram via alert.sh."""
    
    def send_fyi(self, title: str, body: str) -> None
        """Write to Chaguli inbox for batched relay."""
    
    def log_silent(self, title: str, body: str) -> None
        """Log only, no external notification."""
```

**Reuses:**
- Existing `scripts/alert.sh` for Telegram delivery
- `ChaguliBridge.send_insight()` pattern for inbox writes

### 4. Service Registry Extensions — extends `config/harness_registry.yaml`

Add per-service metadata that the runbook executor needs:

```yaml
checks:
  llm_server:
    enabled: true
    command: "curl -sf --max-time 5 http://localhost:8080/health"
    type: http_probe
    message: "Primary LLM server not responding"
    # NEW fields:
    priority: 1          # 1=highest, restart before lower priority services
    runbook: llm-server-offline    # which runbook to trigger on failure
    restart_cmd: "sudo systemctl restart agentharness-llm-proxy"
    
  llm_local:
    enabled: true
    command: "curl -sf --max-time 5 http://localhost:8081/health"
    type: http_probe
    message: "Local LLM engine not responding"
    priority: 1
    runbook: llm-server-offline
    restart_cmd: "sudo systemctl restart llama-primary"
```

**Auto-discovery extension:**
- On each discovery run (`cli.py discover`), also scan:
  - `docker ps --format json` for container health endpoints
  - `systemctl list-units --type=service --state=running` for system services
- New services get a default entry in registry with `runbook: generic-service-restart`
- Generates/updates Mermaid architecture diagrams from current state

### 5. Sudoers Configuration — `config/sudoers.d/agentharness`

Passwordless sudo for specific repair commands only:

```
# AgentHarness Doctor — limited sudo for autonomous repair
rohit ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart llama-primary
rohit ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart agentharness-*
rohit ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart llama-fast
rohit ALL=(ALL) NOPASSWD: /usr/bin/systemctl daemon-reload
rohit ALL=(ALL) NOPASSWD: /usr/bin/kill
rohit ALL=(ALL) NOPASSWD: /usr/bin/cp * /etc/systemd/system/*
```

**Install via:** `sudo cp config/sudoers.d/agentharness /etc/sudoers.d/agentharness && sudo chmod 440 /etc/sudoers.d/agentharness`

### 6. Chaguli /doctor Command — (addition to Chaguli agent.py)

Interactive mode for on-demand diagnosis:

```
User: /doctor
Chaguli: Running diagnostics...
  - Proxy (8080): OK
  - Local LLM (8081): OK
  - Disk: 47GB free (78%)
  - RAM: 18GB/36GB used
  - Docker: 38/38 containers running
  - Circuit breakers: none open
  All systems nominal.

User: restart llama
Chaguli: Restarting llama-primary...
  - Snapshot: saved llama-primary.service
  - Executed: sudo systemctl restart llama-primary
  - Verify: health check passed after 12s
  Done. LLM server back online.
```

**Implementation:** Calls `RunbookExecutor` via the existing `ChaguliBridge` mechanism or direct Python import (since both are on the same machine).

## Runbook Format Specification

```yaml
# Required fields
name: string              # Unique identifier (matches filename without .yaml)
version: integer          # For tracking changes
trigger: string           # What activates this runbook
                          # Formats: health_check_fail:{check_name}
                          #          manual
                          #          circuit_open:{check_name}
                          #          on_schedule:{frequency}

# Optional metadata
priority: string          # "critical" | "high" | "medium" | "low"
notify: string            # "critical" | "fyi" | "silent" (default notification level)
description: string       # Human-readable purpose
tags: list[string]        # For filtering/searching

# Execution
steps:
  - name: string          # Step description (logged)
    
    # One of these action types:
    check: string         # Shell command to evaluate current state
    fix: string           # Shell command to apply a repair
    snapshot: string      # File path to backup before proceeding
    wait: integer         # Seconds to pause (e.g., for service startup)
    
    # For check steps — evaluation:
    expect_contains: string    # Output must contain this substring
    expect_exit_code: integer  # Expected exit code (default: 0)
    expect_regex: string       # Output must match this regex
    
    # For check steps — branching:
    on_fail:              # What to do if check fails
      - fix: string       # Inline fix steps
      - verify: string    # Re-check after fix
        on_fail:
          escalate: string    # Give up, notify human
          # OR
          runbook: string     # Chain to another runbook
    
    # LLM interpretation (for unknown output):
    interpret: llm        # Feed output to local LLM
    on_known:             # Pattern matching on LLM-interpreted output
      "pattern": string   # runbook:{name} or inline fix
    on_unknown:
      escalate: bool      # Notify human with LLM's analysis
      llm_diagnose: bool  # Ask LLM to propose fix steps
```

## Runbooks to Ship

### llm-server-offline.yaml
**Trigger:** `health_check_fail:llm_server` or `health_check_fail:llm_local`

Steps:
1. Check proxy health (curl 8080) -> restart proxy if down -> verify
2. Check local LLM health (curl 8081) -> restart llama-primary if down -> wait 15s -> verify
3. If restart fails: read journalctl logs -> LLM interpret
   - Known: "port in use" -> chain to port-conflict runbook
   - Known: "out of memory" -> chain to free-memory runbook
   - Known: "model not found" -> chain to model-missing runbook
   - Unknown: escalate with LLM analysis
4. End-to-end test: POST to proxy with test prompt -> verify response has "choices"

### container-crashed.yaml
**Trigger:** `health_check_fail:docker_crashed`

Steps:
1. List exited containers
2. For each: check exit code
   - 137 (OOM killed): chain to free-memory, then restart
   - 143 (SIGTERM): just restart
   - Other: read logs -> LLM interpret
3. Restart container
4. Verify health endpoint (if registered)

### disk-pressure.yaml
**Trigger:** `health_check_fail:disk_usage` (threshold > 80%)

Steps:
1. Check current usage
2. Prune Docker images: `docker image prune -f`
3. Prune Docker build cache: `docker builder prune -f`
4. Rotate logs: compress files > 7 days in data/logs/
5. Check Docker volumes: prune volumes not attached to running containers
6. Re-check usage
7. If still > 85%: escalate (needs human to decide what to delete)

### ram-pressure.yaml
**Trigger:** `health_check_fail:ram_usage` (threshold > 90%)

Steps:
1. Check current RAM breakdown
2. Identify largest non-essential processes
3. Stop lowest-priority services per priority list
4. Clear filesystem caches: `sync && echo 3 > /proc/sys/vm/drop_caches`
5. Drain swap if active: restart swapped services
6. Re-check RAM
7. If still critical: escalate

### port-conflict.yaml
**Trigger:** Chained from other runbooks

Steps:
1. `ss -tlnp | grep {port}` to find conflicting process
2. If it's a known service on wrong port: fix config, restart
3. If unknown process: kill it, verify port free, restart intended service

### free-memory.yaml
**Trigger:** Chained from other runbooks

Steps:
1. Get process list sorted by RSS
2. Check priority list
3. Stop lowest-priority container/service
4. Verify memory freed
5. Restart original service

### service-wont-start.yaml
**Trigger:** Generic, chained when any systemd restart fails

Steps:
1. Read `journalctl -u {service} --since '5 min ago'`
2. Check common issues:
   - "Permission denied" -> fix ownership/permissions
   - "Address already in use" -> chain to port-conflict
   - "No such file" -> check paths, fix symlinks
   - "NAMESPACE" (sandbox error) -> check ProtectSystem settings
3. If none match: LLM interpret logs
4. Escalate if still failing

### api-key-expired.yaml
**Trigger:** `health_check_fail:cloud_provider_401`

Steps:
1. Identify which provider returned 401
2. Test key directly with minimal API call
3. If expired: notify critical (can't auto-fix API keys)
4. If working: problem is elsewhere, check request format

### chaguli-down.yaml
**Trigger:** `health_check_fail:chaguli_container`

Steps:
1. Check container state: `docker inspect chaguli`
2. If exited: `docker restart chaguli`
3. Wait 10s, verify health
4. If OOM: check container memory limit, restart with lower model
5. If config error: read logs -> LLM interpret
6. Notify via direct Telegram (can't use Chaguli to report Chaguli is down)

### network-offline.yaml
**Trigger:** Unexpected offline state during business hours

Steps:
1. Check interface: `ip link show`
2. Check DNS: `dig google.com`
3. Check gateway: `ping -c1 $(ip route | grep default | awk '{print $3}')`
4. If interface down: `sudo ip link set {iface} up`
5. If DNS only: restart systemd-resolved
6. Re-check
7. If still offline: log and wait (might be ISP outage)

### model-missing.yaml
**Trigger:** Chained from llm-server-offline

Steps:
1. Check if model file exists at configured path
2. If not: check ~/models/ for the file (maybe path changed)
3. If found elsewhere: update service file with correct path
4. If truly missing: list available models, pick best alternative, update config
5. Restart service with updated config

## Integration Points

### Watchdog -> Runbook Engine (trigger on failure)

Modify `core/scheduler/scheduler.py` tick() method:

When a check fails and has a `runbook` field in its registry entry:
1. Check if runbook is already running (lock exists)
2. If not, execute runbook via `RunbookExecutor.execute()`
3. Record result in doctor_log.jsonl

This is the primary autonomous trigger — no new scheduler or loop needed.

### Registry Engine -> Runbook Engine (alert-triggered)

Modify `scripts/registry_engine.py` run_checks():

When alert would be sent and check has `runbook` field:
1. Attempt runbook execution before sending alert
2. If runbook succeeds: send FYI instead of alert
3. If runbook fails: send original alert + runbook failure details

### Chaguli -> Runbook Engine (interactive)

Via MCP tool or direct import:
- `/doctor` calls `RunbookExecutor.list_runbooks()` + runs all in check-only mode
- Natural language commands ("restart llama") mapped to specific runbooks
- Results formatted and sent back via Telegram

### Discovery -> Diagrams (auto-generated docs)

Extend `cli.py discover` (or new `core/doctor/diagrams.py`):
- After discovery, generate Mermaid markdown from state
- Services, ports, Docker networks, data flow
- Write to `docs/architecture.md`
- Commit to git if changed

## Priority List (for RAM pressure decisions)

```yaml
# config/service_priorities.yaml
# Lower number = higher priority = last to be killed
priorities:
  1: llama-primary        # LLM must stay up for all agents
  2: agentharness-llm-proxy  # Routes LLM traffic
  3: agentharness-scheduler  # Runs health checks + doctor
  4: chaguli               # Personal assistant
  5: searxng               # Search (nice to have)
  6: career-ops            # Future service
  7: new-agent             # Future powerful agent
  # Docker containers not listed default to priority 10
```

## Offline Window Awareness

The scheduler already detects online/offline/lan_only windows. Runbooks inherit this:

- Runbooks with `fix` commands that need internet (apt install, docker pull, API calls) are tagged:
  ```yaml
  requires_network: true
  ```
- If triggered during offline window: queue for next online window
- Queued runbooks stored in `data/doctor_queue.json`
- On next online-window tick: drain queue

## Logging & Audit

All actions logged to `data/doctor_log.jsonl`:
```json
{
  "timestamp": "2026-04-10T03:14:00Z",
  "runbook": "llm-server-offline",
  "trigger": "health_check_fail:llm_local",
  "steps_executed": 3,
  "steps_passed": 2,
  "steps_failed": 1,
  "fix_applied": "sudo systemctl restart llama-primary",
  "snapshot_created": "/data/snapshots/llama-primary.service.bak.1775833529",
  "result": "fixed",
  "notify_level": "fyi",
  "duration_seconds": 27,
  "llm_used": false
}
```

## Testing

### Dry-run mode
```bash
python3 -m core.doctor.engine --dry-run llm-server-offline
```
Walks through all steps, checks preconditions, reports what would happen without executing fixes.

### Simulated failure testing
Each runbook should be testable by simulating its trigger:
```bash
# Stop llama to simulate failure, then run the runbook
sudo systemctl stop llama-primary
python3 -m core.doctor.engine llm-server-offline
# Should: detect failure, restart, verify, notify
```

## File Structure

```
core/doctor/
  engine.py              # Runbook executor (~300 lines) — NEW
  snapshot.py            # Snapshot manager (~100 lines) — NEW
  notify.py              # Notification router (~80 lines) — NEW
  diagrams.py            # Mermaid diagram generator (~150 lines) — NEW
  runbooks/
    llm-server-offline.yaml
    container-crashed.yaml
    disk-pressure.yaml
    ram-pressure.yaml
    port-conflict.yaml
    free-memory.yaml
    service-wont-start.yaml
    api-key-expired.yaml
    chaguli-down.yaml
    network-offline.yaml
    model-missing.yaml
  troubleshoot.py        # EXISTING — rules migrate to YAML over time
  diagnose.py            # EXISTING — reused by engine for LLM context
  autofix.py             # EXISTING — reused for LLM fallback
  smoketest.py           # EXISTING — unchanged
  validate_remote.py     # EXISTING — unchanged
config/
  harness_registry.yaml  # EXTENDED with priority, runbook, restart_cmd fields
  service_priorities.yaml # NEW — priority ordering for resource pressure
  sudoers.d/
    agentharness         # NEW — passwordless sudo rules
```

## Migration Path

1. **Phase 1:** Build engine.py, snapshot.py, notify.py
2. **Phase 2:** Write the 11 runbooks
3. **Phase 3:** Wire scheduler to trigger runbooks on check failure
4. **Phase 4:** Add /doctor command to Chaguli
5. **Phase 5:** Add diagram generation
6. **Phase 6:** Migrate troubleshoot.py rules to YAML runbooks (gradual)

## Scalability

**Adding a new service:**
1. Register in `harness_registry.yaml` with health check + runbook reference
2. Write a service-specific runbook (or use `generic-service-restart`)
3. Add to `service_priorities.yaml`
4. Done — engine picks it up on next scheduler tick

**Adding a new agent (e.g., powerful ops agent):**
- Same as any Docker container — health check + runbook
- Agent's broad permissions don't affect the doctor engine
- Doctor monitors the agent, not the other way around
- If agent goes rogue (OOM, disk fill), doctor intervenes per runbook

**Adding a new failure mode:**
- Write a new YAML runbook
- Reference it from the relevant check in harness_registry.yaml
- Or let existing runbooks chain to it via `runbook:` references
- No code changes needed
