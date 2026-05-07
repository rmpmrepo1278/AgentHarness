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
- **Model**: Multi-provider LLM routing via AgentHarness proxy (free-first: Groq → Google-alt → Cerebras → SambaNova → OpenRouter → Google-primary)

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
- **LLM Proxy** (AgentHarness, Port 8080): Multi-provider routing with tiered fallback
- **Local LLM** (llama.cpp, Port 8081): Gemma 4 26B-A4B (4096 ctx, CPU-only)
- **MCP Gateway** (Port 8090): Tool routing to 12 specialized MCP servers
- **claudemem.db**: Shared memory across all domain agents (observations, SOPs, session summaries)

## Monitoring
- **Uptime Kuma**: Service availability monitoring (Port 3002).
- **Chaguli Health**: Integrated system health checks.
- **Service Watchdog**: `service_watchdog.sh` (every 5 min), `doctor_check.py` (every 10 min).

## Media Services
- **Note**: Media stack (Sonarr, Radarr, Jellyfin, etc.) has been decommissioned.
- **Stump**: Digital library/comic server (Port 10801).
- **SearXNG**: Privacy-focused search engine (Port 8118).

## Git Repos (GitHub: rmpmrepo1278)
- **AgentHarness**: `/home/rohit/agentharness/` — LLM proxy, MCP framework, orchestrator scripts
- **AgentChaguli** (formerly AgentRocki): `/home/rohit/.hermes/hermes-agent/` — Hermes agent, gateway, skills
- **Openclaw**: `/home/rohit/openclaw/` — Docker compose stack
- **Career-ops**: `/home/rohit/projects/career-ops/`
