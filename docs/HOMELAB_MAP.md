# Homelab Architecture Map

## Overview
Primary Host: 192.168.29.10 (HP Ryzen 4700U, 36GB RAM)
OS: Debian 13 (Trixie)
Storage: 256GB NVMe (Root), 5TB USB (External)

## Core Infrastructure
- **Docker Engine**: Container orchestration.
- **Nginx Proxy Manager (NPM)**: Reverse proxy and SSL management (Port 81).
- **Pi-hole**: DNS sinkhole and local DNS (Port 8053).
- **Portainer**: Visual container management (Port 9000).

## Productivity & Data
- **Paperless-ngx**: Document management (Port 8000).
- **Gitea**: Local Git server (Port 3001).
- **n8n**: Workflow automation (Port 5678).
- **Vaultwarden**: Password management.

## AI & Agents — Hub-and-Spoke Architecture

### Chaguli Agent (Chief of Staff)
- **Gateway**: Hermes agent system, single Telegram bot entry point
- **Interface**: Telegram supergroup with forum topics (infrastructure, knowledge-base, career-ops, general)
- **Model**: Multi-provider LLM routing via AgentHarness proxy (CostGuard dynamic order: tier + reliability; free-only)

### Hub-and-Spoke Domain Routing
- **Single bot, domain-isolated contexts** — Messages route to domain-specific sub-agents based on Telegram topic or `/focus` override
- **Topic → Domain mapping** (via `~/.hermes/topic_routes.json`):
  - Thread 1 → General (LOW reasoning, kawaii personality)
  - Thread 3 → Infrastructure (HIGH reasoning, technical personality)
  - Thread 5 → Knowledge-Base (MEDIUM reasoning, teacher personality)
  - Thread 7 → Career-Ops (MEDIUM reasoning, concise personality)
- **Domain SOUL overlays** (`~/.hermes/SOUL_INFRA.md`, `SOUL_CAREER.md`, `SOUL_KNOWLEDGE.md`) — Injected as ephemeral system prompts per domain
- **Agent cache** — Fresh AIAgent per domain with isolated context window; cache invalidates automatically on domain change

### Domain Commands
- `/focus <domain>` — Manual domain override (persists across messages)
- `/focus --clear` — Clear override
- `/domain` — Show active domain and detection method

### Key Files
| File | Purpose |
|------|---------|
| `~/.hermes/topic_routes.json` | Thread ID → domain mapping, skill subsets, model tiers (single source of truth) |
| `~/.hermes/config.yaml` | Hermes config; `channel_prompts` auto-generated from topic_routes.json |
| `~/.hermes/SOUL.md` | Base agent identity with domain awareness & intent classifier |
| `~/.hermes/SOUL_INFRA.md` | Infrastructure domain overlay (SRE/DevOps identity) |
| `~/.hermes/SOUL_CAREER.md` | Career-ops domain overlay (career coach identity) |
| `~/.hermes/SOUL_KNOWLEDGE.md` | Knowledge-base domain overlay (research specialist identity) |
| `~/.hermes/scripts/sync_topic_routes.py` | Syncs topic_routes.json → config.yaml channel_prompts |
| `~/.hermes/scripts/set_focus.py` | Sets/clears per-session domain focus override |

### Supporting Infrastructure
- **LLM Proxy** (AgentHarness, Port 8080): `agentharness-llm-proxy.service` — CostGuard dynamic routing, `/v1/reliability`
- **Source of truth**: `~/.hermes/hermes-agent/proxy/core/providers/` (runtime symlinks from `~/agentharness/core/providers/`)
- **Sync scripts**: `~/agentharness/scripts/sync-proxy-from-hermes.sh`, `verify-proxy-sync.sh`
- **CostGuard**: `~/.hermes/hermes-agent/costguard/` (symlinked at `~/.hermes/lib/costguard`)
- **Local LLM** (`llama-local.service`, Port **18090**): Llama-3.2-1B IQK (4096 ctx)
- **MCP Gateway** (Port 8090): Tool routing to 14 specialized MCP servers
- **Hermes Memory MCP** (Port 8091)
- **Autoheal** (`willfarrell/autoheal`): Monitors labeled containers, restarts unhealthy every 120s
- **claudemem.db**: Shared memory across all domain agents (observations, SOPs, session summaries)

## Monitoring & Auto-Heal
- **Uptime Kuma**: Service availability monitoring (Port 3002).
- **Netdata**: Host metrics (Port 19999).
- **health_check.sh**: Cron every 5 min — hermes-gateway, LLM proxy (systemd), local LLM, Docker, memory MCP.
- **deadman_check.sh**: Cron every 10 min — heartbeat / dead-man switch.
- **doctor_check.py**: On-demand health + 9 runbooks (`/doctor` in Telegram).
- **sentinel-agent**: User systemd — proactive incident detection and remediation.
- **proactive-daemon**: User systemd — SOP and health monitoring.
- **Autoheal** (Docker): Restarts unhealthy containers with `autoheal=true` label.
- **service_watchdog.sh**: Deprecated wrapper → calls `health_check.sh`.

## Media Services
- **Note**: Media stack (Sonarr, Radarr, Jellyfin, etc.) has been decommissioned.
- **Stump**: Digital library/comic server (Port 10801).
- **SearXNG**: Privacy-focused search engine (Port 8118).

## Git Repos (GitHub: rmpmrepo1278)
- **AgentHarness**: `/home/rohit/agentharness/` — LLM proxy runtime, MCP framework, monitoring scripts
- **AgentChaguli**: `/home/rohit/.hermes/hermes-agent/` — Hermes agent, gateway, CostGuard, proxy source
- **Openclaw**: `/home/rohit/openclaw/` — Docker compose stack
- **Career-ops**: `/home/rohit/projects/career-ops/`
