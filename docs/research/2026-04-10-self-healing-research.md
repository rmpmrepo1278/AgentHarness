# Self-Healing Homelab Research — 2026-04-10

## Key Findings That Affect Our Design

### 1. Layer 0 Safety Net (MISSING)
We need a dumb, no-LLM-dependency watchdog as a safety net. If AgentHarness itself crashes, nothing restarts it.
- **Monit** — 20+ year old process supervisor, tiny footprint, zero dependencies
- **docker-autoheal** — single-purpose container that restarts unhealthy Docker containers
- systemd `Restart=always` handles systemd services but not Docker containers

### 2. Cooldown Timers (MISSING)
Our runbooks can currently restart a service infinitely. We need:
- Max 3 restart attempts per service per 10 minutes
- After 3 failures, stop fixing and escalate
- Prevents restart loops that can make things worse

### 3. Daily Digest Notifications
Instead of alerting on every auto-fix, send one daily summary at 8am.
Only interrupt for things that couldn't be auto-fixed.

### 4. Docker Socket Security
Current setup mounts `/var/run/docker.sock` directly into MCP containers.
- Should use Tecnativa docker-socket-proxy to limit API access
- Block `EXEC=0` — prevents running arbitrary commands inside containers
- Block `VOLUMES=0` — prevents mounting host paths

### 5. Existing Tools to Consider
- **homebutler** — closest to what we're building, has MCP integration
- **Automatron** — lightweight YAML runbook engine, good pattern to study
- **Healthchecks.io** (self-hosted) — dead-man-switch for cron jobs and the agent itself

### 6. Runbook Format Alignment
Our YAML format is close to Automatron/StackStorm patterns. Good.
Should add: `cooldown` field, `max_attempts` field, runbook validation CLI.

### 7. Sudoers Hardening
Our current sudoers is good (specific commands only). Could add:
- `Defaults log_output` for audit trail
- Rate limiting: max 5 sudo calls per minute

## Sources
- homebutler: github.com/Higangssh/homebutler
- Automatron: github.com/madflojo/automatron
- docker-autoheal: github.com/willfarrell/docker-autoheal
- Tecnativa docker-socket-proxy: github.com/tecnativa/docker-socket-proxy
- PagerDuty auto-remediation: autoremediation.pagerduty.com
- Healthchecks.io: healthchecks.io
- Monit: mmonit.com/monit
