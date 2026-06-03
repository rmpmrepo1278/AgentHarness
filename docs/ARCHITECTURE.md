# AgentHarness Architecture

## System Overview

AgentHarness is the LLM inference and context management layer for the homelab. It sits between clients (Claude Code, Telegram/Hermes) and LLM providers (OpenRouter, Groq, Cerebras, Google, local llama.cpp), routing requests intelligently while minimizing token consumption and handling provider failures gracefully.

## Core Components

### 1. LLM Proxy Server (`core/providers/proxy_server.py`)

FastAPI server on port 8080. Routes chat completions requests to the best available provider.

**Request Flow:**
```
Client → POST /v1/chat/completions
  → TokenJuice preprocessing (HTML→markdown, URL shortening, noise stripping)
  → Response cache check (skip if cached)
  → Rate limit filter (skip providers in cooldown)
  → Provider cascade: owl → laguna → qwen-coder → openrouter → groq → google → local
  → Response cache store
  → Return to client
```

**Key Endpoints:**
| Endpoint | Description |
|----------|-------------|
| `POST /v1/chat/completions` | Main chat endpoint (OpenAI-compatible) |
| `GET /v1/status` | Provider health, cooldowns, circuit breakers |
| `GET /v1/usage` | Per-provider daily usage stats |
| `GET /v1/cost` | Cost breakdown + OpenRouter credit |
| `GET /v1/reliability` | Provider reliability scores |
| `GET /v1/rate-limits` | Rate limit tracker observability (localhost only) |
| `GET /v1/token-juice` | TokenJuice preprocessing stats (localhost only) |
| `GET /v1/cache` | Response cache stats (localhost only) |
| `DELETE /v1/cache` | Clear response cache (localhost only) |
| `POST /v1/routing` | Runtime provider enable/disable/reset |
| `GET /v1/models` | Available models |
| `GET /health` | Health check |

### 2. TokenJuice Preprocessing (`core/providers/token_juice.py`)

Reduces token consumption by 40-80% on web-heavy requests.

**Pipeline:**
1. Content-hash LRU cache lookup (skip if already processed within TTL)
2. Extract and preserve `<table>`, `<math>`, `<svg>` as HTML fragments
3. HTML → Markdown conversion (in ProcessPoolExecutor, asyncio-safe)
4. URL shortening (strip tracking params)
5. Noise stripping (nav, ads, cookie banners)
6. Whitespace normalization
7. Restore preserved blocks AFTER tag removal

**Config (env vars):**
| Variable | Default | Description |
|----------|---------|-------------|
| `TJ_ENABLED` | `true` | Enable/disable preprocessing |
| `TJ_CACHE_SIZE` | `256` | Max cached entries |
| `TJ_CACHE_TTL_SECS` | `300` | Cache entry TTL |
| `TJ_TIMEOUT_SECS` | `5` | Max processing time per message |
| `TJ_PRESERVE_TABLES` | `true` | Keep tables as HTML |
| `TJ_PRESERVE_MATH` | `true` | Keep math as HTML |
| `TJ_PRESERVE_SVG` | `true` | Keep SVG as HTML |

### 3. Rate Limit Tracker (`core/providers/rate_limit_tracker.py`)

Per-provider, per-model rate limit state with failure scoring.

**Features:**
- Tracks ALL failure types: 429, 500, timeout, connection_refused, empty_response
- Per-model keys (e.g., `owl:openrouter/owl-alpha` ≠ `owl:claude-sonnet-4`)
- Transient error detection: if 3+ providers fail with connection_refused within 5s, it's a local network issue — no per-provider penalty
- All-down deadlock prevention: if ALL providers in cooldown for >300s, forcibly retries the healthiest
- Health score decay: failure scores decay over 600s so stale failures don't penalize forever
- Atomic file writes with `fcntl.flock` for concurrent worker safety

**Config (env vars):**
| Variable | Default | Description |
|----------|---------|-------------|
| `RL_COOLDOWN_THRESHOLD` | `2` | Consecutive failures before cooldown |
| `RL_BASE_COOLDOWN_SECS` | `120` | Initial cooldown duration |
| `RL_MAX_COOLDOWN_SECS` | `1800` | Max cooldown (exponential backoff) |
| `RL_ALL_DOWN_RETRY_SECS` | `300` | Force retry after all providers down |
| `RL_HEALTH_DECAY_SECS` | `600` | Failure score decay period |
| `RL_FAILURE_WEIGHTS` | `{"429":3,"500":2,"timeout":2,"connection_refused":1,"empty_response":1,"other_error":1}` | Per-type weights |

### 4. Context Harvester (`scripts/context_harvester.py`)

Background context accumulation for Hermes Memory. Runs every 20 minutes via cron.

**Harvest Sources:**
- Git commits (deduped by commit hash)
- File changes in watched directories (deduped by path+mtime)
- Terminal command history (user-configurable patterns)
- Docker container events (streaming with auto-reconnect)
- Health check alerts

**Feedback Loop:**
Before each cycle, reads `rate_limit_tracker.json`. If >50% of providers are in cooldown, enters "reduced mode" — skips terminal history and file changes, only keeps git commits and health alerts. Prevents adding token pressure during rate limits.

**Deduplication:**
- Git: last-seen commit hash per repo
- Files: last-seen mtime per path
- Docker: last event timestamp
- Terminal: last command timestamp

**Eviction:**
- Low-importance (< 0.5): evicted after `HARVEST_TTL_HOURS` (default 24h)
- High-importance (≥ 0.5): capped at 1000 per source, oldest evicted first

**Config (env vars):**
| Variable | Default | Description |
|----------|---------|-------------|
| `HARVEST_INTERVAL` | `20` | Minutes between cycles |
| `HARVEST_TTL_HOURS` | `24` | Low-importance observation TTL |
| `HARVEST_LOCKFILE` | `/tmp/context_harvester.lock` | Lock file path |
| `HARVEST_INTERESTING_PATTERNS` | (see source) | Regex for interesting commands |

### 5. Response Cache

LRU cache with TTL for identical requests. Keyed on hash of model + system prompt + last user message + tools + temperature + max_tokens.

**Endpoints:** `GET /v1/cache` (stats), `DELETE /v1/cache` (clear) — localhost only.

### 6. Provider Cascade

**Routing Order (free-first):**
1. `owl` — OpenRouter owl-alpha (free, 1M context)
2. `laguna` — OpenRouter laguna-m.1:free
3. `qwen-coder` — OpenRouter qwen3-coder:free
4. `openrouter` — OpenRouter qwen3-coder:free (fallback)
5. `groq` — Groq llama-3.3-70b-versatile (free tier)
6. `google-alt` — Google Gemini 2.0-flash (free tier)
7. `google-alt-2` — Google Gemini 2.5-flash (free tier)
8. `local` — Local Qwen2.5-7B (last resort)

**Per-request filtering:**
- CostGuard blocklist check
- TPM limit check (skip if request too large for provider)
- Rate limit cooldown check (per-model)
- Circuit breaker check
- Health probe check (3+ consecutive failures)
- Budget exhaustion check

### 7. Secrets Management

**Single source of truth:** `/home/rohit/.secrets/master.env` (symlinked by `agentharness/data/.env` and `.hermes/.env`)

**Vaultwarden:** TLS-enabled at `vaultwarden.local:8443`. Stores API keys as secure notes cipher. Auto-generates `.env` at boot via `vaultwarden-secrets.service`.

**Generation:** `make secrets-gen-env` or `python3 /home/rohit/.secrets/vault.py gen-env`

### 8. Regression Test Suite

**111 tests across 2 suites:**

| Suite | File | Tests | Time | Coverage |
|-------|------|-------|------|----------|
| Fast | `test_regression_suite.py` | 33 | ~35s | Proxy, routing, chat, tools, failover, probes, secrets, circuit breaker, cache, config, local LLM, Vaultwarden |
| Extended | `test_regression_extended.py` | 78 | ~15s | MCP gateway (12 servers), cron (29 jobs), n8n, backups, Hermes memory, log aggregation, rate limits, TokenJuice, context harvester, Docker volumes |

**Run:** `make test` (both suites) or `make test-all` (full unit suite)

**Enforcement:** Git pre-push hook blocks push on failure.

### 9. Cron Jobs (40 total)

| Frequency | Job | Purpose |
|-----------|-----|---------|
| Every 5 min | `health_check.sh` | System health monitoring |
| Every 5 min | `unified_cost_guard.py` | Cost tracking |
| Every 5 min | `export_systemd_status.sh` | Systemd status export |
| Every 10 min | `deadman_check.sh` | Dead man's switch |
| Every 20 min | `context_harvester.py` | Context accumulation |
| Every 30 min | `calendar_prep_watcher.py` | Calendar monitoring |
| Hourly | `document_intel.py` | Document intelligence |
| Every 3 hours | `proactive_quality_monitor.py` | Quality monitoring |
| Daily 11am | `daily_audit.py` | Daily audit |
| Daily 12pm | `autonomous_tier_engine.py` | Tier engine |
| Daily 1pm | `daily_research.py` | Research |
| Daily 6am,6pm | `autonomous_tier_engine.py` | Tier engine |
| Daily 7am | `cos_briefing.py` | Morning briefing |
| Daily 7:30am | `doc-expiry.py` | Document expiry check |
| Daily 8am | `birthday-reminder.py` | Birthday reminders |
| Daily 9am (Mon) | `home-maintenance.py` | Home maintenance |
| Daily 9am (Sun) | `personal_research_digest.py` | Research digest |
| Daily 10am | `proactive_priorities.py` | Priority management |
| Daily 2pm | `email_triage.py` | Email triage |
| Daily 5pm (Fri) | `gratitude-prompt.py` | Gratitude journal |
| Daily 8pm | `wellness-checkin.py` | Wellness check-in |
| Daily 9pm | `ei-checkin.py` | Emotional intelligence check-in |
| Daily 10pm | `stress-check.py` | Stress check |
| Daily 11pm | `commitment_tracker.py` | Commitment tracking |
| Daily 11pm | `session_debrief.sh` | Session debrief |
| Daily 11:30pm | `reflective_phase.sh` | Reflective phase |
| Weekly Sun 11am | `weekly_optimize.sh` | Weekly optimization |
| Weekly Sun 3am | `cve_monitor.sh` | CVE monitoring |
| Weekly Sun 3am | `state.db VACUUM` | Database maintenance |
| Weekly Sun 5am | `verify_backups.sh` | Backup verification |
| Weekly Wed 2pm | `youtube_pipeline.py` | YouTube pipeline |
| Weekly Sat 2pm | `claude-code` update | Claude update |
| Weekly Fri 5pm | `weekly_review.py` | Weekly review |
| Weekly Sun 8pm | `habit-review.py` | Habit review |
| Monthly 1st 8am | `monthly-finance.py` | Finance report |
| 3x/week | `networking_agent.py` | Networking |
| 2x/day | `autonomous_career_scan.py` | Career scan |
| Daily 8:50am | `autonomous_career_scan.py` | Career scan |
| Daily 7am | `bill-reminder.py` | Bill reminders |
| Daily 8:20am | `send_daily_digest.sh` | Daily digest |
| Daily 2:30am | `db_backup.sh` | Database backup |
| Daily 3pm | `sync_backup_remote.sh` | Remote backup sync |
| Daily 8pm | `sync_calibre_to_onedrive.sh` | Calibre sync |
| @reboot | `inbox_watcher_inotify.sh` | Inbox watcher |
| @reboot | `start_llm_server.sh` | LLM server startup |

### 10. Monitoring Stack

| Service | Port | Purpose |
|---------|------|---------|
| Grafana | 3002 | Dashboards |
| Loki | 3100 | Log aggregation |
| Promtail | — | Log shipping |
| Netdata | 19999 | System metrics |
| Uptime Kuma | — | Service uptime |
| Agent Status API | 3010 | Agent health |

### 11. Self-Heal System

| Component | Description |
|-----------|-------------|
| `doctor_check.py` | Diagnose + auto-fix problems |
| `deadman_check.sh` | Dead man's switch (10 min) |
| `health_check.sh` | System health (5 min) |
| `circuit_breaker` | Suppress repeated alerts, auto-reset on recovery |
| `watchdog` | 5-minute heartbeat, restarts if stale |
| `autoheal` | Docker container auto-restart |
| `network-watchdog` | Network connectivity monitoring |

## File Layout

```
agentharness/
├── core/
│   ├── providers/
│   │   ├── proxy_server.py      # Main proxy (FastAPI, port 8080)
│   │   ├── token_juice.py       # HTML preprocessing
│   │   ├── rate_limit_tracker.py # Per-model rate limit state
│   │   ├── router.py            # Smart LLM routing
│   │   ├── budget.py            # Budget tracking
│   │   ├── billing.py           # Cost tracking
│   │   └── [provider].py        # Per-provider implementations
│   ├── resilience/              # Circuit breaker, watchdog, self-test
│   └── security/                # Sanitizer, audit, integrity
├── scripts/
│   ├── context_harvester.py     # Background context accumulation
│   ├── health_check.sh          # System health monitoring
│   ├── deadman_check.sh         # Dead man's switch
│   ├── db_backup.sh             # Database backup
│   └── [40+ other scripts]      # Various automation
├── tests/
│   ├── test_regression_suite.py # 33 fast proxy tests
│   ├── test_regression_extended.py # 78 infrastructure tests
│   └── test_*.py                # 52+ unit test files
├── docs/
│   ├── ARCHITECTURE.md          # This file
│   └── HOMELAB_MAP.md           # Service map
├── Makefile                     # test, secrets, proxy commands
└── data/
    ├── .env                     # Symlink to master secrets
    ├── rate_limit_state.json    # Persistent rate limit state
    ├── claudemem_harvest_state.json # Harvester dedup state
    └── logs/                    # All service logs
```
