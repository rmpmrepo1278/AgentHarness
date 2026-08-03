
# IMPORTANT: This homelab prefers Telegram-first operation.
# All ops tasks (deploy, fix, status, optimize) have MCP tools accessible via Hermes Telegram bot.
# Do NOT use opencode/SSH unless the MCP tool fails. Always prefer Telegram workflow.

# CLAUDE.md — AgentHarness Development Guide

## Code Review Graph (Pre-Task Analysis)

**Always query the code graph BEFORE making changes:**

| Before... | Query... | Why... |
|-----------|----------|--------|
| Modifying a function/class | `code-review-graph query callers_of <name>` | Know who you'll break |
| Deleting a file | `code-review-graph query dependents_of_file <file>` | Verify no one depends on it |
| Adding new code | `code-review-graph search "<topic>"` (semantic) | Reuse existing patterns, avoid duplication |
| Refactoring | `code-review-graph architecture` | See community boundaries |
| Before commit | `code-review-graph dead-code` | Remove truly unused code |

**Quick commands:**

```bash
# In repo root (auto-detected by git)
code-review-graph status          # Graph health (nodes, edges, last build)
code-review-graph dead-code       # 88 dead items found in agentharness
code-review-graph query callers_of <func>  # Who calls this?
code-review-graph query callees_of <func>  # What does this call?
code-review-graph search "proxy server"     # Semantic search
code-review-graph impact --files <files>    # Impact analysis (space-separated)
code-review-graph architecture           # Module structure (11 communities)
```

**Bridge endpoints (from any tool/mcp):**
- `http://127.0.0.1:9199/code-graph/status`
- `http://127.0.0.1:9199/code-graph/dead-code`
- `http://127.0.0.1:9199/code-graph/query` (POST: `{"type":"callers_of","target":"..."})
- `http://127.0.0.1:9199/code-graph/search` (POST: `{"q":"..."})
- `http://127.0.0.1:9199/code-graph/impact` (POST: `{"files":"..."}`)
- `http://127.0.0.1:9199/code-graph/architecture`

**Rule:** If `impact` or `callers_of` returns significant results, list them before proceeding.

## Telegram-First Graph Workflow

When working remotely via Telegram, use the Hermes bot for all graph queries instead of SSH:

| Telegram command | What it does |
|-----------------|--------------|
| `/graph` | CRG status (nodes, edges, files, last build) |
| `/graphify path <a> <b>` | Shortest AST path between two symbols |
| `/graphify explain <name>` | Plain-language explanation of a node |
| `/graphify diagnose` | Multi-graph edge collapse risk report |
| `/code-graph query callers_of <func>` | Who calls this function? |
| `/code-graph query callees_of <func>` | What does this call? |
| `/code-graph search "<topic>"` | Semantic search across the graph |
| `/code-graph impact --files <files>` | Blast-radius analysis |
| `/code-graph architecture` | Module structure and communities |
| `/code-graph dead-code` | Dead code report |

**Rule:** If working via Telegram, prefer `/graphify` for quick lookups (faster, lighter) and `/code-graph` for deep analysis. Use CLI only when on homelab directly.


##

## First Run (Every Session)

1. **Check proxy health:** `curl -s http://localhost:8080/health | python3 -m json.tool`
2. **Check provider status:** `curl -s http://localhost:8080/v1/status | python3 -m json.tool`
3. **Run tests if code changed:** `make test`

## Forbidden Actions

- **NEVER push without running tests first** — pre-push hook enforces this
- **NEVER commit secrets** — API keys are in `~/.secrets/master.env` (symlinked, gitignored)
- **NEVER delete `data/.env`** — it's a symlink to master secrets; all 6 provider keys vanish silently
- **NEVER disable the pre-push hook** — `git push --no-verify` is blocked by policy
- **NEVER hardcode model names** — use CostGuard's `models.json` as single source of truth
- **NEVER use signal.alarm() in async code** — use `asyncio.wait_for` + `run_in_executor` instead

## Mandatory Post-Change Workflow

After ANY code change:

1. **Run tests:** `make test` (111 regression tests, ~50s)
2. **Update documentation** if behavior changed (README, ARCHITECTURE.md, HOMELAB_MAP.md)
3. **Update SOPs** if log paths, health checks, or alert triggers changed
4. **Verify end-to-end** — test the affected paths directly (not just unit tests)
5. **Run** `git status` — confirm no uncommitted changes remain
6. **Commit + push** — pre-push hook runs tests automatically

## Architecture

### AgentHarness LLM Proxy (port 8080)
- `core/providers/proxy_server.py` — FastAPI server
- Request flow: TokenJuice → Response cache → Rate limit filter → Provider cascade
- Direct free-tier providers + local fallback, free-first ordering
- Per-model rate limit tracking with persistent state (`data/rate_limit_state.json`)
- Model routing: `deepseek/deepseek-v4-flash` is force-routed to the `deepseek-v4-flash` provider (OpenRouter `:free`). If OpenRouter returns 429 (free-tier daily cap), the router falls through to the next provider. Check `/v1/status` for remaining daily quota.
- `/v1/status` reports `type: local` for providers with localhost/127.0.0.1 endpoints (e.g. `local`, `local-bmoe`) and `cloud` otherwise.

### TokenJuice (`core/providers/token_juice.py`)
- HTML→markdown conversion in ProcessPoolExecutor (asyncio-safe)
- LRU content-hash cache with TTL (default 300s)
- Preserves tables/math/SVG as HTML fragments
- Timeout (default 5s) with pass-through fallback — never blocks requests

### Rate Limit Tracker (`core/providers/rate_limit_tracker.py`)
- Per-provider-model keys (not just per-provider)
- All failure types: 429, 500, timeout, connection_refused, empty_response
- Transient error detection: 3+ providers failing simultaneously = network issue
- All-down deadlock: forcible retry after 300s of all providers down
- Health score decay: 600s half-life
- Atomic file writes with fcntl locking

### Context Harvester (`scripts/context_harvester.py`)
- Runs every 20 min via cron
- Sources: git, files, terminal, docker events (streaming + reconnect), health
- Dedup via last-seen markers (commit hash, mtime, timestamp)
- Feedback loop: reads rate limiter, reduces intensity under pressure
- Eviction: TTL (24h) for low-importance, cap (1000/source) for high-importance
- File lock prevents cron overlap

### Secrets Management
- Master: `~/.secrets/master.env`
- Symlinked: `agentharness/data/.env` → master, `~/.hermes/.env` → master
- Vaultwarden TLS: `vaultwarden.local:8443`
- Auto-generate at boot: `/etc/systemd/system/vaultwarden-secrets.service`
- Verify: `make secrets-verify`

## Key Files

| File | Purpose |
|------|---------|
| `data/.env` | API keys (symlink to master) |
| `data/rate_limit_state.json` | Persistent rate limit state |
| `~/.hermes/claudemem.db` | Shared agent memory |
| `~/.hermes/claudemem_harvest_state.json` | Harvester dedup state |
| `~/.hermes/config.yaml` | Hermes gateway config |
| `~/.hermes/lib/costguard/models.json` | Model pricing/allowlist |
| `Makefile` | test, secrets, proxy commands |

## New Provider Checklist

To add a new LLM provider:

1. Add provider class in `core/providers/[name].py`
2. Register it in `create_proxy_app()` in `proxy_server.py` (append to `providers`, keyed on the right env var)
3. Add its name to the `speed_order` list in `create_proxy_app()` and, if it supports tool calls, to `TOOL_CAPABLE_PROVIDERS` in `core/providers/router.py`
4. If it should be force-routable by model name, add an entry to `tool_model_routing`/`standard_model_routing` in the `/v1/chat/completions` handler (e.g. `deepseek/deepseek-v4-flash` → `deepseek-v4-flash`)
5. Add env var to `data/.env` (symlink to `~/.secrets/master.env`)
6. Add to `tests/test_regression_suite.py` (provider loaded test)
7. Run `make test`
8. Update `docs/ARCHITECTURE.md` and `docs/HOMELAB_MAP.md`

## New Observability Endpoint Checklist

1. Add `@app.get("/v1/...")` in `proxy_server.py`
2. Add `_enforce_localhost(request)` for sensitive endpoints
3. Add cache/auth checks if applicable
4. Add test to `test_regression_extended.py`
5. Run `make test`
6. Document in `docs/ARCHITECTURE.md`

## Debugging

```bash
# Proxy logs
tail -f data/logs/proxy.log

# Rate limit state
cat data/rate_limit_state.json | python3 -m json.tool

# TokenJuice stats
curl -s http://localhost:8080/v1/cache | python3 -m json.tool

# Rate limit stats
cat data/rate_limit_state.json | python3 -m json.tool

# Response cache
curl -s http://localhost:8080/v1/cache | python3 -m json.tool

# Check which providers are in cooldown
curl -s http://localhost:8080/v1/status | python3 -c "
import sys,json
d=json.load(sys.stdin)
for n,i in d['providers'].items():
    cd=i.get('cooldown_seconds',0)
    if cd: print(f'  {n}: cooldown {cd}s')
    cb=i.get('circuit_breaker',{})
    if cb.get('open'): print(f'  {n}: circuit OPEN (fails={cb.get(\"failures\",0)})')
"

# Harvester state
cat ~/.hermes/claudemem_harvest_state.json | python3 -m json.tool

# Harvester logs
tail -f data/logs/context_harvest.log
```

## Testing

```bash
make test-regression   # 33 fast tests (~35s)
make test-extended     # 78 infra tests (~15s)
make test              # All 111 tests
make test-all          # Full 340+ unit suite

# Specific test file
python3 -m pytest tests/test_regression_suite.py -v -k "test_simple_chat"
```

## Common Issues

| Symptom | Fix |
|---------|-----|
| All providers failing | Check `data/.env` exists (`make secrets-verify`) |
| Telegram slow | Check rate limiter stats; providers may be in cooldown |
| TokenJuice not saving tokens | Check content is HTML, not plain text |
| Harvester not running | Check `data/logs/context_harvest.log`; verify lockfile not stuck |
| Cache stale content | Clear: `curl -X DELETE http://localhost:8080/v1/cache` |
| Test failures after change | Run `make test-all` for full suite |

## Cron Job Changes

When adding/modifying cron jobs:
1. Update crontab: `crontab -e`
2. Verify script exists and is executable
3. Test manually first: `bash /path/to/script.sh`
4. Check log output after next cycle
5. Document in `docs/HOMELAB_MAP.md`
