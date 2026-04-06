# AgentHarness

Self-bootstrapping AI infrastructure for a homelab. Subordinate to Chaguli (OpenClaw agent on Telegram). Monitors services, optimizes LLM inference, auto-deploys repos, self-improves, and keeps the homelab running 24/7.

## Quick Start

```bash
# On your homelab:
git clone <this-repo> /opt/agentharness/repo
cd /opt/agentharness/repo

# Preview what will happen (no changes):
./install.sh --dry-run

# Install everything:
./install.sh

# If something breaks:
./install.sh --doctor
./install.sh --doctor-fix
./install.sh --phase=N    # re-run specific phase
```

## What It Does

### Discovery-First
Phase 0 scans your entire system — existing scripts, cron jobs, Docker configs, OpenClaw installation, API keys, running services — before touching anything. Augments existing automations, never replaces.

### Inference Engine Management
Builds both stock llama.cpp and ik_llama.cpp. Downloads recommended models (Qwen3.5-35B-A3B, 9B, Gemma 4). Benchmarks every model x engine combo. Auto-switches to the best configuration.

### Network-Aware Scheduling
Knows your wifi drops 11PM-7:15AM PT. Runs heavy tasks (benchmarks, cleanup, backups) during offline hours. Downloads, web searches, and syncs run during online hours. Alert queue holds notifications until wifi returns.

### Chaguli Integration
Generates OpenClaw SKILL.md files for every homelab service. Updates AGENTS.md with a managed section (preserves your existing content). As new services deploy, Chaguli automatically gains new capabilities.

### Self-Optimization
Weekly searches SearXNG for new models, tools, and techniques. Monthly re-benchmarks. Daily analyzes OpenClaw/Telegram interactions for failure patterns. Feeds learnings into Chaguli's persistent memory.

## Project Structure

```
install.sh                          # Single entry point
scripts/
  common.sh                         # Shared utilities
  scheduler.sh                      # Network-aware task router (every 15 min)
  registry_engine.py                # Plugin system — add checks/harnesses via YAML
  doctor.sh                         # Diagnose + auto-fix problems
  discover_automations.sh            # Find all existing scripts/services/cron
  discover_config.sh                 # Find all existing API keys/configs
  discover_storage.sh                # Find USB drives for backup
  service_registry.sh                # Discover service APIs/MCPs
  openclaw_sync.sh                   # Generate skills + update AGENTS.md
  build_inference.sh                 # Build llama.cpp + ik_llama.cpp
  download_models.sh                 # Download models by RAM budget
  benchmark.sh                       # Benchmark all combos, auto-switch
  monitor.sh                         # Health checks, alerts, morning briefings
  chaguli_memory.sh                  # Persistent memory store
  backup.sh                          # Nightly backup to USB drive
  cleanup.sh                         # Docker/package/log garbage collection
  daily_improve.sh                   # Analyze interactions, auto-fix
  weekly_optimize.sh                 # Search for new models/tools
  github_deploy.sh                   # Auto-install from GitHub URL
  security_audit.sh                  # Security boundary checks
  self_update.sh                     # Pull latest and apply changes
  setup_minipc.sh                    # Playbook for when mini PC arrives
  validate.sh                        # Post-install health check
config/
  harness_registry.yaml              # Pluggable checks and harnesses
  env.template                       # Environment config template
  openclaw_model_failover.jsonc      # LLM routing config for OpenClaw
  systemd/                           # LLM server service files
  searxng/                           # Self-hosted search engine
  skills/                            # OpenClaw skills for Chaguli
    agentharness-manage/             # Add checks/harnesses via Telegram
    chaguli-self-doc/                # "What can you do?"
    chaguli-content-curator/         # Proactive content discovery
    chaguli-error-explainer/         # Paste an error, get diagnosis
    chaguli-dashboard/               # On-demand homelab status
    chaguli-voice-notes/             # Voice message processing
```

## Extending

Three ways to add new capabilities (no script editing required):

**1. Edit `config/harness_registry.yaml`** — Add a YAML block for a new check or scheduled task.

**2. Use the CLI** — `python3 scripts/registry_engine.py add_check "name" --command "..." --type threshold --warn 80 --critical 90`

**3. Ask Chaguli** — "Start monitoring Jellyfin transcoding queue" via Telegram.

Custom scripts go in `/opt/agentharness/custom/`.

## Updating

```bash
# From the homelab:
bash /opt/agentharness/scripts/self_update.sh

# Or:
cd /opt/agentharness/repo && git pull
./install.sh --phase=8.5   # re-install skills/registry
```

## Hardware

**Current:** HP Laptop — Ryzen 4700U, 4+32GB DDR4, 256GB SSD, Debian + Docker
**Upcoming:** Mini PC — Ryzen 8745HS, 16GB DDR5, 780M iGPU. Run `scripts/setup_minipc.sh` when it arrives.
