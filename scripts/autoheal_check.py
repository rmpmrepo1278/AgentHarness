#!/usr/bin/env python3
"""autoheal_check.py - Restart unhealthy containers that opt in via autoheal=true.

Fast, deterministic companion to autonomous_fixer (which delegates slow Claude
sessions). Honors the autoheal=true label already set on services. Uses a
2-consecutive-check strike threshold and a 15-minute cooldown to avoid restart
thrash. Logs to ~/.hermes/logs/autoheal.log.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

AG_HOME = os.environ.get("AG_HOME", "/home/rohit/agentharness")
HERMES_HOME = os.environ.get("HERMES_HOME", "/home/rohit/.hermes")
LOG_FILE = os.path.join(HERMES_HOME, "logs", "autoheal.log")
STATE_FILE = os.path.join(HERMES_HOME, "data", "autoheal_state.json")
COOLDOWN_SECONDS = 900
REQUIRED_STRIKES = 2


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a") as fh:
        fh.write(line + "\n")


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, STATE_FILE)


def run(cmd: list) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def main() -> int:
    now = time.time()
    state = load_state()
    restart_policy = os.environ.get("AUTOHEAL_DRY_RUN")
    dry_run = restart_policy == "1"

    ps = run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Label \"autoheal\"}}\t{{.Status}}"])
    rows = [r for r in ps.stdout.splitlines() if r.strip()]
    healed = 0
    seen = set()

    for row in rows:
        parts = row.split("\t")
        if len(parts) < 3:
            continue
        name, label, status = parts[0], parts[1], parts[2]
        if label != "true":
            continue
        seen.add(name)
        entry = state.get(name, {"strikes": 0, "last_restart": 0})
        unhealthy = "unhealthy" in status.lower()
        if unhealthy:
            entry["strikes"] = entry.get("strikes", 0) + 1
            state[name] = entry
            if entry["strikes"] >= REQUIRED_STRIKES:
                since_last = now - entry.get("last_restart", 0)
                if since_last >= COOLDOWN_SECONDS:
                    if dry_run:
                        log(f"DRY-RUN: would restart unhealthy {name} ({status})")
                    else:
                        res = run(["docker", "restart", name])
                        ok = res.returncode == 0
                        log(f"restart {name}: {'ok' if ok else 'FAILED ' + res.stderr.strip()}")
                        if ok:
                            healed += 1
                            entry["last_restart"] = now
                            entry["strikes"] = 0
                else:
                    log(f"skip {name}: unhealthy but cooldown {int(since_last)}s < {COOLDOWN_SECONDS}s")
        else:
            if name in state and state[name].get("strikes", 0) > 0:
                state[name]["strikes"] = 0

    for name in list(state.keys()):
        if name not in seen and state[name].get("strikes", 0) > 0:
            del state[name]

    save_state(state)
    log(f"autoheal_check complete: {'restarted ' + str(healed) if healed else 'no action'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
