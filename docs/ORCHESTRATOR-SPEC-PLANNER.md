# Planner-Executor Orchestrator Implementation Spec

> **NOTE**: This covers the **planner-executor orchestrator** (cognitive tier classification + plan-execute loop), which is **NOT YET STARTED**. The hub-and-spoke agent architecture is **COMPLETE** — see `ORCHESTRATOR-HUB-DESIGN.md`.

**Status:** APPROVED (not started)
**Date:** 2026-04-25
**Repo:** AgentHarness (proxy changes) + AgentRocki/Hermes (orchestrator logic)
**Design:** See `ORCHESTRATOR-DESIGN.md` for full design rationale.

## How To Use This Spec

This spec is broken into **numbered atomic tasks**. Each task:
- Is self-contained (all file paths, code snippets, and context included)
- Can be completed in one session (~10-20 min each)
- Ends with a verification step and a git commit
- Has no dependency on conversation history from prior tasks

**If your session is interrupted:** Look at the task list below. Find the last committed task (check `git log --oneline -5`). Start the next task.

**Convention:** Each task title includes `[REPO]` to indicate which repo to work in.

---

## Key File Paths

```
PROXY:     /home/rohit/agentharness/core/providers/proxy_server.py
COMPAT:    /home/rohit/agentharness/core/providers/anthropic_compat.py
HERMES:    /home/rohit/.hermes/hermes-agent/
RUN_AGENT: /home/rohit/.hermes/hermes-agent/run_agent.py
PLUGINS:   /home/rohit/.hermes/plugins/
ENV:       /home/rohit/agentharness/data/.env
ORCH_DIR:  /home/rohit/.hermes/orchestrator/
LOCAL_LLM: http://localhost:8081 (Qwen 2.5 7B, CPU-only, 4096 ctx)
PROXY_URL: http://localhost:8080
```

## Current Provider Order (verify before changing)

**Tool-calling cascade** in proxy_server.py `_TOOL_PROVIDERS` list (~line 870):
```
groq > google-alt > cerebras > sambanova > fireworks > openrouter > google-primary
```
**Note:** Rohit may NOT have a `GOOGLE_FREE_API_KEY` for google-alt. Check `data/.env` before relying on it. If absent, skip google-alt in EXECUTE tier routing.

**Plain-chat cascade** uses the Router class from `_get_router()` (~line 340).

---

## PHASE 1: Cognitive Classifier + Tier Routing

### Task 1.1: Create the classifier module [HERMES]

**Goal:** Create `cognitive_router.py` that classifies a user message into one of 4 tiers by calling the local LLM.

**File to create:** `/home/rohit/.hermes/hermes-agent/agent/cognitive_router.py`

**Code:**

```python
"""Cognitive request classifier.

Calls the local LLM to classify a user message as CHAT, EXECUTE,
REASON, or PLAN_NEEDED.  Used by the orchestrator to select the
right model tier for each request.
"""

import logging
import time
import httpx

log = logging.getLogger(__name__)

# Tiers in order of cost (cheapest first)
TIER_CHAT = "CHAT"
TIER_EXECUTE = "EXECUTE"
TIER_REASON = "REASON"
TIER_PLAN = "PLAN_NEEDED"

VALID_TIERS = {TIER_CHAT, TIER_EXECUTE, TIER_REASON, TIER_PLAN}

# Default tier when classifier fails or is unreachable
DEFAULT_TIER = TIER_EXECUTE

_CLASSIFY_PROMPT = """Classify this user request into exactly one category.
Reply with ONLY the category name, nothing else.

- CHAT: casual conversation, greetings, questions about yourself, thanks, acknowledgments
  Examples: "hello", "how are you", "what can you do", "thanks", "good morning"
- EXECUTE: a single action that needs one tool call (run a command, read a file, check status)
  Examples: "restart n8n", "show me docker ps", "read /etc/hosts", "check disk space"
- REASON: needs analysis or explanation but can be answered in one model response
  Examples: "why is my DNS slow", "explain the proxy routing", "what does this error mean"
- PLAN_NEEDED: complex task requiring multiple steps, investigation, or fixing something broken
  Examples: "fix n8n it's in a boot loop", "set up a new monitoring service", "debug why backups are failing", "migrate the database to a new volume"

When uncertain between adjacent tiers, default to the cheaper one.
Exception: if the request mentions something being broken, failing, crashing,
or needing fixing, default to PLAN_NEEDED.

User request: "{message}"

Category:"""

# Max chars of user message to include in classification prompt.
# Keeps us well within the local LLM's 4096 context window.
_MAX_MESSAGE_CHARS = 500

# Timeout for local LLM classification call
_CLASSIFY_TIMEOUT_S = 10.0

# Local LLM endpoint
_LOCAL_LLM_URL = "http://localhost:8081/v1/chat/completions"


def classify(message: str) -> str:
    """Classify a user message into a cognitive tier.

    Returns one of: CHAT, EXECUTE, REASON, PLAN_NEEDED.
    On any failure, returns DEFAULT_TIER (EXECUTE).
    """
    if not message or not message.strip():
        return TIER_CHAT

    # Fast-path: slash commands and very short messages
    stripped = message.strip()
    if stripped.startswith("/"):
        return TIER_EXECUTE
    if len(stripped.split()) <= 3:
        # Very short messages are almost always chat or simple execute
        _action_words = {"restart", "stop", "start", "check", "show",
                         "read", "run", "kill", "fix", "debug", "deploy"}
        if any(w in stripped.lower() for w in _action_words):
            return TIER_EXECUTE
        return TIER_CHAT

    # Truncate long messages for classification
    truncated = stripped[:_MAX_MESSAGE_CHARS]

    prompt = _CLASSIFY_PROMPT.format(message=truncated)

    try:
        start = time.monotonic()
        with httpx.Client(timeout=_CLASSIFY_TIMEOUT_S) as client:
            resp = client.post(
                _LOCAL_LLM_URL,
                json={
                    "model": "local",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 20,
                    "temperature": 0.0,
                },
            )
        elapsed = time.monotonic() - start
        log.info("Classifier latency: %.1fs", elapsed)

        if resp.status_code != 200:
            log.warning("Classifier HTTP %d, using default tier", resp.status_code)
            return DEFAULT_TIER

        data = resp.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
            .upper()
        )

        # Extract tier from response (model might include extra text)
        for tier in VALID_TIERS:
            if tier in content:
                log.info("Classified as %s (raw: %s)", tier, content[:50])
                return tier

        log.warning("Unrecognized classification: %s, using default", content[:50])
        return DEFAULT_TIER

    except httpx.TimeoutException:
        log.warning("Classifier timed out (%.1fs), using default tier", _CLASSIFY_TIMEOUT_S)
        return DEFAULT_TIER
    except Exception as exc:
        log.warning("Classifier error: %s, using default tier", exc)
        return DEFAULT_TIER
```

**Verify:**
```bash
cd /home/rohit/.hermes/hermes-agent
./venv/bin/python3 -c "
from agent.cognitive_router import classify
# Should be fast-path
assert classify('hello') == 'CHAT'
assert classify('/status') == 'EXECUTE'
assert classify('restart n8n') == 'EXECUTE'
print('Fast-path tests passed')
# Full classify (needs local LLM running)
result = classify('fix n8n it is in a boot loop and keeps crashing')
print(f'Full classify result: {result}')
"
```

**Commit:**
```bash
cd /home/rohit/.hermes/hermes-agent
git add agent/cognitive_router.py
git commit -m "feat: add cognitive request classifier for tier-based routing

Classifies user messages as CHAT/EXECUTE/REASON/PLAN_NEEDED using local
LLM. Fast-path bypass for slash commands and short messages. Falls back
to EXECUTE tier on any classifier failure."
git push
```

---

### Task 1.2: Create the 50-message test set [HERMES]

**Goal:** Create a test file with 50 labeled messages to measure classifier accuracy. This is the GATE for Phase 1.

**File to create:** `/home/rohit/.hermes/hermes-agent/tests/test_classifier_accuracy.py`

**Code:**

```python
"""Classifier accuracy test.

Run: cd /home/rohit/.hermes/hermes-agent && ./venv/bin/python3 tests/test_classifier_accuracy.py

GATE: Must achieve >= 90% accuracy before proceeding to Task 1.3.
If < 90%, either tune the prompt in cognitive_router.py or simplify
to 2-way (SIMPLE vs COMPLEX) classification.
"""

import sys
sys.path.insert(0, ".")

from agent.cognitive_router import classify

# (message, expected_tier)
TEST_CASES = [
    # CHAT (15 examples)
    ("hello", "CHAT"),
    ("hi there", "CHAT"),
    ("good morning", "CHAT"),
    ("thanks", "CHAT"),
    ("how are you", "CHAT"),
    ("what can you do", "CHAT"),
    ("who are you", "CHAT"),
    ("nice work", "CHAT"),
    ("ok", "CHAT"),
    ("got it", "CHAT"),
    ("tell me a joke", "CHAT"),
    ("what's the weather like", "CHAT"),
    ("good night", "CHAT"),
    ("lol", "CHAT"),
    ("I appreciate that", "CHAT"),

    # EXECUTE (15 examples)
    ("restart n8n", "EXECUTE"),
    ("show me docker ps", "EXECUTE"),
    ("check disk space", "EXECUTE"),
    ("read /etc/hosts", "EXECUTE"),
    ("what's the CPU usage", "EXECUTE"),
    ("list running containers", "EXECUTE"),
    ("show me the last 20 lines of the proxy log", "EXECUTE"),
    ("ping google.com", "EXECUTE"),
    ("check if pihole is running", "EXECUTE"),
    ("show me the crontab", "EXECUTE"),
    ("cat /home/rohit/agentharness/data/.env", "EXECUTE"),
    ("how much RAM is free", "EXECUTE"),
    ("show docker events from the last hour", "EXECUTE"),
    ("what's listening on port 8080", "EXECUTE"),
    ("stop the n8n container", "EXECUTE"),

    # REASON (10 examples)
    ("why is my DNS slow", "REASON"),
    ("explain how the proxy routing works", "REASON"),
    ("what does this error mean: connection refused on port 5432", "REASON"),
    ("compare Groq and Cerebras for tool calling reliability", "REASON"),
    ("what's the difference between docker stop and docker kill", "REASON"),
    ("why do free providers return empty responses after tool calls", "REASON"),
    ("explain the Hermes fallback provider chain", "REASON"),
    ("what would happen if I increase the proxy cache TTL to 300s", "REASON"),
    ("summarize the last week of Docker events", "REASON"),
    ("what are the pros and cons of running llama locally vs using Groq", "REASON"),

    # PLAN_NEEDED (10 examples)
    ("fix n8n, it's in a boot loop", "PLAN_NEEDED"),
    ("set up a new monitoring dashboard for all containers", "PLAN_NEEDED"),
    ("debug why the nightly backup is failing silently", "PLAN_NEEDED"),
    ("migrate the n8n database to a larger volume", "PLAN_NEEDED"),
    ("the proxy keeps crashing every few hours, investigate and fix", "PLAN_NEEDED"),
    ("set up automatic SSL certificate renewal for all services", "PLAN_NEEDED"),
    ("pihole stopped resolving DNS and I can't figure out why", "PLAN_NEEDED"),
    ("create a new Docker service for SearXNG with persistent storage and reverse proxy", "PLAN_NEEDED"),
    ("something is wrong with the homelab, nothing is working since last night", "PLAN_NEEDED"),
    ("optimize the proxy to reduce latency for tool calls", "PLAN_NEEDED"),
]


def run_accuracy_test():
    correct = 0
    wrong = []
    total = len(TEST_CASES)

    for i, (message, expected) in enumerate(TEST_CASES):
        result = classify(message)
        if result == expected:
            correct += 1
            status = "OK"
        else:
            wrong.append((message, expected, result))
            status = f"WRONG (got {result})"
        print(f"  [{i+1:2d}/{total}] {expected:12s} -> {result:12s} {status}  | {message[:60]}")

    accuracy = correct / total * 100
    print(f"\n{'='*60}")
    print(f"ACCURACY: {correct}/{total} = {accuracy:.1f}%")
    print(f"{'='*60}")

    if wrong:
        print(f"\nMisclassified ({len(wrong)}):")
        for msg, exp, got in wrong:
            print(f"  Expected {exp}, got {got}: {msg[:80]}")

    if accuracy >= 90:
        print("\n>>> GATE PASSED. Proceed to Task 1.3.")
    else:
        print("\n>>> GATE FAILED. Tune the classifier prompt or simplify to 2-way.")
        print("    Options:")
        print("    1. Adjust examples/prompt in cognitive_router.py and re-run")
        print("    2. Simplify to SIMPLE (CHAT+EXECUTE) vs COMPLEX (REASON+PLAN_NEEDED)")
        print("    3. Use Groq instead of local LLM for classification")

    return accuracy >= 90


if __name__ == "__main__":
    passed = run_accuracy_test()
    sys.exit(0 if passed else 1)
```

**Verify:**
```bash
cd /home/rohit/.hermes/hermes-agent
./venv/bin/python3 tests/test_classifier_accuracy.py
```

**Commit:**
```bash
cd /home/rohit/.hermes/hermes-agent
git add tests/test_classifier_accuracy.py
git commit -m "test: add 50-message classifier accuracy test (Phase 1 gate)

Must achieve >= 90% accuracy before integrating tier routing into
the proxy. Covers CHAT (15), EXECUTE (15), REASON (10), PLAN_NEEDED (10)."
git push
```

**STOP HERE if accuracy < 90%.** Tune the classifier prompt in `cognitive_router.py` and re-run until it passes. Do not proceed to Task 1.3 until the gate passes.

---

### Task 1.3: Add tier routing to the proxy [AGENTHARNESS]

**Goal:** Make `proxy_server.py` read a `cognitive_tier` field from the request body and reorder provider priorities accordingly.

**File to modify:** `/home/rohit/agentharness/core/providers/proxy_server.py`

**What to change:**

**Step A:** Find the `chat_completions` handler function (the `@app.post("/v1/chat/completions")` handler, around line 670). Near the top, after `body = await request.json()`, add tier extraction:

```python
        # Cognitive tier hint from Hermes orchestrator
        cognitive_tier = body.pop("cognitive_tier", None)  # pop so upstream providers don't see it
        if cognitive_tier:
            log.info("Cognitive tier hint: %s", cognitive_tier)
```

**Step B:** Find the section that handles tool calls (the `if tools:` block around line 742). Before the call to `_tool_call_passthrough`, add:

```python
        if tools:
            # ... existing chat-only detection code stays ...

            resp = await _tool_call_passthrough(
                body, messages, max_tokens, temperature, tools, tool_choice,
                cognitive_tier=cognitive_tier,  # ADD THIS PARAMETER
            )
```

**Step C:** Modify `_tool_call_passthrough` signature (around line 922) to accept the tier:

Change:
```python
    async def _tool_call_passthrough(
        body: dict,
        messages: list,
        max_tokens: int,
        temperature: float,
        tools: list,
        tool_choice: Any | None,
    ) -> JSONResponse:
```

To:
```python
    async def _tool_call_passthrough(
        body: dict,
        messages: list,
        max_tokens: int,
        temperature: float,
        tools: list,
        tool_choice: Any | None,
        cognitive_tier: str | None = None,
    ) -> JSONResponse:
```

**Step D:** Inside `_tool_call_passthrough`, after the existing provider list setup, add tier-based reordering. Find where `_TOOL_PROVIDERS` is first referenced in the function (the loop that tries each provider). Before that loop, add:

```python
        # Reorder providers based on cognitive tier hint
        providers_to_try = list(_TOOL_PROVIDERS)
        if cognitive_tier == "REASON" or cognitive_tier == "PLAN_NEEDED":
            # Move google-primary to front for reasoning tasks
            providers_to_try.sort(
                key=lambda p: 0 if "google-primary" in p[0] else 1
            )
            log.info("Tier %s: google-primary prioritized", cognitive_tier)
        elif cognitive_tier == "CHAT":
            # Chat shouldn't have tools, but if it does, use cheapest
            providers_to_try.sort(
                key=lambda p: 0 if p[0] in ("groq", "cerebras") else 1
            )
            log.info("Tier CHAT: cheapest providers prioritized")
        # EXECUTE uses default order (already optimized for cost)
```

Then change the loop to iterate over `providers_to_try` instead of `_TOOL_PROVIDERS`.

**Step E:** For the plain-chat routing path (the section after `if tools:` that uses the Router), add tier handling. Find the complexity classification section (around line 810):

```python
        # Original complexity logic
        token_estimate = len(prompt.split())
        if token_estimate < 5:
            complexity = Complexity.LOW
        ...
```

Add tier override BEFORE this:

```python
        # Tier override from cognitive classifier
        if cognitive_tier == "REASON" or cognitive_tier == "PLAN_NEEDED":
            complexity = Complexity.HIGH  # forces routing to stronger model
        elif cognitive_tier == "CHAT":
            complexity = Complexity.LOW   # forces routing to local/free
        else:
            # Original complexity logic (EXECUTE or no tier hint)
            token_estimate = len(prompt.split())
            ...
```

**Verify:**
```bash
# Test that the proxy still starts and handles requests
cd /home/rohit/agentharness
set -a && source data/.env && set +a
./venv/bin/python3 -c "
import asyncio, json
from core.providers.proxy_server import app
print('Proxy module loads OK')
"

# Test with curl (after restarting proxy)
curl -s http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"test","messages":[{"role":"user","content":"hello"}],"cognitive_tier":"CHAT"}' \
  | python3 -m json.tool | head -5
```

**Commit:**
```bash
cd /home/rohit/agentharness
git add core/providers/proxy_server.py
git commit -m "feat: add cognitive_tier routing to proxy

Reads cognitive_tier field from request body (CHAT/EXECUTE/REASON/
PLAN_NEEDED). Reorders provider cascade: REASON/PLAN_NEEDED prioritize
Gemini Pro, CHAT prioritizes cheapest providers. EXECUTE uses default
order. Field is popped from body so upstream providers never see it."
git push
```

---

### Task 1.4: Wire classifier into Hermes [HERMES]

**Goal:** Make Hermes call the classifier before each model request and pass the tier hint to the proxy.

**File to modify:** `/home/rohit/.hermes/hermes-agent/run_agent.py`

**What to change:**

Find the section in `run_agent.py` where API kwargs are built for the model call. This is likely in the main conversation loop, around where `api_kwargs` dict is assembled with `model`, `messages`, `tools`, etc. (Search for `"tools": self.tools` around line 3025.)

**Step A:** Add import at top of file:
```python
from agent.cognitive_router import classify as classify_cognitive_tier
```

**Step B:** Before the API call is made (where `api_kwargs` is finalized), add:

```python
            # Cognitive tier classification (only for proxy-routed requests)
            if "localhost:8080" in str(getattr(self, 'base_url', '')):
                _last_user_msg = ""
                for _m in reversed(api_kwargs.get("messages", [])):
                    if _m.get("role") == "user":
                        _content = _m.get("content", "")
                        if isinstance(_content, str):
                            _last_user_msg = _content
                        break
                if _last_user_msg:
                    _tier = classify_cognitive_tier(_last_user_msg)
                    api_kwargs["cognitive_tier"] = _tier
```

**Important:** This should ONLY fire when talking to the local proxy (localhost:8080), not when using the Google fallback directly.

**Verify:**
```bash
# Check Hermes starts cleanly
cd /home/rohit/.hermes/hermes-agent
./venv/bin/python3 -c "
import run_agent
print('run_agent imports OK')
from agent.cognitive_router import classify as classify_cognitive_tier
print('classifier import OK')
"

# Restart Hermes and check logs
systemctl --user restart hermes-gateway
sleep 5
journalctl --user -u hermes-gateway --since '10 sec ago' --no-pager | tail -5
```

Then send a test message via Telegram and check the proxy logs for "Cognitive tier hint: ..." 

**Commit:**
```bash
cd /home/rohit/.hermes/hermes-agent
git add run_agent.py
git commit -m "feat: wire cognitive classifier into Hermes conversation loop

Before each model call through the local proxy, classifies the last
user message and passes cognitive_tier in the request body. Only
active for proxy-routed requests (localhost:8080), not direct fallback."
git push
```

---

### Task 1.5: Deploy and verify Phase 1 end-to-end [BOTH]

**Goal:** Restart the proxy and Hermes, verify tier routing works via Telegram.

**Steps:**

1. Restart the proxy:
```bash
# Kill existing proxy
kill $(ss -tlnp | grep 8080 | grep -oP 'pid=\K\d+') 2>/dev/null
sleep 1
cd /home/rohit/agentharness && set -a && source data/.env && set +a
./venv/bin/python3 -m core.providers.proxy_server --host 0.0.0.0 --port 8080 --data-dir data & disown %1
```

2. Restart Hermes:
```bash
systemctl --user restart hermes-gateway
```

3. Test via Telegram:
   - Send "hello" -> check proxy log shows `Cognitive tier hint: CHAT`
   - Send "check disk space" -> check log shows `EXECUTE`
   - Send "why is pihole slow" -> check log shows `REASON`
   - Send "fix n8n it keeps crashing" -> check log shows `PLAN_NEEDED`

4. Check proxy logs:
```bash
journalctl --user -u hermes-gateway --since '5 min ago' --no-pager | grep -i "tier\|classif"
# Also check proxy process output
```

**No commit needed.** This is a verification task. If something fails, debug and fix in the relevant file, then commit the fix.

---

## PHASE 2: Plan-Execute Loop

### Task 2.1: Create the orchestrator state directory [HERMES]

**Goal:** Create the directory structure and plan state persistence module.

**File to create:** `/home/rohit/.hermes/hermes-agent/agent/orchestrator_state.py`

**Also run:**
```bash
mkdir -p /home/rohit/.hermes/orchestrator
```

**Code:**

```python
"""Orchestrator plan state persistence.

Persists active plans to disk so they survive Hermes restarts.
On restart, incomplete plans are reported to the user.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

ORCH_DIR = Path(os.path.expanduser("~/.hermes/orchestrator"))
ACTIVE_PLAN_FILE = ORCH_DIR / "active_plan.json"
PLAN_HISTORY_DIR = ORCH_DIR / "history"


def ensure_dirs():
    """Create orchestrator directories if they don't exist."""
    ORCH_DIR.mkdir(parents=True, exist_ok=True)
    PLAN_HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def save_plan(plan: dict) -> None:
    """Save the active plan to disk."""
    ensure_dirs()
    plan["updated_at"] = time.time()
    ACTIVE_PLAN_FILE.write_text(json.dumps(plan, indent=2))
    log.info("Plan saved: %s (%d steps)", plan.get("goal", "?"), len(plan.get("steps", [])))


def load_plan() -> Optional[dict]:
    """Load the active plan from disk, or None if no plan exists."""
    if not ACTIVE_PLAN_FILE.exists():
        return None
    try:
        return json.loads(ACTIVE_PLAN_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to load active plan: %s", exc)
        return None


def update_step(step_id: int, output: str, status: str = "completed") -> None:
    """Update a specific step's output and status in the active plan."""
    plan = load_plan()
    if not plan:
        return
    for step in plan.get("steps", []):
        if step.get("id") == step_id:
            step["output"] = output
            step["status"] = status
            step["completed_at"] = time.time()
            break
    save_plan(plan)


def clear_plan() -> Optional[dict]:
    """Archive the active plan and clear it. Returns the archived plan."""
    plan = load_plan()
    if not plan:
        return None
    # Archive to history
    ensure_dirs()
    ts = int(time.time())
    archive_file = PLAN_HISTORY_DIR / f"plan_{ts}.json"
    archive_file.write_text(json.dumps(plan, indent=2))
    # Remove active plan
    ACTIVE_PLAN_FILE.unlink(missing_ok=True)
    log.info("Plan archived to %s", archive_file)
    return plan


def get_interrupted_plan() -> Optional[dict]:
    """Check for an incomplete plan (from a prior crash/restart).

    Returns the plan if it has steps without 'completed_at', else None.
    """
    plan = load_plan()
    if not plan:
        return None
    incomplete = [s for s in plan.get("steps", []) if "completed_at" not in s]
    if incomplete:
        return plan
    return None
```

**Verify:**
```bash
cd /home/rohit/.hermes/hermes-agent
./venv/bin/python3 -c "
from agent.orchestrator_state import save_plan, load_plan, clear_plan, get_interrupted_plan

# Test save/load cycle
test_plan = {'goal': 'test', 'steps': [{'id': 1, 'action': 'test step'}]}
save_plan(test_plan)
loaded = load_plan()
assert loaded['goal'] == 'test'
print('save/load OK')

# Test archive
archived = clear_plan()
assert archived is not None
assert load_plan() is None
print('archive OK')

print('All state tests passed')
"
```

**Commit:**
```bash
cd /home/rohit/.hermes/hermes-agent
git add agent/orchestrator_state.py
git commit -m "feat: add orchestrator plan state persistence

Saves active plans to ~/.hermes/orchestrator/active_plan.json.
Supports save/load/update/archive/interrupt-detection. Plans
survive Hermes restarts."
git push
```

---

### Task 2.2: Create the plan-execute orchestrator [HERMES]

**Goal:** Build the core orchestrator that creates plans via reasoning model and executes steps via cheap models.

**File to create:** `/home/rohit/.hermes/hermes-agent/agent/orchestrator.py`

**Code:**

```python
"""Plan-Execute Orchestrator.

When the cognitive classifier returns PLAN_NEEDED, this module:
1. Sends the request to a reasoning model to create a JSON plan
2. Executes each step using cheap/free models
3. Feeds results back to the reasoning model for evaluation (selectively)
4. Reports completion or failure

The proxy stays dumb. This module handles all orchestration logic.
"""

import json
import logging
import re
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

# Safety limits
MAX_STEPS = 10
MAX_FEEDBACK_ITERATIONS = 3
STEP_TIMEOUT_S = 60
FEEDBACK_TIMEOUT_S = 30
TOTAL_TIMEOUT_S = 900  # 15 min (allows 10 steps + reasoning overhead)

# Destructive command patterns that require Telegram confirmation
_DESTRUCTIVE_PATTERNS = [
    r"rm\s+.*(-[rRf]+|--force|--recursive)",
    r"docker\s+(stop|rm|kill|system\s+prune|compose\s+down)",
    r"systemctl\s+(stop|disable)",
    r"kill\s+(-9|-KILL)",
    r"(DROP|TRUNCATE)\s+",
    r"sed\s+-i",
    r"mv\s+.*/etc/",
    r"chmod\s+",
    r"chown\s+",
]

_PLAN_SCHEMA_PROMPT = """You are a planning assistant. Given a user's request, create a
structured JSON plan to accomplish it. The plan will be executed step-by-step by a
separate execution model.

Respond with ONLY valid JSON matching this schema:
{
  "goal": "one-line description of what we're trying to accomplish",
  "estimated_duration_s": 120,
  "steps": [
    {
      "id": 1,
      "action": "human-readable description of this step",
      "tool": "terminal",
      "command": "the exact command to run",
      "requires_reasoning": false
    }
  ],
  "success_criteria": "how to verify the goal was achieved"
}

Rules:
- Maximum 10 steps
- Each step needs an "id" (sequential integer starting at 1)
- Steps that run commands: set "tool": "terminal" and "command": "..."
- Steps that need analysis of prior results: set "requires_reasoning": true (no command)
- Steps that depend on a prior step's analysis: set "depends_on": <step_id>
- Be specific with commands. Use absolute paths. Include flags.
- For investigation tasks, start with read-only diagnostic commands before any mutations.

User request: {request}
"""

_FEEDBACK_PROMPT = """You are evaluating the progress of a plan execution.

Goal: {goal}
Current step: #{step_id} - {step_action}
Step output:
```
{step_output}
```

Previous step outputs (last 2):
{prev_outputs}

Respond with ONLY valid JSON:
- To continue: {{"action": "CONTINUE"}}
- To modify remaining steps: {{"action": "MODIFY", "replace_from_step": N, "new_steps": [...]}}
- To abort: {{"action": "ABORT", "reason": "why"}}
- To declare success early: {{"action": "DONE", "summary": "what was accomplished"}}
"""


def is_destructive(command: str) -> bool:
    """Check if a command matches destructive patterns."""
    for pattern in _DESTRUCTIVE_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


def validate_plan(data: Any) -> Optional[dict]:
    """Validate a plan dict has the required structure.

    Returns the plan if valid, None otherwise.
    """
    if not isinstance(data, dict):
        return None
    if "goal" not in data or "steps" not in data:
        return None
    if not isinstance(data["steps"], list) or len(data["steps"]) == 0:
        return None
    if len(data["steps"]) > MAX_STEPS:
        data["steps"] = data["steps"][:MAX_STEPS]
        log.warning("Plan truncated to %d steps", MAX_STEPS)

    for step in data["steps"]:
        if "id" not in step or "action" not in step:
            return None

    return data


def build_plan_prompt(user_request: str) -> str:
    """Build the prompt to send to the reasoning model for plan creation."""
    return _PLAN_SCHEMA_PROMPT.format(request=user_request)


def build_feedback_prompt(
    goal: str,
    step_id: int,
    step_action: str,
    step_output: str,
    prev_outputs: list[tuple[int, str]],
) -> str:
    """Build the prompt for feedback evaluation after a step."""
    prev_text = ""
    for sid, out in prev_outputs[-2:]:  # Only last 2
        prev_text += f"Step #{sid}: {out[:500]}\n"
    if not prev_text:
        prev_text = "(no previous steps)"

    return _FEEDBACK_PROMPT.format(
        goal=goal,
        step_id=step_id,
        step_action=step_action,
        step_output=step_output[:1000],  # Cap output size
        prev_outputs=prev_text,
    )


def parse_plan_response(text: str) -> Optional[dict]:
    """Parse a JSON plan from the reasoning model's response.

    Handles models that wrap JSON in markdown code blocks.
    """
    # Try direct parse
    text = text.strip()
    try:
        return validate_plan(json.loads(text))
    except json.JSONDecodeError:
        pass

    # Try extracting from code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return validate_plan(json.loads(match.group(1).strip()))
        except json.JSONDecodeError:
            pass

    log.warning("Failed to parse plan from response: %s", text[:200])
    return None


def parse_feedback_response(text: str) -> dict:
    """Parse a feedback response from the reasoning model.

    Returns {"action": "CONTINUE"} on parse failure (safest default).
    """
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "action" in data:
            return data
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1).strip())
            if isinstance(data, dict) and "action" in data:
                return data
        except json.JSONDecodeError:
            pass

    log.warning("Failed to parse feedback, defaulting to CONTINUE: %s", text[:200])
    return {"action": "CONTINUE"}
```

**Verify:**
```bash
cd /home/rohit/.hermes/hermes-agent
./venv/bin/python3 -c "
from agent.orchestrator import (
    is_destructive, validate_plan, parse_plan_response,
    build_plan_prompt, parse_feedback_response
)

# Test destructive detection
assert is_destructive('rm -rf /tmp/stuff')
assert is_destructive('docker stop n8n')
assert is_destructive('docker system prune')
assert not is_destructive('docker ps')
assert not is_destructive('docker logs n8n')
assert not is_destructive('cat /etc/hosts')
print('Destructive detection OK')

# Test plan validation
good_plan = {'goal': 'test', 'steps': [{'id': 1, 'action': 'do thing'}]}
assert validate_plan(good_plan) is not None
assert validate_plan({'bad': True}) is None
assert validate_plan({'goal': 'x', 'steps': []}) is None
print('Plan validation OK')

# Test JSON parsing from code blocks
raw = '''Here is the plan:
\`\`\`json
{\"goal\": \"test\", \"steps\": [{\"id\": 1, \"action\": \"step 1\"}]}
\`\`\`'''
assert parse_plan_response(raw) is not None
print('Plan parsing OK')

# Test feedback parsing
assert parse_feedback_response('{\"action\": \"CONTINUE\"}') == {'action': 'CONTINUE'}
assert parse_feedback_response('garbage')['action'] == 'CONTINUE'
print('Feedback parsing OK')

print('All orchestrator tests passed')
"
```

**Commit:**
```bash
cd /home/rohit/.hermes/hermes-agent
git add agent/orchestrator.py
git commit -m "feat: add plan-execute orchestrator core module

Provides plan creation prompts, JSON plan parsing, feedback loop
prompts, destructive command detection, and plan validation.
Does not contain the execution loop itself (that lives in run_agent.py)."
git push
```

---

### Task 2.3: Wire the orchestrator into Hermes conversation loop [HERMES]

**Goal:** When the classifier returns PLAN_NEEDED, intercept the normal conversation flow and run the plan-execute loop instead.

**File to modify:** `/home/rohit/.hermes/hermes-agent/run_agent.py`

**This is the most complex task.** The approach: add a method to the AIAgent class that handles PLAN_NEEDED by creating a plan, executing steps, and reporting results. This method is called when the classifier returns PLAN_NEEDED, BEFORE the normal model call.

**What to change:**

**Step A:** Add imports near the top of run_agent.py:
```python
from agent.orchestrator import (
    build_plan_prompt, build_feedback_prompt,
    parse_plan_response, parse_feedback_response,
    is_destructive, MAX_STEPS, TOTAL_TIMEOUT_S,
)
from agent.orchestrator_state import (
    save_plan, load_plan, update_step, clear_plan,
    get_interrupted_plan, ensure_dirs,
)
```

**Step B:** Add this method to the AIAgent class (find a good location, perhaps near the tool execution methods):

```python
    async def _run_orchestrated_plan(self, user_request: str) -> str:
        """Execute a multi-step plan using the planner-executor pattern.

        1. Calls reasoning model to create a JSON plan
        2. Executes each step with cheap models
        3. Feeds results back to reasoning model selectively
        4. Returns a summary string for the user

        Falls back to normal conversation on any critical failure.
        """
        ensure_dirs()
        log.info("Orchestrator: creating plan for: %s", user_request[:100])

        # Step 1: Create plan via reasoning model
        plan_prompt = build_plan_prompt(user_request)
        try:
            # Use the existing model call infrastructure but force PLAN_NEEDED tier
            plan_response = await self._single_model_call(
                plan_prompt,
                cognitive_tier="PLAN_NEEDED",
                max_tokens=2048,
            )
        except Exception as exc:
            log.error("Orchestrator: plan creation failed: %s", exc)
            return None  # Fall back to normal conversation

        plan = parse_plan_response(plan_response)
        if not plan:
            log.warning("Orchestrator: invalid plan response, falling back")
            return None

        # Initialize plan state
        for step in plan["steps"]:
            step["status"] = "pending"
        save_plan(plan)

        self._emit_status(
            f"Plan created: {plan['goal']} ({len(plan['steps'])} steps)"
        )

        # Step 2: Execute steps
        start_time = time.time()
        step_outputs = []  # [(step_id, output)]

        for step in plan["steps"]:
            # Timeout check
            if time.time() - start_time > TOTAL_TIMEOUT_S:
                log.warning("Orchestrator: total timeout reached")
                clear_plan()
                return f"Plan timed out after {TOTAL_TIMEOUT_S}s. Completed {len(step_outputs)}/{len(plan['steps'])} steps."

            step_id = step["id"]
            action = step["action"]
            self._emit_status(f"Step {step_id}/{len(plan['steps'])}: {action}")

            if step.get("requires_reasoning"):
                # This step needs the reasoning model to analyze prior results
                fb_prompt = build_feedback_prompt(
                    goal=plan["goal"],
                    step_id=step_id,
                    step_action=action,
                    step_output="(analysis requested)",
                    prev_outputs=step_outputs,
                )
                try:
                    analysis = await self._single_model_call(
                        fb_prompt,
                        cognitive_tier="REASON",
                        max_tokens=1024,
                    )
                    step_outputs.append((step_id, analysis))
                    update_step(step_id, analysis, "completed")
                except Exception as exc:
                    log.error("Orchestrator: reasoning step %d failed: %s", step_id, exc)
                    update_step(step_id, str(exc), "failed")
                    step_outputs.append((step_id, f"ERROR: {exc}"))
                continue

            command = step.get("command")
            if not command:
                update_step(step_id, "skipped (no command)", "skipped")
                step_outputs.append((step_id, "skipped"))
                continue

            # Check for destructive commands
            if is_destructive(command):
                self._emit_status(
                    f"Step {step_id} requires confirmation: `{command}`"
                )
                # For now, skip destructive commands and note it
                update_step(step_id, "SKIPPED (destructive, needs confirmation)", "blocked")
                step_outputs.append((step_id, "BLOCKED: destructive command, skipped"))
                continue

            # Execute the command via terminal tool
            try:
                from model_tools import handle_function_call
                result = handle_function_call(
                    "terminal",
                    {"command": command, "timeout": 60},
                )
                step_outputs.append((step_id, result))
                update_step(step_id, result, "completed")
            except Exception as exc:
                error_msg = f"ERROR: {exc}"
                step_outputs.append((step_id, error_msg))
                update_step(step_id, error_msg, "failed")

            # Selective feedback: only on errors or final step
            is_error = "ERROR" in str(step_outputs[-1][1])
            is_final = step_id == plan["steps"][-1]["id"]

            if is_error or is_final:
                fb_prompt = build_feedback_prompt(
                    goal=plan["goal"],
                    step_id=step_id,
                    step_action=action,
                    step_output=str(step_outputs[-1][1]),
                    prev_outputs=step_outputs[:-1],
                )
                try:
                    fb_text = await self._single_model_call(
                        fb_prompt,
                        cognitive_tier="REASON",
                        max_tokens=512,
                    )
                    feedback = parse_feedback_response(fb_text)

                    if feedback["action"] == "ABORT":
                        reason = feedback.get("reason", "unknown")
                        clear_plan()
                        return f"Plan aborted at step {step_id}: {reason}"

                    if feedback["action"] == "DONE":
                        summary = feedback.get("summary", plan["goal"])
                        clear_plan()
                        return f"Plan completed: {summary}"

                    if feedback["action"] == "MODIFY":
                        new_steps = feedback.get("new_steps", [])
                        if new_steps:
                            # Replace remaining steps
                            replace_from = feedback.get("replace_from_step", step_id + 1)
                            plan["steps"] = [
                                s for s in plan["steps"] if s["id"] < replace_from
                            ] + new_steps
                            save_plan(plan)
                            log.info("Plan modified: %d steps remaining", len(new_steps))

                except Exception as exc:
                    log.warning("Orchestrator: feedback call failed: %s", exc)
                    # Continue anyway

        # All steps done
        archived = clear_plan()
        summary_parts = [f"Plan completed: {plan['goal']}"]
        for sid, out in step_outputs:
            summary_parts.append(f"  Step {sid}: {str(out)[:200]}")
        return "\n".join(summary_parts)
```

**Step C:** You also need a helper method `_single_model_call` that makes a single model call with a cognitive tier. Add this to the AIAgent class:

```python
    async def _single_model_call(
        self, prompt: str, cognitive_tier: str = "EXECUTE", max_tokens: int = 1024,
    ) -> str:
        """Make a single model call through the proxy with a cognitive tier hint.

        Returns the model's text response.
        """
        import httpx

        messages = [{"role": "user", "content": prompt}]
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "cognitive_tier": cognitive_tier,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )

        if resp.status_code != 200:
            raise RuntimeError(f"Model call failed: HTTP {resp.status_code}")

        data = resp.json()
        return data["choices"][0]["message"]["content"]
```

**Step D:** In the main conversation loop (where the cognitive tier was added in Task 1.4), add the orchestrator intercept. Find where `_tier = classify_cognitive_tier(_last_user_msg)` was added and add AFTER it:

```python
                if _tier == "PLAN_NEEDED":
                    # Run orchestrated plan instead of normal model call
                    plan_result = await self._run_orchestrated_plan(_last_user_msg)
                    if plan_result is not None:
                        # Plan succeeded, inject result as assistant message
                        # (exact injection mechanism depends on how run_agent.py
                        # manages the conversation loop -- adapt to fit)
                        pass  # TODO: inject plan_result into conversation
```

**Note to implementer:** The exact integration point depends on the structure of `run_agent.py`'s conversation loop. The key principle: when PLAN_NEEDED is detected, call `_run_orchestrated_plan` INSTEAD of the normal model call. If it returns None (failure), fall back to the normal model call. If it returns a string (success), use that as the assistant's response.

**This task requires reading the conversation loop in run_agent.py carefully to find the right insertion point.** The file is ~10K lines. Search for the section where `api_kwargs` is sent to the API and the response is processed.

**Verify:**
```bash
# Check it imports cleanly
cd /home/rohit/.hermes/hermes-agent
./venv/bin/python3 -c "
import run_agent
print('run_agent with orchestrator imports OK')
"

# Restart and test
systemctl --user restart hermes-gateway
# Send via Telegram: "fix n8n it keeps crashing"
# Check logs for "Orchestrator: creating plan" and "Plan created"
```

**Commit:**
```bash
cd /home/rohit/.hermes/hermes-agent
git add run_agent.py
git commit -m "feat: wire plan-execute orchestrator into Hermes conversation loop

When classifier returns PLAN_NEEDED, intercepts normal model call and
runs orchestrated plan: reasoning model creates plan, cheap models
execute steps, selective feedback evaluates progress. Falls back to
normal conversation on any orchestrator failure."
git push
```

---

### Task 2.4: Test the full plan-execute demo [BOTH]

**Goal:** Verify the "fix n8n" demo works end-to-end.

**Steps:**

1. Ensure proxy and Hermes are running with all prior changes
2. Via Telegram, send: "fix n8n, it's in a boot loop"
3. Observe:
   - Classifier should return PLAN_NEEDED
   - Reasoning model should create a multi-step plan
   - Each step should execute via terminal tool
   - Summary should be reported back via Telegram

4. If n8n isn't actually broken, test with:
   - "check the health of all Docker containers and report any issues"
   - "investigate disk usage and find the largest directories"

5. Check plan state persistence:
```bash
cat ~/.hermes/orchestrator/active_plan.json 2>/dev/null || echo "No active plan"
ls ~/.hermes/orchestrator/history/
```

**No commit needed.** This is a verification task. Fix any issues found in the relevant files.

---

## PHASE 3: Predictive Orchestration

**PREREQUISITE CHECK before starting Phase 3:**
```bash
# Must have working backups
ls /home/rohit/backups/ 2>/dev/null || echo "WARNING: No backups directory found. Set up backups before Phase 3."
```

### Task 3.1: Create the predictive monitor [HERMES]

**Goal:** Background thread that watches HomeButler events and creates synthetic orchestrator triggers.

**File to create:** `/home/rohit/.hermes/hermes-agent/agent/predictive_monitor.py`

This task creates the module. The actual wiring into Hermes happens in Task 3.2.

**Code:**

```python
"""Predictive infrastructure monitor.

Watches HomeButler's Docker event log for anomaly patterns and creates
synthetic orchestrator triggers when issues are detected.

Runs as a background thread inside Hermes.
"""

import json
import logging
import os
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

HOMEBUTLER_LOG = Path("/home/rohit/shared_agent_memory/homebutler.log")
PENDING_TRIGGERS_FILE = Path(os.path.expanduser(
    "~/.hermes/orchestrator/pending_triggers.json"
))

# Anomaly detection thresholds
CRASH_THRESHOLD = 3          # crashes within the window
CRASH_WINDOW_S = 600         # 10 minutes
RESTART_THRESHOLD = 5        # restarts within window
RESTART_WINDOW_S = 1800      # 30 minutes

# How often to scan the log (seconds)
SCAN_INTERVAL_S = 60

# Cooldown: don't re-trigger for the same container within this window
TRIGGER_COOLDOWN_S = 3600    # 1 hour


class PredictiveMonitor:
    """Watches HomeButler events and triggers orchestrator plans."""

    def __init__(self, trigger_callback: Callable[[str], None]):
        """
        Args:
            trigger_callback: Called with a synthetic user request string
                when an anomaly is detected. The orchestrator should
                handle this like a PLAN_NEEDED request.
        """
        self._callback = trigger_callback
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_position = 0  # Track file read position
        self._recent_events: dict[str, list[float]] = defaultdict(list)
        self._last_triggered: dict[str, float] = {}

    def start(self):
        """Start the background monitoring thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="predictive-monitor"
        )
        self._thread.start()
        log.info("Predictive monitor started")

    def stop(self):
        """Stop the monitoring thread."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Predictive monitor stopped")

    def _run(self):
        """Main monitoring loop."""
        while not self._stop.is_set():
            try:
                self._scan_events()
            except Exception as exc:
                log.error("Predictive monitor scan error: %s", exc)
            self._stop.wait(SCAN_INTERVAL_S)

    def _scan_events(self):
        """Read new lines from the HomeButler log and detect patterns."""
        if not HOMEBUTLER_LOG.exists():
            return

        try:
            with open(HOMEBUTLER_LOG) as f:
                f.seek(self._last_position)
                new_lines = f.readlines()
                self._last_position = f.tell()
        except OSError:
            return

        now = time.time()

        for line in new_lines:
            line = line.strip()
            if not line:
                continue

            # Parse HomeButler log format: [timestamp] message
            # Look for container events
            lower = line.lower()

            container_name = None
            event_type = None

            if "status: die" in lower or "status: oom" in lower:
                event_type = "crash"
                # Extract container name from: Container 'xxx' status: die
                for part in line.split("'"):
                    if part and part[0].isalpha() and "container" not in part.lower():
                        container_name = part
                        break
            elif "restarted" in lower or "status: start" in lower:
                event_type = "restart"
                for part in line.split("'"):
                    if part and part[0].isalpha() and "container" not in part.lower():
                        container_name = part
                        break

            if not container_name or not event_type:
                continue

            # Track event
            self._recent_events[f"{container_name}:{event_type}"].append(now)

            # Clean old events
            for key in list(self._recent_events):
                self._recent_events[key] = [
                    t for t in self._recent_events[key] if now - t < max(CRASH_WINDOW_S, RESTART_WINDOW_S)
                ]

            # Check thresholds
            crash_key = f"{container_name}:crash"
            restart_key = f"{container_name}:restart"

            crashes = [t for t in self._recent_events.get(crash_key, []) if now - t < CRASH_WINDOW_S]
            restarts = [t for t in self._recent_events.get(restart_key, []) if now - t < RESTART_WINDOW_S]

            trigger_msg = None
            if len(crashes) >= CRASH_THRESHOLD:
                trigger_msg = (
                    f"[PREDICTIVE] Container '{container_name}' has crashed "
                    f"{len(crashes)} times in the last {CRASH_WINDOW_S // 60} minutes. "
                    f"Investigate the root cause and fix if possible."
                )
            elif len(restarts) >= RESTART_THRESHOLD:
                trigger_msg = (
                    f"[PREDICTIVE] Container '{container_name}' has restarted "
                    f"{len(restarts)} times in the last {RESTART_WINDOW_S // 60} minutes. "
                    f"This may indicate a boot loop. Investigate."
                )

            if trigger_msg:
                # Cooldown check
                if now - self._last_triggered.get(container_name, 0) < TRIGGER_COOLDOWN_S:
                    log.info("Predictive: %s in cooldown, skipping", container_name)
                    continue

                self._last_triggered[container_name] = now
                log.info("Predictive trigger: %s", trigger_msg)

                # Check if we can reach the proxy (network awareness)
                if self._is_network_available():
                    self._callback(trigger_msg)
                else:
                    self._queue_trigger(trigger_msg)

    def _is_network_available(self) -> bool:
        """Check if the proxy is reachable (and thus cloud APIs work)."""
        try:
            import httpx
            resp = httpx.get("http://localhost:8080/health", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    def _queue_trigger(self, trigger_msg: str):
        """Queue a trigger for later processing (network unavailable)."""
        try:
            pending = []
            if PENDING_TRIGGERS_FILE.exists():
                pending = json.loads(PENDING_TRIGGERS_FILE.read_text())
            pending.append({
                "message": trigger_msg,
                "queued_at": time.time(),
            })
            PENDING_TRIGGERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            PENDING_TRIGGERS_FILE.write_text(json.dumps(pending, indent=2))
            log.info("Predictive trigger queued (network unavailable)")
        except Exception as exc:
            log.error("Failed to queue trigger: %s", exc)

    def process_pending_triggers(self):
        """Process any queued triggers (call after network is restored)."""
        if not PENDING_TRIGGERS_FILE.exists():
            return
        try:
            pending = json.loads(PENDING_TRIGGERS_FILE.read_text())
            PENDING_TRIGGERS_FILE.unlink()
            for item in pending:
                age_min = (time.time() - item["queued_at"]) / 60
                log.info("Processing queued trigger (%.0f min old): %s",
                         age_min, item["message"][:80])
                self._callback(item["message"])
        except Exception as exc:
            log.error("Failed to process pending triggers: %s", exc)
```

**Verify:**
```bash
cd /home/rohit/.hermes/hermes-agent
./venv/bin/python3 -c "
from agent.predictive_monitor import PredictiveMonitor

triggered = []
monitor = PredictiveMonitor(trigger_callback=lambda msg: triggered.append(msg))
print('PredictiveMonitor instantiated OK')
print(f'Crash threshold: {monitor._stop is not None}')  # just verify it works
print('Import and instantiation OK')
"
```

**Commit:**
```bash
cd /home/rohit/.hermes/hermes-agent
git add agent/predictive_monitor.py
git commit -m "feat: add predictive infrastructure monitor

Background thread watches HomeButler Docker event log for crash/restart
patterns. Triggers orchestrator plans when thresholds are exceeded.
Network-aware: queues triggers during outages for later processing."
git push
```

---

### Task 3.2: Wire predictive monitor into Hermes [HERMES]

**Goal:** Start the predictive monitor when Hermes starts, connect it to the orchestrator.

**File to modify:** `/home/rohit/.hermes/hermes-agent/run_agent.py`

**What to change:**

Find where the AIAgent class initializes (the `__init__` method). Add near the end:

```python
        # Start predictive monitor
        from agent.predictive_monitor import PredictiveMonitor
        self._predictive_monitor = PredictiveMonitor(
            trigger_callback=self._handle_predictive_trigger
        )
        self._predictive_monitor.start()
```

Add the callback method to the AIAgent class:

```python
    def _handle_predictive_trigger(self, trigger_message: str):
        """Handle a predictive trigger from the infrastructure monitor.

        Creates a synthetic message and runs it through the orchestrator.
        Destructive actions are always gated behind Telegram confirmation.
        """
        log.info("Handling predictive trigger: %s", trigger_message[:100])
        self._emit_status(f"Predictive: {trigger_message[:80]}")

        # Queue for processing in the main event loop
        # The exact mechanism depends on how Hermes handles injected messages.
        # Use the plugin inject_message API if available, or queue to a list
        # that the main loop checks.
        try:
            # This uses Hermes's message injection capability
            # (see hermes_cli/plugins.py inject_message)
            if hasattr(self, '_inject_predictive'):
                self._inject_predictive(trigger_message)
            else:
                log.warning("Predictive: no injection mechanism available, logging only")
        except Exception as exc:
            log.error("Predictive trigger handling failed: %s", exc)
```

**Note:** The exact injection mechanism depends on how Hermes's conversation loop accepts external messages. The implementer should check if `inject_message` from the plugin system works, or if there's a simpler queue mechanism. The key is: the trigger message should enter the conversation loop as if the user sent it, which will cause the classifier to return PLAN_NEEDED and invoke the orchestrator.

**Verify:**
```bash
systemctl --user restart hermes-gateway
sleep 5
journalctl --user -u hermes-gateway --since '10 sec ago' --no-pager | grep -i "predict"
# Should see "Predictive monitor started"
```

**Commit:**
```bash
cd /home/rohit/.hermes/hermes-agent
git add run_agent.py
git commit -m "feat: wire predictive monitor into Hermes startup

Starts PredictiveMonitor background thread on AIAgent init.
Connects trigger callback to orchestrator via message injection."
git push
```

---

## Summary of All Tasks

| Task | Phase | Repo | Description | Depends On |
|------|-------|------|-------------|------------|
| 1.1 | 1 | Hermes | Create cognitive_router.py | - |
| 1.2 | 1 | Hermes | Create 50-message accuracy test | 1.1 |
| 1.3 | 1 | AgentHarness | Add tier routing to proxy | 1.2 (GATE) |
| 1.4 | 1 | Hermes | Wire classifier into Hermes | 1.1, 1.3 |
| 1.5 | 1 | Both | Deploy and verify Phase 1 | 1.4 |
| 2.1 | 2 | Hermes | Create orchestrator state module | 1.5 |
| 2.2 | 2 | Hermes | Create orchestrator core module | 2.1 |
| 2.3 | 2 | Hermes | Wire orchestrator into Hermes | 2.2 |
| 2.4 | 2 | Both | Test full plan-execute demo | 2.3 |
| 3.1 | 3 | Hermes | Create predictive monitor | 2.4 + backups |
| 3.2 | 3 | Hermes | Wire monitor into Hermes | 3.1 |
