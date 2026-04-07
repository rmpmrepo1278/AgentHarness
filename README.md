# AgentHarness

Self-healing infrastructure agent framework for homelabs. Subordinate to Chaguli (OpenClaw agent). Discovers your system, monitors services, manages LLM inference, and keeps everything running 24/7.

## Quick Start

```bash
git clone <repo> ~/agentharness
cd ~/agentharness
python3 cli.py discover    # Find everything on this machine
python3 cli.py status      # Show what was found
python3 cli.py selftest    # Verify the system works
./install.sh               # Full install with inference engines
```

Default install location: `$HOME/agentharness`. No root required for core functionality.

## What It Does

**Discovery-first.** Phase 0 probes your system — running services, Docker containers, cron jobs, API keys, USB drives, existing scripts — before touching anything. No hardcoded paths. Augments what exists, never replaces.

**Modular bundles.** Functionality is split into installable bundles:
- `core` — discovery, scheduling, health checks, CLI
- `homelab` — service monitoring, Docker cleanup, Chaguli integration
- `inference` — build inference engines, download models, benchmark
- `security` — hardening, audit trail, integrity verification
- `backup` — nightly backup to USB, config backup/restore

**Network-aware scheduling.** Knows when connectivity drops (11PM-7:15AM PT). Queues alerts, defers downloads, runs heavy tasks (benchmarks, cleanup, backups) during offline hours.

**Agent integration.** Generates OpenClaw SKILL.md files for discovered services. Updates Chaguli's capabilities as new services deploy.

## Resilience

- **Auto-restart** — systemd service with restart-on-failure
- **Watchdog** — 5-minute heartbeat check, restarts if stale
- **Crash-safe queues** — atomic JSON writes (write-tmp-rename pattern)
- **Circuit breaker** — suppresses repeated alerts for the same failure, auto-resets on recovery
- **Startup self-test** — validates config, permissions, and dependencies before running
- **Config backup/restore** — snapshots config state, rollback on corruption
- **Log rotation** — configurable retention, prevents disk fill

## Security

- **Input sanitization** — blocks shell injection in all external inputs
- **Exec audit trail** — JSONL log of every command executed, with secret redaction
- **File integrity verification** — SHA-256 manifest of core files, detects tampering
- **Container isolation** — community bundles run in Docker, not on the host

## Project Structure

```
cli.py                          # CLI entry point
install.sh                      # Full installer (phased)
core/
  discovery/                    # System probing (services, storage, config, Chaguli)
  registry/                     # Bundle and check registry
  resilience/                   # Watchdog, circuit breaker, crash-safe queue, self-test
  security/                     # Sanitizer, audit logger, integrity checker
bundles/
  core/                         # Core monitoring checks
  homelab/                      # Docker, service monitoring
  inference/                    # LLM engine build + model management
  security/                     # Hardening and audit checks
  backup/                       # USB backup, config snapshots
  community/                    # Sandboxed third-party bundles
  dashboard/                    # Status dashboard
config/
  harness_registry.yaml         # Check and harness definitions
  env.template                  # Environment config template
  systemd/                      # Service files
  logrotate/                    # Log rotation config
  searxng/                      # Self-hosted search engine config
scripts/
  common.sh                     # Shared shell utilities
  scheduler.sh                  # Network-aware task router
  registry_engine.py            # Registry management (Python)
  doctor.sh                     # Diagnose + auto-fix problems
  discover_automations.sh       # Find scripts, services, cron jobs
  discover_config.sh            # Find API keys, configs
  discover_storage.sh           # Find USB drives for backup
  discover_chaguli.sh           # Find Chaguli/OpenClaw installation
  build_inference.sh            # Build llama.cpp + ik_llama.cpp
  download_models.sh            # Download models by RAM budget
  benchmark.sh                  # Benchmark model+engine combos
  self_update.sh                # Pull latest and apply
  validate.sh                   # Post-install health check
```

## CLI Reference

```
python3 cli.py status           # Current system status
python3 cli.py discover         # Run full discovery
python3 cli.py health           # Run health checks
python3 cli.py bundle list      # List installed bundles
python3 cli.py selftest         # Startup self-test
python3 cli.py circuits         # Show open circuit breakers
python3 cli.py audit            # Show recent audit log entries
python3 cli.py integrity        # Verify file integrity (SHA-256)
```

## Extending

**Add a check via bundle YAML.** Create a YAML file in `bundles/<name>/checks.yaml` defining the check name, command, type (threshold/boolean/pattern), and alert thresholds. The registry engine picks it up automatically.

**Add via CLI.** `python3 scripts/registry_engine.py add_check "name" --command "..." --type threshold --warn 80 --critical 90`

**Install a community bundle.** Drop it into `bundles/community/`. Community bundles run inside Docker containers — they cannot access the host filesystem directly.

## Hardware

**Current:** HP Laptop — Ryzen 4700U, 4+32GB DDR4, 256GB SSD, Debian + Docker

**Planned:** Mini PC — Ryzen 8745HS, 16GB DDR5, 780M iGPU. Two-machine distributed inference via exo. Run `scripts/setup_minipc.sh` when it arrives.

## Updating

```bash
# Automatic:
bash scripts/self_update.sh

# Manual:
git pull
./install.sh
```
