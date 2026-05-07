# Homelab — CLAUDE.md

You are operating on a homelab server (HP laptop, 192.168.29.10) running Ubuntu.
All work happens via SSH or locally on this machine. Never store code on external machines.

## System Overview

This homelab runs a self-hosted agent infrastructure combining:
- **Hermes Agent System**: Personal Chief of Staff agent ("Chaguli") with memory, skills, and multi-provider LLM routing
- **AgentHarness MCP Framework**: 12 specialized MCP servers for Docker, files, n8n, paperless, git, media, backups, network, RSS, and health
- **Application Stack**: NPM reverse proxy, n8n, Nextcloud, Gitea, Paperless-ngx, Immich, Pi-hole, Stump, SearXNG, Vaultwarden, Uptime Kuma, Portainer

## Key Paths

| Component | Path |
|-----------|------|
| Hermes agent | `/home/rohit/.hermes/hermes-agent/` |
| Hermes config | `/home/rohit/.hermes/config.yaml` |
| Hermes SOUL | `/home/rohit/.hermes/SOUL.md` |
| Hermes skills | `/home/rohit/.hermes/skills/` |
| Hermes logs | `/home/rohit/.hermes/logs/agent.log` |
| Hermes Web UI | `/home/rohit/hermes-webui/` (port 8787) |
| AgentHarness | `/home/rohit/agentharness/` |
| Proxy server | `/home/rohit/agentharness/core/providers/proxy_server.py` |
| Anthropic compat | `/home/rohit/agentharness/core/providers/anthropic_compat.py` |
| API keys/env | `/home/rohit/agentharness/data/.env` (single source of truth) |
| Application stack | `/home/rohit/openclaw/docker/compose/` |
| Shared agent memory | `/home/rohit/shared_agent_memory/` |
| LLM models | `/home/rohit/models/` |
| llama.cpp | `/home/rohit/ik_llama.cpp/` |
| Career-ops | `/home/rohit/projects/career-ops/` |

## Ports

| Service | Port |
|---------|------|
| LLM Proxy | 8080 |
| Local LLM (llama.cpp) | 8081 |
| MCP Gateway | 8090 |
| MCP child services | 8095-8105 |
| Hermes Web UI | 8787 |
| NPM (HTTP/Admin/HTTPS) | 80/81/443 |
| Pi-hole DNS / Web UI | 53 / 8053 |
| Homepage dashboard | 7575 |
| Portainer | 9000 |
| Uptime Kuma | 3002 |
| Gitea | 3001 |
| Nextcloud | 8888 |
| n8n | 5678 (localhost only) |
| Paperless | 8000 |
| Immich | 2283 |
| Stump | 10801 |
| SearXNG | 8118 |

## LLM Proxy Architecture

The proxy at port 8080 serves TWO API formats:
- **OpenAI format** (`/v1/chat/completions`): Used by Hermes. Tiered routing: free providers first (groq, cerebras, sambanova, google-alt), paid last (google-primary).
- **Anthropic format** (`/v1/messages`): Used by Claude Code. Routes DIRECTLY to Google Gemini 2.5 Pro, bypassing tiered routing.

Provider priority for Hermes (tool calls): groq → google-alt → cerebras → sambanova → fireworks → openrouter → google-primary.
Model override env vars: `{PROVIDER}_TOOL_MODEL` (e.g., `GOOGLE_TOOL_MODEL=gemini-2.5-pro`).

## Docker Compose Projects

1. **agentharness** (12 containers): `/home/rohit/agentharness/docker-compose.mcp.yml`
   - mcp-gateway, docker-mcp, file-mcp, n8n-mcp, paperless-mcp, git-mcp, media-mcp, backup-mcp, network-mcp, rss-mcp, doctor-mcp, autoheal
2. **compose** (9 containers): `/home/rohit/openclaw/docker/compose/`
   - npm.yml, n8n.yml, gitea.yml, nextcloud.yml, paperless.yml, searxng.yml, network-watchdog.yml
3. **immich** (4): immich_server, immich_machine_learning, immich_redis, immich_postgres
4. **pihole** (1), **uptime-kuma** (1), **vaultwarden** (1), **stump** (1)

## Systemd User Services

- `hermes-gateway.service`: Hermes Telegram bot (polling mode). RestartSec=120, StartLimitBurst=30/hour.

## Cron Jobs

| Schedule | Task |
|----------|------|
| */5 min | service_watchdog.sh |
| */10 min | uptime_kuma_autosync.sh, deadman_check.sh |
| */15 min | homelab_monitor.py (Hermes proactive) |
| 1 min | inbox_watcher.py |
| @reboot | start_llm_server.sh (30s delay) |
| Sun 11am | weekly_optimize.sh |
| 7am daily | homelab_summary.sh |
| 8am daily | daily AI news digest |

## DNS & Reverse Proxy

- Pi-hole: wildcard `address=/chagulihome.duckdns.org/192.168.29.10` in dnsmasq
- NPM: reverse proxy for `*.chagulihome.duckdns.org` subdomains (SSL via Let's Encrypt)
- DuckDNS: `chagulihome.duckdns.org` points to public IP

## Git Repos (GitHub: rmpmrepo1278)

- AgentHarness: `/home/rohit/agentharness/`
- Hermes agent: `/home/rohit/.hermes/hermes-agent/` (remote: `chaguli`)
- Openclaw stack: `/home/rohit/openclaw/`
- Career-ops: `/home/rohit/projects/career-ops/`

## Storage

- Root: 221GB (41% used)
- External USB: 4.6TB at `/mnt/usb` (4% used, backups/media)

## Known Issues

- Free-tier providers (Groq, Cerebras, SambaNova) rate-limit (429) frequently; cooldowns escalate exponentially
- Local LLM (Gemma 4 26B-A4B, 4096 ctx) times out on large requests
- Nightly ISP outage (~1 AM) can crash hermes-gateway; systemd now tolerates 30 restarts/hour
- Host nginx was disabled (was conflicting with NPM port bindings)
- **inbox_watcher.py race condition** (FIXED May 4): Was spawning overlapping processes every minute under load. Now uses `flock` in cron. Monitor process count if load spikes.
- **Duplicate hermes-gateway** (FIXED May 4): System-level service (`/etc/systemd/system/hermes-gateway.service`) was conflicting with user-level service. System-level is now disabled.

## System Health Watchdog — MANDATORY FIRST STEP

**BEFORE debugging ANY system issue**, run these commands FIRST — never jump straight to code files:

```bash
uptime
free -h
ps aux | wc -l
systemctl --failed
```

**Thresholds that trigger immediate investigation:**
- Load average > 5× CPU cores → investigate runaway processes
- Process count > 300 → something is spawning out of control
- Any failed systemd service → check before proceeding
- Memory available < 1GB → check for memory leaks

**When the user reports "slow", "stuck", "unresponsive", or "crash":**
1. Run health checks above FIRST
2. Check `ps aux --sort=-%cpu | head -20` for CPU hogs
3. Check `dmesg | tail -20` for kernel/OOM events
4. Only THEN examine application code

**Never continue a code-audit session when the user reports a live system problem. Start a new session or explicitly scope the task.**

## Development Workflow

- Always `git commit && git push` after completing debug/fix sessions
- Never ask for permission before taking read-only actions
- SOUL.md defines Hermes personality; config.yaml defines capabilities
- SOPs are stored in claudemem_sop_search — check before starting tasks

## Active Project: Hub-and-Spoke Agent Architecture ✅ COMPLETE

Hub-and-spoke domain routing for Chaguli agent. Phases 1–3 implemented; Phase 4 (separate bots) skipped.

**What was built**:
- Topic-based domain routing via Telegram forum topics (thread IDs → domains)
- `/focus <domain>` manual override with session persistence
- Domain-specific SOUL overlays (INFRA, CAREER, KNOWLEDGE) injected as ephemeral prompts
- Intent classifier for cross-topic messages
- Single source of truth: `~/.hermes/topic_routes.json` synced to config.yaml

**Key commands**: `/focus infra|career|knowledge|general`, `/focus --clear`, `/domain`

**Key files**:
- Routes config: `~/.hermes/topic_routes.json`
- Sync script: `~/.hermes/scripts/sync_topic_routes.py`
- Focus script: `~/.hermes/scripts/set_focus.py`
- Domain SOULs: `~/.hermes/SOUL_INFRA.md`, `SOUL_CAREER.md`, `SOUL_KNOWLEDGE.md`
- Gateway dispatch: `~/.hermes/hermes-agent/gateway/run.py`

**Documentation**:
- **Design doc:** `/home/rohit/.claude/ORCHESTRATOR-HUB-DESIGN.md`
- **Implementation plan:** `/home/rohit/.claude/ORCHESTRATOR-HUB-PLAN.md`
- **Homelab map:** `/home/rohit/HOMELAB_MAP.md`

**Adding a new domain**: Edit `topic_routes.json` → create `SOUL_<DOMAIN>.md` → run `sync_topic_routes.py` → restart gateway.

---

## Legacy: Planner-Executor Orchestrator (separate project)

Implementation spec for the planner-executor orchestrator. Not yet started.

- **Start here:** `/home/rohit/.claude/ORCHESTRATOR-INSTRUCTIONS.md`
- **Implementation spec:** `/home/rohit/.claude/ORCHESTRATOR-SPEC.md`
- **Design doc:** `/home/rohit/.claude/ORCHESTRATOR-DESIGN.md`

## Claude Code Self-Configuration

This claude instance routes through `~/.claude/settings.json`, swapped by `claude-mode {proxy|openrouter|show}` (script at `~/bin/claude-mode`). Mode definitions live in `~/.claude/modes/`.

- **proxy mode** → `localhost:8080` → Gemini 2.5 Pro (out of credits as of 2026-04-25)
- **openrouter mode** → `openrouter.ai/api` → currently set to `qwen/qwen3-coder:free` (recommended for debugging)
- **Model switcher**: `claude-mode-free [qwen3-coder|gpt-oss|nemotron|qwen3-next|glm-4.5|llama-3.3|owl]`
  - **For system debugging**: use `qwen3-coder` (262K ctx, best coding/agent performance)
  - **For general tasks**: `gpt-oss` or `nemotron` (large context, strong reasoning)
  - **For tool-heavy sessions**: `glm-4.5` (reliable tool use)
  - **Fallback**: `owl` (1M ctx but weaker reasoning — only for massive context needs)

### Env-var gotchas (Claude Code 2.1+)

If you edit a mode file or `proxy_server.py` to route Anthropic traffic to a non-Anthropic backend, all three rules apply or you get "There`'s an issue with the selected model":

1. **`ANTHROPIC_BASE_URL` must NOT end in `/v1`** — Claude Code auto-appends `/v1/messages`. Use `https://openrouter.ai/api`.
2. **`ANTHROPIC_MODEL` is ignored.** Set the three tier vars: `ANTHROPIC_DEFAULT_HAIKU_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL` — Claude Code calls all three internally.
3. **`ANTHROPIC_API_KEY` must be `""` (empty string, not unset)** so the fallback to `ANTHROPIC_AUTH_TOKEN` triggers.

OpenRouter key (single source of truth): `OPENROUTER_API_KEY` in `/home/rohit/agentharness/data/.env`.

If Qwen tool-use stumbles on a long session, fall back to GLM-5 by editing `~/.claude/modes/settings.openrouter-qwen.json` model fields to `z-ai/glm-5`.

## Zero-Cost LLM Guard

**Policy: Claude Code must NEVER incur charges.** All API calls must use free OpenRouter models.

- **Current model**: `qwen/qwen3-coder:free` (262K ctx, best free coder model)
- **Guard script**: `python3 ~/.claude/scripts/zero_cost_guard.py`
- **Monitoring**: Cron checks every 5 minutes that the current model is still free
- **Auto-switch**: If ANY charge is detected (even $0.0001), the guard auto-switches to the next best free model
- **Commands**:
  - `zero_cost_guard.py status` — show current model + ranked free alternatives
  - `zero_cost_guard.py check` — verify current model is free (used by cron)
  - `zero_cost_guard.py test` — make a test API call and check for charges
  - `zero_cost_guard.py switch <model>` — manually switch to a specific free model
  - `zero_cost_guard.py monitor` — continuous monitoring loop

**Ranked free models** (tool-supporting, best first):
1. `qwen/qwen3-coder:free` — 262K ctx, purpose-built coder ← CURRENT
2. `nvidia/nemotron-3-super-120b-a12b:free` — 262K ctx, large
3. `qwen/qwen3-next-80b-a3b-instruct:free` — 262K ctx
4. `openai/gpt-oss-120b:free` — 131K ctx
5. `z-ai/glm-4.5-air:free` — 131K ctx, reliable tool use
6. `nvidia/nemotron-3-nano-30b-a3b:free` — 256K ctx
7. `inclusionai/ling-2.6-1t:free` — 262K ctx
8. `google/gemma-4-26b-a4b-it:free` — 262K ctx
9. `openrouter/owl-alpha` — 1M ctx, fallback (weaker reasoning)

**If the guard auto-switches**, it updates `~/.claude/settings.json` and logs to `~/.claude/logs/zero_cost_guard.log`. Restart Claude Code after a switch.
