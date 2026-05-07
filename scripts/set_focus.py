#!/usr/bin/env python3
"""
set_focus.py — Set or clear the domain focus override for a session.

Usage:
    set_focus.py <session_key> <domain>   # Set domain override (infra|career|knowledge|general)
    set_focus.py <session_key> --clear    # Clear override
    set_focus.py <session_key> --show     # Show current override

The focus state is stored in ~/.hermes/sessions/focus_<session_key>.txt
and injected into the agent's ephemeral system prompt on each turn.
"""

import sys
from pathlib import Path

SESSIONS_DIR = Path.home() / ".hermes" / "sessions"
VALID_DOMAINS = {"infra", "infrastructure", "career", "career-ops", "knowledge", "knowledge-base", "general"}

DOMAIN_ALIASES = {
    "infra": "infrastructure",
    "career": "career-ops",
    "knowledge": "knowledge-base",
}


def normalize(domain: str) -> str:
    domain = domain.lower().strip()
    domain = DOMAIN_ALIASES.get(domain, domain)
    return domain


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    session_key = sys.argv[1]
    action = sys.argv[2].lower().strip()

    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    focus_file = SESSIONS_DIR / f"focus_{session_key}.txt"

    if action in ("--clear", "clear", "none", "reset"):
        focus_file.unlink(missing_ok=True)
        print(f"✓ Cleared domain focus for session {session_key[:12]}...")
        return

    if action in ("--show", "show", "status"):
        if focus_file.exists():
            domain = focus_file.read_text().strip()
            print(f"Current focus: {domain}")
        else:
            print("No domain focus set (using topic-based routing)")
        return

    # Set domain
    domain = normalize(action)
    if domain not in VALID_DOMAINS and domain not in DOMAIN_ALIASES.values():
        print(f"✗ Unknown domain: {action}")
        print(f"  Valid: {', '.join(sorted(set(DOMAIN_ALIASES.values())))}")
        sys.exit(1)

    focus_file.write_text(domain)
    print(f"✓ Session {session_key[:12]}... → {domain} focus")


if __name__ == "__main__":
    main()
