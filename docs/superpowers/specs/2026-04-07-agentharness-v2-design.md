# AgentHarness v2 — Full Redesign Spec

**Revision:** 2 (post-Codex review)
**Codex review date:** 2026-04-07
**Changes:** Addressed 18 findings from independent Codex review. Added phased delivery plan, concurrency model, ownership boundary clarity, error handling, credential security, approval authentication, and migration strategy.

## Overview

AgentHarness v2 transforms the project from a Chaguli-specific homelab automation layer into a **generic, open-source infrastructure agent framework**. Any agent (Chaguli, OpenClaw, custom Python bots, etc.) can plug in via a bridge adapter. The user's Chaguli homelab setup becomes the reference deployment.

**Core identity:** AgentHarness is the hands, never the brain. It discovers, executes, monitors, and proposes — the agent decides.

## Goals

1. Plug all identified gaps from the v1 rework (feedback loop, proactive alerts, security, self-improvement)
2. Build every feature generic-first for open-source release on GitLab
3. Discovery-driven — never hardcode paths, always probe at runtime
4. HITL approval for all risky or self-modifying operations
5. Modular bundle system for community extensibility
6. Multi-provider LLM abstraction with budget-aware routing
7. Continuous optimization scouting and adoption pipeline

## Non-Goals

- AgentHarness does NOT make decisions — it proposes, the agent (or human) decides
- AgentHarness does NOT replace an agent's memory, personality, or reasoning
- AgentHarness does NOT duplicate agent-level learning — it generates *infrastructure* insights that the agent consumes
- No Ollama provider shipped (per project constraint), but the interface allows third-party adapters
- No heavy frontend framework — dashboard is optional, single-file, server-rendered
- Not cross-platform — targets Linux (Debian/Ubuntu) with Docker and systemd. macOS/Windows are out of scope for v2.

---

## Phased Delivery Plan

**Codex finding:** "You are trying to ship everything in one move. There is no credible vertical slice."

**Response:** The build is split into four delivery phases. Each phase is independently useful and testable. Later phases depend on earlier ones.

### Phase A: Foundation (Discovery + Script Rewrite + Registry Evolution)
**What ships:** Discovery engine, all scripts rewritten to use discovered paths, registry evolved to support bundles, CLI skeleton.
**Why first:** Nothing else works without discovery. Scripts must be rewritten (not wrapped) to eliminate hardcoded paths. The registry evolution is incremental, not a rebuild.
**Validates:** install.sh works on different machines. Bundle loading works. CLI can run tools.

### Phase B: Intelligence Layer (Providers + Budget + Scheduler Rewrite)
**What ships:** LLM provider abstraction, router, budget tracking, scheduler rewritten in Python.
**Why second:** Providers need discovery (Phase A) to find endpoints. Scheduler needs registry (Phase A) to know what to run.
**Validates:** LLM calls route correctly. Budget tracking works. Scheduler reads bundles.

### Phase C: Safety + Approval (HITL + Sandbox + Agent Bridge)
**What ships:** Approval gateway, sandbox execution, Chaguli bridge adapter.
**Why third:** Approval needs the scheduler (Phase B) to execute approved proposals. Sandbox wraps tool execution (Phase A). Bridge needs discovery (Phase A) to find the agent.
**Validates:** Proposals flow through Telegram. Tools execute in correct sandbox. Agent receives briefings.

### Phase D: Learning + Optimization (Feedback + Scout + Dashboard)
**What ships:** Distiller, synthesizer, preference model, optimization scout, optional dashboard.
**Why last:** These consume data from all earlier phases. They are valuable but not blocking.
**Validates:** Morning briefings arrive. Pattern detection proposes useful tools. Scout finds real optimizations.

Each phase ends with a working system. You can stop after Phase A and have a dramatically better AgentHarness. Each subsequent phase adds capability.

---

## Architecture

```
agentharness/
├── core/                          # Generic framework (open-source)
│   ├── discovery/                 # Path/service/hardware discovery
│   │   ├── engine.py              # Coordinator — ensure_fresh(), resolve(), override()
│   │   ├── paths.py               # Install dirs, config locations, data dirs
│   │   ├── services.py            # Docker containers, ports, APIs, MCP servers
│   │   ├── hardware.py            # RAM, CPU, GPU, NPU, storage, NICs, USB drives
│   │   ├── agents.py              # Find agent installations + capabilities
│   │   ├── credentials.py         # API keys, tokens, .env files (opt-in only)
│   │   └── state.py               # State management with locking + atomic writes
│   ├── providers/                 # LLM abstraction layer
│   │   ├── base.py                # Abstract provider interface
│   │   ├── router.py              # Smart routing by complexity + budget + fallback
│   │   ├── llamacpp.py            # llama.cpp / ik_llama.cpp
│   │   ├── lemonade.py            # AMD GPU/NPU (Lemonade SDK)
│   │   ├── groq.py                # Groq API (free tier)
│   │   ├── google.py              # Gemini API (free tier — 1500/day Flash)
│   │   ├── openrouter.py          # OpenRouter (free model rotation)
│   │   ├── cerebras.py            # Cerebras (free tier)
│   │   ├── sambanova.py           # SambaNova (free tier)
│   │   ├── together.py            # Together AI
│   │   ├── openai.py              # OpenAI-compatible (also LocalAI, vLLM, LM Studio)
│   │   └── anthropic.py           # Claude API
│   ├── agents/                    # Agent integration layer
│   │   ├── base.py                # Abstract agent bridge interface
│   │   └── chaguli.py             # Chaguli adapter (reference implementation)
│   ├── registry/                  # YAML-driven tool/check/harness system
│   │   ├── engine.py              # Registry engine (evolved from registry_engine.py)
│   │   ├── loader.py              # Load + merge YAML bundles
│   │   └── schema.py              # Validate registry entries against schema
│   ├── approval/                  # HITL approval gateway
│   │   ├── gateway.py             # Proposal creation, signing, lifecycle
│   │   ├── policies.py            # Tier definitions (auto/notify/approve)
│   │   └── auth.py                # Telegram command authentication (HMAC tokens)
│   ├── sandbox/                   # Tool execution isolation
│   │   ├── runner.py              # Dispatch to correct sandbox mode
│   │   ├── docker_sandbox.py      # Ephemeral Docker container execution
│   │   └── direct.py              # Host execution (trusted scripts only)
│   ├── scheduler/                 # Network/resource-aware scheduling
│   │   ├── scheduler.py           # Core scheduler (Python, replaces scheduler.sh)
│   │   ├── budget.py              # LLM request budgeting across providers
│   │   └── windows.py             # Time/network window detection
│   ├── observe/                   # Observability + self-watchdog
│   │   ├── heartbeat.py           # Scheduler watchdog (systemd timer)
│   │   ├── metrics.py             # Tool call / check / proposal metrics
│   │   └── dashboard.py           # Optional FastAPI web UI
│   ├── feedback/                  # Infrastructure insight generation
│   │   ├── synthesizer.py         # Detect patterns → propose new tools
│   │   ├── distiller.py           # Daily logs → executive briefing
│   │   ├── bridge.py              # Push insights to agent via adapter
│   │   └── preference_model.json  # Learned approval/rejection patterns
│   └── optimize/                  # Discovering future improvements
│       ├── scout.py               # Search sources for new techniques
│       ├── evaluator.py           # Score applicability to current + future hardware
│       ├── tracker.py             # What's been tried, what worked, source reliability
│       └── proposals.py           # Generate HITL proposals for promising finds
├── bundles/                       # Modular tool registries
│   ├── core/                      # Always active — disk, RAM, swap, CPU checks
│   ├── homelab/                   # Docker + self-hosted service monitoring
│   ├── inference/                 # LLM engine build, download, benchmark
│   ├── security/                  # Hardening + audit
│   ├── backup/                    # Backup + verify + restore
│   ├── dashboard/                 # Optional web dashboard
│   └── community/                 # Downloaded third-party bundles
├── proposals/                     # HITL approval queue (JSON files)
├── config/
│   ├── agentharness.yaml          # Main config (discovery hints, sandbox defaults)
│   ├── providers.yaml             # LLM provider config + routing + budgets
│   ├── bundles.yaml               # Which bundles are active
│   └── overrides.yaml             # User's local registry overrides
├── scripts/                       # Shell scripts (rewritten to use discovery)
├── install.sh                     # Discovery-first installer
├── cli.py                         # agentharness CLI entry point
└── README.md
```

---

## Section 1: Discovery Engine

### Problem

install.sh failed on real hardware because paths were hardcoded. Every script assumed `/opt/agentharness/`, but the actual install location, Docker paths, agent directories, USB mounts, and config files vary per machine.

### Design

Discovery is a first-class subsystem, not a one-time install phase. Nothing hardcodes paths.

**Discovery modules:**

| Module | Discovers | Method |
|--------|-----------|--------|
| `paths.py` | Install location, config dirs, data dirs, log dirs | Convention → env vars → filesystem probe |
| `services.py` | Running Docker containers, ports, APIs, MCP servers | `docker ps`, port scanning, process list |
| `hardware.py` | RAM (per-DIMM), CPU (cores, arch, features), GPU, NPU, storage, USB drives, NICs | `/proc`, `lscpu`, `lspci`, `lsblk`, `lsusb`, `ip link` |
| `agents.py` | Agent installations (Chaguli container, app dir, key files, capabilities) | Docker inspect, filesystem probe for known patterns |
| `credentials.py` | API keys, tokens, .env files, service credentials | **Opt-in only** — see Security section |

**Discovery resolution order (per path):**

1. Explicit override in `agentharness.yaml` (always wins)
2. Environment variable (`$AGENTHARNESS_HOME`, etc.)
3. Convention-based probing (common install locations)
4. Process/container inspection
5. Filesystem search (last resort, cached after first find)

### State Management (Codex fix: concurrency + atomicity)

**Codex finding:** "`state.json` refreshed every scheduler tick is a race-condition magnet."

`state.json` is replaced by `state.py` — a proper state manager:

```python
class StateManager:
    """Thread-safe, atomic state with schema versioning."""

    LOCK_FILE = "{data_dir}/state.lock"
    STATE_FILE = "{data_dir}/state.json"
    SCHEMA_VERSION = 1

    def read() -> dict:
        """Read current state. No lock needed (atomic read of last-written file)."""

    def write(updates: dict) -> None:
        """Atomic write with file locking.
        1. Acquire LOCK_FILE (fcntl.flock, 5s timeout)
        2. Read current state
        3. Merge updates
        4. Write to state.json.tmp
        5. os.rename() (atomic on Linux)
        6. Release lock
        """

    def ensure_fresh(max_age_seconds=900) -> None:
        """Re-validate cached paths exist. If stale:
        1. Try re-resolve via discovery
        2. If resolved → update state
        3. If not resolved → mark as 'missing', alert
        Partial refresh: only re-validates paths older than max_age.
        """

    def schema_migrate(old_version, new_version) -> None:
        """Handle state file upgrades between AgentHarness versions."""
```

**Failure semantics:**
- Lock acquisition timeout → skip this tick, log warning, try next tick
- Partial discovery failure → keep last-known-good values for failed paths, alert on missing
- Corrupt state file → rebuild from scratch via full discovery, alert

### Script Rewrite (Codex fix: not just "wrap")

**Codex finding:** "Legacy code is hardcoded to `/opt/agentharness` everywhere — discovery-first is fake unless you rewrite the scripts too."

**Response:** All scripts in `scripts/` are rewritten in Phase A. Every script:

1. Sources a common preamble that reads `state.json` for all paths:
   ```bash
   # scripts/common.sh (rewritten)
   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
   STATE_FILE="$(dirname "$SCRIPT_DIR")/core/discovery/state.json"

   # Read paths from state — no hardcoded /opt/agentharness
   eval "$(python3 -c "
   import json, sys
   state = json.load(open('$STATE_FILE'))
   for k, v in state.get('paths', {}).items():
       print(f'export AH_{k.upper()}=\"{v}\"')
   ")"
   ```

2. Uses `$AH_DATA_DIR`, `$AH_SCRIPTS_DIR`, `$AH_REPORTS_DIR`, etc. instead of hardcoded paths
3. Fails with a clear error if state.json is missing or a required path is unresolved

No "wrap legacy scripts" — the scripts are migrated.

### Future Hardware Awareness

`hardware.py` supports a `planned_hardware` section in config for evaluating optimizations against hardware that's arriving but not installed yet (e.g., 8745HS mini PC with XDNA NPU).

---

## Section 2: LLM Provider Abstraction + Budget Layer

### Provider Interface (Codex fix: realistic abstractions)

**Codex finding:** "`capacity()` and `cost_per_request()` are not realistic abstractions."

```python
class LLMProvider:
    name: str                    # "llamacpp", "groq", "google", etc.
    tier: str                    # "local", "cloud_free", "cloud_paid"

    def complete(prompt, max_tokens, temperature) -> Response
    def stream(prompt, max_tokens, temperature) -> Iterator[str]
    def is_available() -> bool   # Health check

    def budget_status() -> BudgetStatus:
        """Returns what we know about remaining capacity.
        Fields:
          known_remaining: int | None   # None if provider doesn't expose this
          estimated_remaining: int | None  # Our tracking estimate
          reset_at: datetime | None
          cost_model: "per_request" | "per_token" | "per_minute" | "free"
        """

    def capabilities() -> list   # ["chat", "function_calling", "vision"]
```

Key change: `budget_status()` returns what's *actually knowable* per provider. Some expose remaining quota (Groq), some don't (OpenRouter free tier). The budget layer tracks its own estimates regardless.

### Shipped Providers

**Local (unlimited):**
- `llamacpp.py` — llama.cpp / ik_llama.cpp HTTP server
- `lemonade.py` — AMD GPU/NPU inference (Lemonade SDK, for future 8745HS)

**Cloud free (budget-tracked):**
- `groq.py` — 200 req/day
- `google.py` — Gemini: 1500/day Flash, 50/day Pro
- `openrouter.py` — Free models rotate (scout.py tracks availability)
- `cerebras.py` — ~1000 req/day on Llama models
- `sambanova.py` — Free tier on Llama/DeepSeek
- `together.py` — $1 free credit, cheap after

**Cloud paid (budget-capped):**
- `openai.py` — OpenAI-compatible (also covers LocalAI, vLLM, LM Studio)
- `anthropic.py` — Claude API

No Ollama provider shipped. The public interface allows third-party adapters.

### Router (Codex fix: fallback + retry + backoff)

Routes each request to the best provider based on complexity hint, budget, and availability.

**Complexity tiers:**

| Tier | Priority order | Use case |
|------|---------------|----------|
| LOW | local_small → local_large | Triage, formatting, simple extraction |
| MEDIUM | local_large → google_flash → cerebras → openrouter | Summarization, analysis, tool selection |
| HIGH | google_pro → groq → sambanova → local_large | Complex reasoning, multi-step planning |
| CRITICAL | groq → google_pro → openai → anthropic → local_large | System broken, immediate help needed |

**Fallback behavior:**
```python
class Router:
    def route(request) -> Response:
        """Try providers in priority order for the request's complexity tier.

        For each provider:
        1. Check is_available() — skip if down
        2. Check budget_status() — skip if exhausted
        3. Check user policy — skip if blocked for this tool/category
        4. Attempt the call
        5. On rate limit (429): exponential backoff (1s, 2s, 4s), max 3 retries
        6. On timeout: skip, try next provider
        7. On auth error: disable provider, alert, try next
        8. On success: record usage in budget tracker

        If all providers exhausted:
        - For LOW/MEDIUM: queue the request for later retry
        - For HIGH/CRITICAL: alert user "All providers exhausted"
        """
```

### Budget Layer (Codex fix: concurrency-safe)

**Codex finding:** "`reserve/commit/release` says nothing about overlapping requests."

```python
class LLMBudget:
    """Concurrency-safe budget tracking. Uses file lock shared with StateManager."""

    def can_use(provider) -> bool
        """Quick check — does NOT reserve. Thread-safe read."""

    def record_usage(provider, tokens_in, tokens_out, success) -> None
        """Record after the fact. Simpler than reserve/commit/release.
        Atomic write with lock.
        If we exceed the limit, the next can_use() returns False.
        Slight over-budget is acceptable — better than lock contention."""

    def daily_report() -> str
    def reset_daily_counters() -> None  # Called by scheduler at midnight
```

Key change: Dropped the reservation pattern. Record-after-the-fact is simpler and good enough. Slight overages on a free tier are harmless — the provider will return 429, the router will fallback.

**Budget-aware behaviors:**
- At 80% of any provider's daily limit → router deprioritizes
- At 95% → only CRITICAL requests use that provider
- Spread load across free providers by remaining quota
- Budget report feeds into nightly executive briefing

### Free Tier Registry

`optimize/scout.py` maintains `free_tier_registry.json` — auto-updated weekly. Tracks which providers have free tiers, current limits, which models are available.

**Codex finding:** "~2,800+ requests/day is operational fiction."

**Response:** The number is an *estimate*, not a guarantee. It's documented as such. The router doesn't depend on the total — it depends on per-provider `budget_status()` and `is_available()`. If a free tier disappears tomorrow, that provider simply returns unavailable and the router skips it. No hardcoded dependency on aggregate numbers.

---

## Section 3: HITL Approval Gateway + Sandbox Execution

### Approval Tiers

| Tier | Behavior | Examples |
|------|----------|---------|
| `auto` | Runs immediately, no human needed | `check_trends`, `diagnose_system`, all `read_*` / `list_*` / `status_*` |
| `notify` | Runs immediately, reports what it did via Telegram | `run_benchmark`, `run_security_audit` |
| `approve` | Creates a proposal, waits for human sign-off | `cleanup_system`, `deploy_repo`, all self-modification, all community bundle tools |

Self-improvement operations (tool synthesis, config changes, optimization adoption, provider switching) ALWAYS require `approve` tier regardless of other settings.

### Approval Authentication (Codex fix: not just one-way Telegram)

**Codex finding:** "Telegram approval is underspecified. No authenticated command channel, no replay protection."

**Design:**

```python
# core/approval/auth.py

class ApprovalAuth:
    """Authenticated approval channel via Telegram.

    Problem: Current alert.sh is one-way send. We need two-way
    authenticated commands.

    Solution: Chaguli already processes Telegram messages and can
    call AgentHarness tools. Approval works through this existing
    channel — NOT a new direct Telegram bot.

    Flow:
    1. AgentHarness creates proposal → JSON in proposals/
    2. Alert sent via Telegram (one-way, same as today)
    3. User tells Chaguli "approve 001"
    4. Chaguli calls AgentHarness's approve_proposal tool
    5. Tool validates:
       - Proposal exists and is still pending
       - Proposal hasn't expired
       - Proposal state hasn't changed since creation (hash check)
       - Caller is authorized (Chaguli's Telegram allowFrom already gates this)
    6. On approval: mark proposal approved, queue for execution

    CLI approval (when SSH'd in):
    - agentharness approve <id> — no additional auth needed (you're on the box)

    No HMAC tokens for Telegram — Chaguli's existing Telegram auth
    (allowFrom chat ID validation) provides the authentication boundary.
    We don't bypass it with a separate channel.
    """
```

**State revalidation at execution time:**
```python
def execute_proposal(proposal):
    """Called by scheduler when executing an approved proposal."""
    # Re-check conditions before executing
    if proposal.has_preconditions():
        current = evaluate_preconditions(proposal.preconditions)
        if not current.still_valid():
            # e.g., "disk at 87%" was the reason, but it's now 72%
            proposal.status = "stale"
            alert("Proposal #{id} conditions changed — skipping. Re-propose if needed.")
            return
    # Execute via sandbox
    sandbox.run(proposal.tool, proposal.args, proposal.sandbox_mode)
```

### Proposal Lifecycle

1. Agent or subsystem creates proposal → JSON file in `proposals/`
2. Alert sent via Telegram with summary + action options
3. User responds via Chaguli ("approve 001") or CLI
4. Approval validated (existence, expiry, state hash, auth)
5. If approved → executes in next scheduler tick (respects time windows), with precondition revalidation
6. If rejected → logged, synthesizer learns the preference pattern
7. If no response → expires after 3 days, agent may re-propose if conditions worsen

**Proposal types:** `tool_execution`, `tool_synthesis`, `config_change`, `optimization_apply`, `provider_switch`, `trust_promotion`

### Sandbox Execution (Codex fix: drop "guarded mode")

**Codex finding:** "Guarded mode is not a sandbox. Regex-blocking rm -rf / is trivially bypassed."

**Response:** Codex is right. Dropped "guarded" as a pretend-sandbox. Two modes:

| Mode | Isolation | Use for |
|------|-----------|---------|
| `direct` | Host execution — full access, timeout enforced | All shipped bundle scripts (we wrote them, we trust them) |
| `containerized` | Ephemeral Docker container, destroyed after execution | Community bundles, `deploy_repo`, anything from an external source |

**Direct mode constraints (not pretending to be a sandbox):**
- Configurable timeout (default 300s)
- Runs as the AgentHarness service user (not root unless explicitly configured)
- All execution logged to metrics

**Containerized mode:**
- Minimal Docker image (`agentharness/sandbox:latest`)
- Mounts: reports dir (rw), scripts dir (ro)
- No Docker socket access, no host filesystem, no credentials
- Resource limits: `--memory=512m --cpus=1 --network=none` (configurable per-tool)
- Egress control: `--network=none` by default, specific tools can opt into `--network=bridge`
- Destroyed after execution

### Community Bundle Safety (Codex fix: better trust model)

**Codex finding:** "10 successful runs = trusted is a bad trust model."

**Revised trust model:**

1. All community bundle tools default to `approve` + `containerized`
2. Trust promotion requires ALL of:
   - 20+ successful runs (not 10)
   - Zero network egress violations (if network=none was set)
   - Zero writes outside expected output paths
   - Zero error rate spikes (>3x normal)
   - Manual user approval of the promotion proposal
3. Trust promotion only moves to `notify` + `containerized` (never to `direct`)
4. Community tools NEVER get `direct` sandbox mode — that's reserved for shipped bundles
5. Future: bundle signing + provenance verification (out of scope for v2, tracked as v3 item)

---

## Section 4: Feedback Loop

### Ownership Boundary (Codex fix: clear separation)

**Codex finding:** "The plan pulls feedback/distillation/bridging back into AgentHarness, creating duplicate loops."

**Clarification — what belongs where:**

| Concern | Owner | Why |
|---------|-------|-----|
| Agent memory (preferences, conversation history, knowledge) | Chaguli (memory.py) | Agent-level, requires reasoning context |
| Agent self-improvement (interaction analysis, response quality) | Chaguli (self_improve.py) | Agent-level, requires conversation access |
| Agent briefings (scheduling, formatting, delivery) | Chaguli (briefings.py) | Agent decides what/when to brief |
| **Infrastructure metrics** (disk, RAM, container health, LLM budget) | **AgentHarness** (distiller.py) | Pure infrastructure data collection |
| **Infrastructure patterns** (repeated tool failures, alert fatigue) | **AgentHarness** (synthesizer.py) | Tool execution patterns, not agent reasoning |
| **Optimization scouting** (new models, engines, techniques) | **AgentHarness** (scout.py) | Hardware/infra domain, not agent domain |

**The bridge is one-way data flow, not a control loop:**
```
AgentHarness generates infrastructure data
    → writes to well-known file locations
        → Agent's modules consume at their own pace

AgentHarness does NOT:
    - Read agent memory
    - Modify agent behavior
    - Make decisions about what the agent should do
    - Duplicate agent-level self-improvement
```

The distiller does NOT use an LLM to "summarize" — it produces structured JSON from metrics data. If an LLM is needed for natural-language briefing formatting, that's the agent's job when it reads the JSON.

### Distiller — Infrastructure Data Compilation

Runs every night during offline window. **Pure data aggregation — no LLM needed.**

**Inputs:** scheduler logs, tool run reports, alert queue, proposal history, metrics, budget history

**Output:** `briefings/YYYY-MM-DD.json` containing structured data:
- Health status (checks run/passed/failed, failure details)
- Resource trends (disk, RAM, swap — current + projection)
- LLM usage (calls per provider, remaining budget, cloud spend)
- Proposal activity (created/approved/rejected/pending)
- Optimization findings from scout
- Anomalies (patterns that need attention)
- Action items (prioritized)

**Morning delivery (7:20 AM PT):**
1. `alert.sh flush` — send queued overnight alerts
2. Send structured summary via Telegram (formatted by a template, not LLM)
3. Agent's briefings.py can read the JSON for richer conversational delivery

### Log Retention (Codex fix: don't destroy audit trail)

**Codex finding:** "Purging raw logs after 7 days while keeping summaries is an audit failure."

**Revised retention:**
- Raw logs: keep 30 days (not 7) — enough to debug any bad summary/alert/proposal
- Daily briefing JSON: keep 90 days
- Weekly summaries: auto-generated, keep 1 year
- Monthly digests: keep forever
- Metrics data: keep 90 days raw, aggregated summaries forever
- Old raw logs compress to `.gz` after 7 days to save disk

### Synthesizer — Pattern Detection

Watches operational patterns and proposes new tools via HITL.

**Detection patterns:**
- **Repetitive commands:** Same command structure 5+ times/week → propose permanent tool
- **Alert fatigue:** Same alert fires 10+ times without action → propose threshold adjustment
- **Failed patterns:** Tool fails predictably → propose pre-check
- **Missing tools:** Agent gets requests it can't handle → propose new tool
- **Optimization opportunities:** Cross-reference benchmarks with scout findings

### Preference Learning (Codex fix: bounded, not overfit)

**Codex finding:** "Approval/rejection history without strong context is not enough to learn safe policy changes."

**Revised approach:** The preference model is *advisory*, not *autonomous*:

1. It tracks patterns but NEVER auto-suppresses proposals
2. It surfaces observations: "You've rejected 3 similar container-restart proposals. Suppress future ones?"
3. The suppression itself is a proposal that requires `approve` tier
4. Minimum 5 data points before any pattern is surfaced
5. Patterns expire after 90 days of no new data points
6. User can clear all learned preferences via CLI: `agentharness preferences reset`

### Bridge — Agent Integration

File-based coupling (deliberate — no tight process coupling):

| AgentHarness writes | Location | Agent reads |
|---------------------|----------|-------------|
| Infrastructure briefing JSON | `briefings/` | Agent's briefing module picks up on next loop |
| Infrastructure insights | `insights_inbox/` | Agent's memory module can ingest if it wants to |
| Tool schema updates | `tool_updates/` | Agent's tool system hot-reloads |
| Alerts | Telegram (direct) | Immediate delivery |

**What the bridge does NOT do:**
- Write to agent memory directly (agent decides what to remember)
- Modify agent config
- Assume any specific agent internal architecture beyond "can read files from a directory"

For the Chaguli reference implementation, `agents/chaguli.py` writes to paths discovered by `discovery/agents.py`. For other agents, implementors extend `agents/base.py`.

---

## Section 5: Continuous Optimization

### Scout

Searches multiple sources weekly (online window) for new techniques, models, tools.

**Sources:**
- GitHub releases: llama.cpp, ik_llama.cpp, AMD Lemonade, etc.
- RSS/news: Phoronix (AMD/AI tags), other tech news
- HuggingFace: Model feed filtered by architecture + RAM budget
- Reddit: r/LocalLLaMA, r/selfhosted
- arxiv: Quantization/inference papers (via API)

### Evaluator

Scores each finding against current AND future hardware:

```json
{
  "finding": "Lemonade 10.1 — AMD GPU/NPU inference optimization",
  "applicable_now": false,
  "applicable_future": true,
  "future_hardware": "8745HS (780M iGPU + XDNA NPU)",
  "action": "bookmark — install when mini PC arrives",
  "confidence": "high",
  "tags": ["npu", "amd", "inference-engine"]
}
```

Proposals tagged `now` vs `future` — no spam for things you can't use yet.

### Tracker

Maintains `optimization_history.json`:
- What's been tried and the before/after benchmark results
- Source reliability scores (which repos/blogs consistently produce useful finds)
- Sources with high reliability get checked more frequently
- Sources producing noise get deprioritized

---

## Section 6: Registry + Bundles

### Why Evolve the Registry (Codex response)

**Codex finding:** "You are rebuilding loader/engine/schema/merge/bundles without proving the existing model is insufficient."

**Why the current model is insufficient:**
1. Single flat file — no way to install community tools without editing the master YAML
2. No schema validation — typos in bundle.yaml silently break checks
3. No merge semantics — can't layer user overrides on top of defaults
4. No `bundle install <url>` workflow — the user asked for plug-and-play extensibility

The evolution is incremental: `harness_registry.yaml` entries migrate into `bundles/core/bundle.yaml` and `bundles/homelab/bundle.yaml`. The engine gains a loader and schema validator. Not a ground-up rewrite.

### Registry Engine

Merges multiple YAML bundle files into a unified runtime registry.

**Load order:** `bundles/core/bundle.yaml` → `bundles/*/bundle.yaml` → `config/overrides.yaml`

**Merge rules:**
- Same tool name in two bundles → conflict error (user must resolve in overrides.yaml)
- Same check name → later bundle wins (with warning logged)
- User overrides always win
- Disabled entries stay disabled even if a bundle enables them

### Discovery-Generated Checks (Codex fix: separate from bundle YAML)

**Codex finding:** "Runtime-discovered state should not rewrite the declarative bundle definition."

**Response:** Agreed. Discovery-generated checks go to a **separate runtime file**, not into bundle.yaml:

```
bundles/homelab/
├── bundle.yaml              # Declarative, human-authored, version-controlled
├── scripts/
└── discovered.yaml          # Auto-generated, gitignored, rebuilt on every discovery run
```

The registry loader reads both: `bundle.yaml` (stable) + `discovered.yaml` (ephemeral). `discovered.yaml` is never committed, never manually edited, and rebuilt from scratch on each discovery run. No drift, no merge noise.

### Bundle Structure

Each bundle is self-contained:

```
bundles/<name>/
├── bundle.yaml          # Checks, tools, harnesses definitions
├── discovered.yaml      # Auto-generated runtime checks (gitignored)
└── scripts/             # Bundle-specific scripts
```

**Shipped bundles:** core, homelab, inference, security, backup, dashboard

### Bundle CLI

```bash
agentharness bundle install <url>     # Clone + validate + sandbox
agentharness bundle list              # Show active bundles + stats
agentharness bundle disable <name>    # Deactivate
agentharness bundle test <name>       # Dry-run all checks in sandbox
```

### Registry Schema Validation

Every entry validated against `schema.py`:
- Checks: must have name, command, type (threshold/command_exit/command_output/regex_match/http_probe)
- Tools: must have name, description, command_or_script; optional approval_tier, sandbox_mode, budget_hint
- Harnesses: must have name, script, frequency; optional window, depends_on, timeout

Invalid entries are rejected with a clear error message at load time, not silently ignored.

---

## Section 7: Observability

### Self-Watchdog

`heartbeat.py` writes a timestamp file on every scheduler tick. A separate systemd timer checks the timestamp every 5 minutes. If stale (>20 min), alerts and attempts to restart the scheduler.

### Metrics

Every tool call, check, and harness run gets logged to `metrics.jsonl` (append-only JSONL, not a single JSON file):
- Tool calls: name, duration, success/fail, provider used, sandbox mode
- Checks: name, value, status (ok/warn/critical), timestamp
- Proposals: lifecycle events (created, approved, rejected, executed, expired)

`metrics.summary(days=7)` provides aggregates for the distiller and dashboard.

### Dashboard (Optional Bundle)

Single-file FastAPI app. Server-rendered HTML (Jinja2, inline CSS). No JS framework, no build step.

Endpoints: health status, metrics summary, pending proposals, recent briefings, LLM budget.

LAN-only by default. Auth via bearer token from `agentharness.yaml`.

---

## Section 8: Install + CLI

### Installer Phases (Codex fix: agent discovery early)

**Codex finding:** "Agent integration deferred to phase 10, but current install depends on Chaguli paths earlier."

**Revised phase order:**

```
Phase 0:  Full discovery → populate state.json
          Includes: paths, hardware, services, agents, credentials (opt-in)
          Agent discovery happens HERE, not at phase 10.
Phase 1:  Install dependencies (reads state.json for paths)
Phase 2:  Build inference engines (if inference bundle active)
Phase 3:  Download models by RAM budget (from hardware discovery)
Phase 4:  Set up SearXNG (if scout needs it)
Phase 5:  Set up systemd services (scheduler, watchdog, LLM servers)
Phase 6:  Benchmark + auto-select best config
Phase 7:  Config setup (symlink to agent's .env if found, suggest free tiers)
Phase 8:  Smart scheduler + registry engine
Phase 9:  Bundle activation + validation
Phase 10: Agent bridge generation (uses agent data from Phase 0 discovery)
Phase 11: Validate entire installation
```

Every phase reads from `state.json`. Every phase can be re-run independently. `--dry-run` previews all changes.

### Scheduler Migration (Codex fix: no duplicate runners)

**Codex finding:** "Easy outcome: duplicate runners, duplicate alerts, and corrupt shared state."

**Migration plan:**
1. Phase A builds the Python scheduler alongside the existing bash scheduler
2. Both read the same registry, but the Python scheduler is initially disabled
3. `agentharness migrate-scheduler` command:
   - Stops the cron entry for `scheduler.sh`
   - Enables the systemd service for `scheduler.py`
   - Validates one successful tick
   - Removes the cron entry
4. Rollback: `agentharness migrate-scheduler --rollback` reverses the process
5. `scheduler.sh` is kept in the repo as a fallback but marked deprecated

No period where both run simultaneously.

### CLI

```bash
agentharness                              # Status summary
agentharness discover                     # Re-run full discovery
agentharness proposals                    # List pending proposals
agentharness approve/reject <id>          # Manage proposals
agentharness bundle list/install/disable  # Manage bundles
agentharness budget                       # LLM budget status
agentharness health                       # Current health checks
agentharness briefing                     # Latest briefing
agentharness run <tool>                   # Manually run a tool
agentharness migrate-scheduler            # Migrate from bash to Python scheduler
agentharness preferences reset            # Clear learned preference patterns
```

---

## Section 9: Credential Security (Codex fix: opt-in + audit)

**Codex finding:** "`credentials.py` is secret harvesting disguised as convenience."

### Design

Credential discovery is **opt-in and audited**:

1. **Off by default.** First install asks: "Scan for API keys and service credentials? (y/n)"
2. **Scoped.** User specifies which directories to scan in `agentharness.yaml`:
   ```yaml
   credentials:
     enabled: false
     scan_paths:
       - "${CHAGULI_DIR}/.env"
       - "/opt/docker-configs/"
     exclude_paths:
       - "/home/*/.*"
       - "/root/"
   ```
3. **Read-only.** AgentHarness reads credentials to configure providers and service checks. It NEVER writes, rotates, or modifies credentials.
4. **Not stored.** Discovered credentials are resolved into provider configs at runtime, not persisted to state.json. The state file stores "groq_api_key_source: /path/to/.env", not the key itself.
5. **Audit logged.** Every credential access logged to `metrics.jsonl`:
   ```json
   {"type": "credential_access", "source": "/opt/chaguli/.env",
    "key_name": "GROQ_API_KEY", "accessed_by": "providers/groq.py",
    "timestamp": "2026-04-07T14:30:00"}
   ```
6. **Alert on new credentials.** If a scan finds a new credential not previously seen, alert via Telegram: "New API key found: OPENAI_API_KEY in /opt/chaguli/.env. Enable OpenAI provider? (approve/ignore)"

---

## Section 10: Error Handling (Codex fix: define failure behavior)

**Codex finding:** "Error handling is missing almost everywhere."

### Error Taxonomy

| Error | Behavior |
|-------|----------|
| `docker` not found | Homelab bundle disabled, alert, other bundles work fine |
| `sensors` not found | CPU temperature check disabled, other checks unaffected |
| Multiple discovery matches for same path | Use first match, log warning with all candidates |
| Corrupt state.json | Rebuild from scratch via full discovery, alert |
| Corrupt metrics.jsonl | Rotate to metrics.jsonl.corrupt, start fresh, alert |
| Provider auth error | Disable provider, alert, router skips it |
| Provider rate limit (429) | Exponential backoff (1s, 2s, 4s), max 3 retries, then skip |
| Provider timeout | Skip, try next provider |
| All providers down | Queue request if LOW/MEDIUM, alert if HIGH/CRITICAL |
| Partial proposal execution (tool starts but fails mid-run) | Mark proposal `failed`, log output, alert, do NOT retry automatically |
| Scheduler crash | Watchdog detects within 5 min, restarts, alerts |
| Discovery finds agent but can't identify capabilities | Bridge generates minimal adapter (alert-only), logs what it couldn't detect |

### Principle: Fail Narrow, Not Wide

A failure in one subsystem should not take down others. Missing Docker doesn't break LLM benchmarking. A broken provider doesn't stop health checks. The scheduler continues even if one harness fails.

Every script and tool invocation captures exit code, stdout, stderr. Failures are:
1. Logged to metrics.jsonl
2. Alerted if severity warrants
3. Surfaced in the next briefing
4. Never silently swallowed with `|| true`

### Resilience — Unattended Homelab Operation

The primary demographic for AgentHarness is homelab users. The tool must be one less thing to worry about — it should recover from failures without user intervention.

**Auto-restart:** Scheduler runs as a systemd service with `Restart=on-failure`, `RestartSec=30`, and a crash loop limit (`StartLimitBurst=5` in 10 minutes). If it crashes 5 times rapidly, it stops and sends a critical alert.

**Self-watchdog:** A separate systemd timer checks the scheduler heartbeat every 5 minutes. The scheduler writes a `heartbeat.json` (timestamp + PID) on every tick. If stale >20 minutes, the watchdog alerts and attempts restart.

**Crash-safe queues:** All JSON queue files (alerts, tasks, proposals) use atomic write (tmp-then-rename with file locking). Corrupt files are backed up as `.corrupt` and replaced with empty defaults. No data loss on process crash or power failure.

**Stale lock recovery:** If the process crashes while holding a file lock, the lock file persists with a dead PID. Before every state write, the system checks if the lock PID is alive. Dead PID locks are automatically removed.

**Circuit breaker for alert fatigue:** When a health check fails N consecutive times (default 5), the circuit "opens" and the check is suppressed — no more alert spam for a removed service. When discovery runs and detects service changes, all circuits reset so checks get re-evaluated.

**Startup self-test:** On every boot and scheduler start, a quick validation runs: can we read state.json? Can we write to data dirs? Is Python adequate? Is Docker available? Are there stale locks? Results are logged, and failures alert immediately.

**Config backup before changes:** Before any proposal execution, bundle install, or self-update modifies configuration, the current config is snapshotted to `config_backups/`. If something breaks, `agentharness config restore <snapshot>` reverts. Old snapshots auto-purge (keep 10).

**Self-update safety:** `self_update.sh` snapshots state before updating, runs `--dry-run` first, validates after update, and auto-rollbacks if validation fails.

**Log rotation:** Logrotate config keeps 30 days of logs, compresses after 1 day. Prevents disk fill on unattended systems.

**Dependency degradation:** If Docker daemon dies, Python is missing, or disk is full, the scheduler detects this at the start of each tick and degrades gracefully — runs what it can, skips what it can't, alerts on what's broken.

---

## Section 11: Agent Bridge — Addressing Open Questions

**Codex finding:** "Open questions are on critical path, not 'open questions.'"

### Chaguli Integration Strategy

The bridge design does NOT depend on Chaguli having a webhook or memory API. It works with the lowest-common-denominator assumption: **file-based communication**.

```
Bridge contract (any agent):
  1. AgentHarness writes JSON files to a known directory
  2. Agent reads them at its own pace
  3. Agent deletes files after processing (or AgentHarness cleans up after TTL)

That's it. No webhook needed. No memory API needed.
```

**For Chaguli specifically:**
- Phase A discovery probes Chaguli's container to find what's available
- If Chaguli has a webhook → bridge uses it (opportunistic upgrade)
- If Chaguli's memory.py has a file inbox → bridge writes there
- If neither → bridge writes to a shared volume, Chaguli can be patched later

The bridge generates a **capability report** after discovery:
```json
{
  "agent": "chaguli",
  "communication": {
    "file_inbox": "/opt/chaguli/inbox/",
    "webhook": null,
    "memory_api": null,
    "telegram": true
  },
  "tools_integration": "patched_tools_py",
  "capabilities_detected": ["heartbeat", "briefings", "memory", "self_improve"]
}
```

This report tells the user exactly what integration level was achieved and what's missing.

### Open Questions (Remaining)

1. **GitLab vs GitHub:** Confirm hosting for open-source release
2. **License:** MIT, Apache 2.0, or AGPL?
3. **Python version floor:** 3.10+ recommended (match/case, modern typing)

---

## Migration Path from v1

1. **Scripts rewritten** (not wrapped) to use discovery paths via `common.sh` preamble
2. `harness_registry.yaml` entries split into `bundles/core/bundle.yaml` and `bundles/homelab/bundle.yaml`
3. `integrate_chaguli.sh` logic moves into `core/agents/chaguli.py`
4. `discover_*.sh` scripts become reference implementations for `core/discovery/*.py`
5. `alert.sh` preserved as the Telegram transport, called by bridge and approval gateway
6. `scheduler.sh` replaced by `core/scheduler/scheduler.py` via explicit migration command (no simultaneous operation)
7. README.md rewritten to reflect new architecture

---

## Codex Review Response Summary

| # | Codex Finding | Response | Where Fixed |
|---|--------------|----------|-------------|
| 1 | No vertical slice | Added 4-phase delivery plan | Phased Delivery Plan section |
| 2 | Legacy scripts need rewrite, not wrap | Scripts rewritten with discovery preamble | Section 1: Script Rewrite |
| 3 | Ownership boundary violation | Clarified infra vs agent concerns | Section 4: Ownership Boundary |
| 4 | Open questions are critical path | Bridge works with file-only assumption | Section 11 |
| 5 | state.json race conditions | StateManager with file locking + atomic writes | Section 1: State Management |
| 6 | Discovery rewrites bundle YAML | Separate discovered.yaml (gitignored) | Section 6 |
| 7 | credentials.py is secret harvesting | Opt-in, scoped, audit-logged, not persisted | Section 9 |
| 8 | ~2,800 req/day is fiction | Documented as estimate, router doesn't depend on it | Section 2: Free Tier |
| 9 | capacity/cost abstractions unrealistic | Replaced with budget_status() returning what's knowable | Section 2: Provider Interface |
| 10 | Budget reserve/commit concurrency | Replaced with record-after-the-fact | Section 2: Budget Layer |
| 11 | Telegram approval underspecified | Routes through Chaguli's existing auth, precondition revalidation | Section 3: Authentication |
| 12 | Guarded mode is fake sandbox | Dropped guarded mode, only direct + containerized | Section 3: Sandbox |
| 13 | Community trust model too weak | 20 runs + violation checks + manual approval + never direct | Section 3: Community Safety |
| 14 | Installer sequencing wrong | Agent discovery moved to Phase 0 | Section 8 |
| 15 | Duplicate scheduler risk | Explicit migration command, no simultaneous operation | Section 8: Migration |
| 16 | Registry rebuild is churn | Justified: single file can't support bundles/install/validation | Section 6: Why Evolve |
| 17 | Preference model overfit | Advisory only, never auto-suppresses, requires approval | Section 4: Preference Learning |
| 18 | Log retention audit failure | Raw logs 30 days (not 7), compressed after 7 | Section 4: Log Retention |
| 19 | Error handling missing everywhere | Full error taxonomy + "fail narrow" principle | Section 10 |
| 20 | "Any agent" is false | Clarified: file-based contract, not generic | Section 11 |
| 21 | Platform not generic | Added to Non-Goals: Linux-only for v2 | Non-Goals |
