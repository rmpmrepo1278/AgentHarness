# Spec: Laguna M.1 Brain & Gmail Connectivity (2026-04-29)

## Overview
This update upgrades the core reasoning engine of the Hermes agent and integrates real-time email management via the Gmail API. It also addresses persistent instability caused by service conflicts.

## Architecture Upgrades

### 1. LLM Proxy Migration (Laguna M.1)
- **Primary Engine:** \`poolside/laguna-m.1:free\` (via OpenRouter).
- **Fallback:** Llama 3.3 70B (Groq) and local Gemma 4-26B.
- **Routing:** Unfied \`laguna-m1\` slot prioritized across all complexity tiers (low, medium, high, critical).
- **Identity:** Fixed header injection (\`X-Title\`, \`HTTP-Referer\`) to bypass OpenRouter provider routing errors.

### 2. Gmail Integration
- **Credentials:** Secure OAuth2 flow handled via \`token.json\` in \`~/.hermes/gmail/\`.
- **Tooling:** \`gmail_reader.py\` added for listing, reading, and summarizing emails.
- **Autonomy:** Registered as a primary superpower for the Hermes agent.

### 3. Stability & Self-Healing
- **Service Unification:** Migrated all Hermes services to system-wide systemd units (\`hermes-gateway.service\`, \`agentharness-llm-proxy.service\`).
- **Watchdog Overhaul:** Updated \`service_watchdog.sh\` to use POSIX-compliant systemctl checks, eliminating the user-level conflict loop.
- **Self-Heal Patching:** Updated \`self_heal.py\` to monitor system-level units, preventing "false alarm" restart loops.

## Verification Status
- **LLM Connectivity:** Verified via \`local-smart\` proxy endpoint.
- **Gmail API:** Verified listing of top 3 messages.
- **Watchdog:** Verified clean "All services healthy" report.
