# Hub-and-Spoke Agent Architecture — Design Document

**Status**: Phases 1–3 **IMPLEMENTED** (May 7, 2026)
**Architecture**: Single bot, topic-based domain routing, isolated sub-agent contexts
**Decision**: Phase 4 (separate bots) — **SKIPPED** (see below)

## Goal

Evolve from a single Chaguli agent handling everything in one context window → a hub-and-spoke model where a router/hub dispatches to domain-specific sub-agents, each with isolated context, domain-specific personality, and focused tool usage.

## Current State (as-built)

```
┌─────────────────────────────────────────────────────────────┐
│                   Telegram Gateway (single bot)              │
│                   hermes.gateway service                     │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                   Hub Agent (router/triage)                  │
│                                                               │
│  1. Receive message + extract message_thread_id               │
│  2. Look up channel_prompts by thread_id (→ domain context)  │
│  3. Check focus_<session>.txt (→ /focus override)            │
│  4. Detect domain: focus override > topic prompt > none      │
│  5. Load SOUL_<DOMAIN>.md overlay into ephemeral prompt      │
│  6. Check agent cache (signature = model + tools + ephemeral)│
│     → Hit: reuse agent (same domain, same context)            │
│     → Miss: create fresh AIAgent with domain SOUL overlay    │
│  7. Execute and return result                                 │
└─────────────────────────────────────────────────────────────┘
        │                │                │
        ▼                ▼                ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Infra Agent  │ │ Career Agent │ │ Knowledge    │
│ SOUL_INFRA   │ │ SOUL_CAREER  │ │ SOUL_KNOWLEDGE│
│ HIGH reason  │ │ MEDIUM reas. │ │ MEDIUM reas. │
│ technical    │ │ professional │ │ teacher      │
└──────────────┘ └──────────────┘ └──────────────┘
```

## Architecture Decisions Made During Implementation

### Topic-based routing (Phase 1) — CHOSEN over separate bots
- Forum topics in a single Telegram supergroup map to domains via `message_thread_id`
- Configurable via `topic_routes.json` (single source of truth), synced to `config.yaml` by script
- Adding a new topic: edit `topic_routes.json`, run `sync_topic_routes.py`, restart gateway

### SOUL overlay injection (Phase 3) — CHOSEN over SOUL file swapping
- Domain SOUL files (`SOUL_INFRA.md`, etc.) are **additive overlays** injected into the ephemeral system prompt
- Base `SOUL.md` still loads as the foundation identity
- Gateway detects domain and loads the matching overlay automatically
- Agent cache invalidation is natural: ephemeral prompt is part of the cache signature

### Phase 4 (separate bots) — SKIPPED
- **Reason**: Topic-based routing already provides context isolation, domain-specific personalities, and focused tool usage within a single bot
- Separate bots would add operational complexity (N bot tokens, N configs) with no meaningful benefit
- Per-domain cron jobs can be achieved with topic-targeted dispatch from a single bot
- **Revisit if**: User experience with forum topics proves inferior to separate bots in practice

## Design Principles

1. **Single entry point** — User always talks to one bot. No switching.
2. **Domain isolation** — Each sub-agent has its own context window, SOUL overlay, and tool focus.
3. **Hub is lightweight** — Router only classifies domain and loads SOUL overlay. Does NOT do actual work.
4. **Shared memory** — All agents write to shared claudemem + SOP database.
5. **Single source of truth** — `topic_routes.json` is the canonical config; `config.yaml` is auto-generated.
6. **Progressive enhancement** — Each phase adds value independently.

## Implementation Phases — COMPLETE

### Phase 1: Topic-Aware Context Loading ✅
**What was built**:
- `~/.hermes/topic_routes.json` — Thread ID → domain mapping with skill subsets, model tiers, channel prompts
- `~/.hermes/config.yaml` — `channel_prompts` auto-generated from topic_routes.json
- `~/.hermes/scripts/sync_topic_routes.py` — Sync script (single source of truth)
- `~/.hermes/SOUL.md` — Domain awareness section added

**Thread mappings**:
| Thread ID | Domain | Model Tier | Personality |
|-----------|--------|------------|-------------|
| 1 | general | LOW | kawaii |
| 3 | infrastructure | HIGH | technical |
| 5 | knowledge-base | MEDIUM | teacher |
| 7 | career-ops | MEDIUM | concise |

### Phase 2: Intent-Based Routing ✅
**What was built**:
- `/focus <domain>` command — Manual domain override via quick_command
- `~/.hermes/scripts/set_focus.py` — Sets/clears per-session focus state file
- Gateway reads `focus_<session_key>.txt` and injects override into ephemeral prompt
- Intent classifier in SOUL.md — Keyword-based domain detection when no topic context exists
- `/domain` command — Shows active domain and detection method

**Domain aliases**: `infra` → infrastructure, `career` → career-ops, `knowledge` → knowledge-base

### Phase 3: Sub-Agent Spawning ✅
**What was built**:
- `~/.hermes/SOUL_INFRA.md` — SRE/DevOps identity, terminal-first, HIGH reasoning
- `~/.hermes/SOUL_CAREER.md` — Career coach identity, email-first, professional tone
- `~/.hermes/SOUL_KNOWLEDGE.md` — Research specialist, cite-sources, pedagogical
- Gateway domain detection + SOUL overlay injection in `run.py`
- Agent cache naturally invalidates on domain change (ephemeral prompt in cache signature)

**Domain detection priority**:
1. `/focus` session override file (`~/.hermes/sessions/focus_<session_key>.txt`)
2. `channel_prompt` content (topic-based keyword matching)
3. No domain → base SOUL.md only

## Adding a New Domain (e.g., "travel")

1. Edit `~/.hermes/topic_routes.json`:
   - Add thread ID mapping: `"42": "travel"`
   - Add domain config block (focus skills, model tier, personality)
   - Add channel prompt in `channel_prompts_by_thread_id`
2. Create `~/.hermes/SOUL_TRAVEL.md` (domain SOUL overlay)
3. Run `python3 ~/.hermes/scripts/sync_topic_routes.py`
4. Restart gateway: `systemctl --user restart hermes-gateway`

## Shared State

All agents share:
- `claudemem.db` (observations, SOPs, session summaries)
- `shared_facts.db` (entity memory)
- `/home/rohit/shared_agent_memory/` (dispatch, files)
- SOP library (`~/.hermes/skills/`)

Each agent is independent:
- Conversation history (per session key)
- System prompt (domain SOUL overlay)
- Tool definitions (all loaded, but domain SOUL guides which to prioritize)
- Model tier (guided by domain config, advisory)

## Success Metrics

- **Context efficiency**: Domain SOUL overlays reduce irrelevant tool usage
- **Response quality**: Domain-specific personalities and behavior rules
- **Telegram clarity**: Users interact in focused topic contexts
- **Extensibility**: New domain = edit JSON + create SOUL file + sync + restart
- **Debugging ease**: Domain issues isolated to domain context

## Risks & Mitigations

- **Misclassification**: User talks about infra in the career topic → `/focus` override handles this
- **SOUL overlay too large**: Keep domain SOUL files focused; base SOUL.md stays lean
- **Topic thread ID changes**: Telegram thread IDs are stable, but if recreated, update `topic_routes.json`
- **Sync drift**: `sync_topic_routes.py --check` validates consistency; run before gateway restart

## Related Documents

- **Implementation plan**: `/home/rohit/.claude/ORCHESTRATOR-HUB-PLAN.md`
- **Homelab map**: `/home/rohit/HOMELAB_MAP.md`
- **Orchestrator spec** (planner-executor): `/home/rohit/.claude/ORCHESTRATOR-SPEC.md`
- **Orchestrator instructions**: `/home/rohit/.claude/ORCHESTRATOR-INSTRUCTIONS.md`
