# Instructions for Implementing the Planner-Executor Orchestrator

> **NOTE**: The hub-and-spoke agent architecture (domain routing via Telegram topics) is **COMPLETE** — see `ORCHESTRATOR-HUB-DESIGN.md`. This document covers the separate **planner-executor orchestrator** project (cognitive tier classification + plan-execute loop), which is **NOT YET STARTED**.

**READ THIS FIRST. Follow it exactly.**

## What you are building

A planner-executor orchestrator for Hermes that classifies user requests by cognitive
tier (CHAT/EXECUTE/REASON/PLAN_NEEDED) and routes them to the right model. When a
request needs a multi-step plan, a strong reasoning model creates the plan and cheap
models execute each step.

## Where the spec lives

- **Implementation spec:** `/home/rohit/.claude/ORCHESTRATOR-SPEC.md`
- **Design rationale:** `/home/rohit/.claude/ORCHESTRATOR-DESIGN.md`

## How to use the spec

The spec has 11 numbered tasks (1.1 through 3.2). Work on ONE task at a time.

### Starting a new session or resuming after interruption:

1. Check which tasks are already done:
   ```bash
   cd /home/rohit/.hermes/hermes-agent && git log --oneline -10
   cd /home/rohit/agentharness && git log --oneline -5
   ```
2. Look at the commit messages. Each task has a specific commit message format.
   Find the last completed task number.
3. Open `/home/rohit/.claude/ORCHESTRATOR-SPEC.md` and go to the NEXT task.
4. Do that ONE task. Follow every step exactly.
5. Run the verify commands at the end of the task.
6. Run the git commit commands at the end of the task.
7. Stop. You are done for this session.

### Rules:

- **ONE TASK PER SESSION.** Do not combine tasks. Do not skip ahead.
- **Read the spec literally.** The file paths, code, and commands are exact. Do not adapt or "improve" them.
- **If a task says [HERMES], work in:** `/home/rohit/.hermes/hermes-agent/`
- **If a task says [AGENTHARNESS], work in:** `/home/rohit/agentharness/`
- **If a task says [BOTH], work in both repos as specified.**
- **Always run the verify step.** If verification fails, fix the issue before committing.
- **Always commit and push.** Every task ends with a commit. This is how progress is tracked.
- **Task 1.2 is a GATE.** The accuracy test must pass (>=90%) before you proceed to Task 1.3. If it fails, tune the classifier prompt in `cognitive_router.py` and re-run.
- **Task 2.3 is the hardest task.** It requires reading `run_agent.py` (~10K lines) to find the right insertion point for the orchestrator. Take your time. Read the conversation loop carefully. The spec provides the code but you need to find WHERE to put it.
- **Do not refactor existing code.** Only add the code specified in the spec. Do not rename variables, reorganize imports, or clean up adjacent code.
- **If something is unclear,** re-read the spec section. The answer is there. If you genuinely cannot figure it out, leave a comment `# TODO(orchestrator): <question>` and move to the next task.

### Key file paths:

```
PROXY:         /home/rohit/agentharness/core/providers/proxy_server.py
HERMES AGENT:  /home/rohit/.hermes/hermes-agent/
RUN_AGENT:     /home/rohit/.hermes/hermes-agent/run_agent.py
LOCAL LLM:     http://localhost:8081
PROXY:         http://localhost:8080
ENV:           /home/rohit/agentharness/data/.env
ORCH DIR:      /home/rohit/.hermes/orchestrator/
PLUGINS:       /home/rohit/.hermes/plugins/
```

### After all tasks are complete:

Restart both services:
```bash
# Restart proxy
kill $(ss -tlnp | grep 8080 | grep -oP 'pid=\K\d+') 2>/dev/null; sleep 1
cd /home/rohit/agentharness && set -a && source data/.env && set +a
./venv/bin/python3 -m core.providers.proxy_server --host 0.0.0.0 --port 8080 --data-dir data & disown %1

# Restart Hermes
systemctl --user restart hermes-gateway
```

Then test via Telegram:
- "hello" -> should use CHAT tier
- "restart n8n" -> should use EXECUTE tier
- "fix n8n it keeps crashing" -> should create and execute a plan
