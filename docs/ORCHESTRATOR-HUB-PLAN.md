# Hub-and-Spoke Implementation Plan

**Status**: Phases 1–3 **COMPLETE** (May 7, 2026)
**Phase 4**: **SKIPPED** — Separate bots not needed; topic-based routing provides sufficient isolation

---

## Phase 1: Topic-Aware Context Loading ✅

### Task 1.1: Map Telegram Topics → Domains ✅
**File**: `/home/rohit/.hermes/topic_routes.json`
- Maps thread IDs → domains with skill subsets, model tiers, reasoning effort, personality hints
- Single source of truth; synced to config.yaml by `sync_topic_routes.py`

### Task 1.2: Update SOUL.md with domain-aware behavior ✅
**File**: `/home/rohit/.hermes/SOUL.md`
- Added "Domain Awareness" section with topic → domain mapping
- Added "Dynamic Skill Loading" section with per-domain tool priorities
- Added intent classifier keywords (Phase 2)
- Added `/focus` command handling instructions (Phase 2)

### Task 1.3: Add channel_prompts to config.yaml ✅
**File**: `/home/rohit/.hermes/config.yaml`
- `telegram.channel_prompts` keyed by thread ID ("1", "3", "5", "7")
- Auto-generated from `topic_routes.json` via `sync_topic_routes.py`
- Never hand-edited; always sync from topic_routes.json

---

## Phase 2: Intent-Based Routing ✅

### Task 2.1: Create /focus command ✅
**File**: `/home/rohit/.hermes/config.yaml` (quick_commands)
**Script**: `/home/rohit/.hermes/scripts/set_focus.py`
- `/focus infra|career|knowledge|general` — Sets session-level domain override
- `/focus --clear` — Clears override
- `/focus --show` — Shows current override
- State stored in `~/.hermes/sessions/focus_<session_key>.txt`
- Gateway reads focus file and injects `## DOMAIN FOCUS OVERRIDE` into ephemeral prompt
- `/domain` command added to show active domain

### Task 2.2: Add intent classifier to SOUL.md ✅
**File**: `/home/rohit/.hermes/SOUL.md`
- INFRA keywords: docker, container, service, systemd, disk, memory, cpu, network, backup, deploy, nginx, proxy, domain, dns, port, firewall, ssh, linux, ubuntu, server, uptime, monitor, log, compose, volume, image, k8s, terraform, ansible, ci/cd, pipeline
- CAREER keywords: job, resume, cv, interview, application, hiring, recruiter, linkedin, email draft, cover letter, salary, offer, negotiation, portfolio, referral, networking, position, role, company
- KNOWLEDGE keywords: research, find, search, article, paper, paperless, summarize, learn, what is, how does, explain, tutorial, documentation, docs, reference, study, understand, compare, vs, difference between
- GENERAL: everything else

### Task 2.3: Dynamic skill loading ✅
**File**: `/home/rohit/.hermes/SOUL.md` ("Dynamic Skill Loading" section)
- INFRA mode: Prioritize terminal, docker, homelab_ops, monitoring. Ignore career/email/document tools.
- CAREER mode: Prioritize gmail, calendar, career_ops, email. Ignore docker/infra tools.
- KNOWLEDGE mode: Prioritize research, search, paperless, web_search. Ignore infra/career tools.
- GENERAL mode: Minimal tools, conversational only.

---

## Phase 3: Sub-Agent Spawning ✅

### Task 3.1: Define agent SOUL variants ✅
- `~/.hermes/SOUL_INFRA.md` — SRE/DevOps identity, terminal-first, HIGH reasoning, technical personality
- `~/.hermes/SOUL_CAREER.md` — Career coach identity, email-first, MEDIUM reasoning, professional tone
- `~/.hermes/SOUL_KNOWLEDGE.md` — Research specialist, cite-sources, MEDIUM reasoning, teacher personality

### Task 3.2: Implement hub dispatch ✅
**File**: `/home/rohit/.hermes/hermes-agent/gateway/run.py`
- Domain detection: focus override > channel_prompt keyword matching > none
- SOUL overlay injection into `combined_ephemeral` before agent creation
- Domain → SOUL file mapping: `infrastructure` → `SOUL_INFRA.md`, `career-ops` → `SOUL_CAREER.md`, `knowledge-base` → `SOUL_KNOWLEDGE.md`

### Task 3.3: Sub-agent process management ✅
- Agent cache key includes ephemeral prompt → automatic cache invalidation on domain change
- Each domain gets fresh AIAgent with isolated context window
- Shared: claudemem.db, SOPs, files, skills library
- Independent: conversation history, system prompt (domain SOUL), ephemeral context

---

## Phase 4: Separate Telegram Bots — SKIPPED

**Decision**: Not implemented. Topic-based routing within a single bot provides:
- Sufficient context isolation (separate AIAgent per domain)
- Simpler operations (one bot, one gateway, one config)
- Equivalent UX (forum topics are functionally separate channels)

**Revisit if**: Forum topic UX proves inferior to separate bots in practice.

---

## Key Artifacts

| Artifact | Path | Purpose |
|----------|------|---------|
| Topic routes config | `~/.hermes/topic_routes.json` | Single source of truth for thread→domain mapping |
| Sync script | `~/.hermes/scripts/sync_topic_routes.py` | Syncs topic_routes.json → config.yaml |
| Focus script | `~/.hermes/scripts/set_focus.py` | Sets/clears per-session domain override |
| Base SOUL | `~/.hermes/SOUL.md` | Agent identity with domain awareness & intent classifier |
| Infra SOUL | `~/.hermes/SOUL_INFRA.md` | Infrastructure domain overlay |
| Career SOUL | `~/.hermes/SOUL_CAREER.md` | Career-ops domain overlay |
| Knowledge SOUL | `~/.hermes/SOUL_KNOWLEDGE.md` | Knowledge-base domain overlay |
| Gateway dispatch | `~/.hermes/hermes-agent/gateway/run.py` | Domain detection + SOUL overlay injection |
| Hermes config | `~/.hermes/config.yaml` | channel_prompts, quick_commands (auto-synced) |

## Adding a New Domain

1. Edit `topic_routes.json`: add thread ID, domain config block, channel prompt
2. Create `SOUL_<DOMAIN>.md` (domain SOUL overlay)
3. Run `python3 ~/.hermes/scripts/sync_topic_routes.py`
4. Restart gateway: `systemctl --user restart hermes-gateway`
