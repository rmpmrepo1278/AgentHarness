#!/usr/bin/env python3
"""Patch Chaguli's agent.py to add /doctor and /logs commands.

Run on the homelab:
    python3 ~/agentharness/patches/add_doctor_command.py
    python3 ~/agentharness/patches/add_doctor_command.py --dry-run

What it does:
1. Reads ~/openclaw/chaguli/agent.py
2. Finds the _handle_message() command routing section
3. Inserts /doctor and /logs command handlers
4. Ensures `import subprocess` is present
5. Creates a timestamped backup before modifying

/doctor calls AgentHarness's doctor_check.py for system health.
/doctor fix RUNBOOK runs a specific auto-fix runbook.
/logs SERVICE shows recent logs for a service.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

AGENT_PY = Path.home() / "openclaw" / "chaguli" / "agent.py"

# ---------------------------------------------------------------------------
# The code block to insert
# ---------------------------------------------------------------------------
DOCTOR_BLOCK = '''
        # --- /doctor and /logs commands (added by AgentHarness patcher) ---
        if text == "/doctor" or text.startswith("/doctor "):
            parts = text.split(maxsplit=2)
            if len(parts) >= 3 and parts[1] == "fix":
                cmd = ["python3", "/home/rohit/agentharness/scripts/doctor_check.py", "--fix", parts[2]]
            else:
                cmd = ["python3", "/home/rohit/agentharness/scripts/doctor_check.py"]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                output = result.stdout or result.stderr or "No output"
                send_message(output[:4000])
            except subprocess.TimeoutExpired:
                send_message("Doctor timed out after 60s")
            except Exception as e:
                send_message(f"Doctor error: {e}")
            return

        if text == "/logs" or text.startswith("/logs "):
            parts = text.split(maxsplit=1)
            service = parts[1].strip() if len(parts) > 1 else ""
            cmd = ["python3", "/home/rohit/agentharness/scripts/show_logs.py"]
            if service:
                cmd.append(service)
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                output = result.stdout or result.stderr or "No output"
                send_message(output[:4000])
            except subprocess.TimeoutExpired:
                send_message("Logs timed out after 15s")
            except Exception as e:
                send_message(f"Logs error: {e}")
            return
        # --- end /doctor and /logs ---
'''


def find_send_message_name(source: str) -> str:
    """Discover the actual name used for sending Telegram messages.

    Chaguli might use send_message, self.send_message, self._reply, etc.
    Look for patterns near existing command handlers.
    """
    # Common patterns in order of likelihood
    patterns = [
        r'self\.send_message\(',
        r'self\._reply\(',
        r'self\.reply\(',
        r'send_message\(',
        r'await self\.send_message\(',
        r'await send_message\(',
    ]
    for pat in patterns:
        if re.search(pat, source):
            match = re.search(pat, source)
            # Return the function call prefix (e.g. "self.send_message")
            return match.group(0).rstrip('(')
    return None


def find_insertion_point(source: str) -> tuple[int, str]:
    """Find where to insert the /doctor block.

    Strategy: look for existing slash-command handlers inside _handle_message.
    Insert before the last elif/else in the command chain, or after the last
    recognized command handler.

    Returns (line_number, context_line) or raises if not found.
    """
    lines = source.split('\n')

    # First, find _handle_message
    handle_msg_line = None
    for i, line in enumerate(lines):
        if re.search(r'def _handle_message\b', line):
            handle_msg_line = i
            break

    if handle_msg_line is None:
        # Try alternate names
        for i, line in enumerate(lines):
            if re.search(r'def (handle_message|_process_message|process_command)\b', line):
                handle_msg_line = i
                break

    if handle_msg_line is None:
        raise RuntimeError("Could not find _handle_message (or similar) function")

    print(f"  Found message handler at line {handle_msg_line + 1}")

    # Now scan forward from _handle_message looking for command patterns
    # We want to find the block of if/elif that handles slash commands
    command_pattern = re.compile(
        r'''(text\s*==\s*["\']/(status|usage|draft|help|ping|version|budget|config)'''
        r'''|text\.startswith\(\s*["\']/(status|usage|draft|help|ping|version|budget|config))''',
        re.IGNORECASE,
    )

    # Also match generic slash command patterns
    generic_cmd_pattern = re.compile(
        r'''(text\s*==\s*["\']/\w+|text\.startswith\(\s*["\']/\w+)'''
    )

    last_cmd_line = None
    # Scan from _handle_message to end of function (next def at same indent or less)
    base_indent = len(lines[handle_msg_line]) - len(lines[handle_msg_line].lstrip())
    for i in range(handle_msg_line + 1, len(lines)):
        line = lines[i]
        stripped = line.lstrip()
        # Stop if we hit another def at same or lesser indent
        if stripped.startswith('def ') and (len(line) - len(stripped)) <= base_indent:
            break
        if generic_cmd_pattern.search(line):
            last_cmd_line = i

    if last_cmd_line is None:
        raise RuntimeError(
            "Could not find any slash-command handlers inside the message handler. "
            "Expected patterns like: text == \"/status\" or text.startswith(\"/usage\")"
        )

    print(f"  Last slash-command handler found at line {last_cmd_line + 1}: "
          f"{lines[last_cmd_line].strip()[:80]}")

    # Find the end of this command's block (next blank line, or next if/elif at same indent)
    cmd_indent = len(lines[last_cmd_line]) - len(lines[last_cmd_line].lstrip())
    insert_after = last_cmd_line
    for i in range(last_cmd_line + 1, len(lines)):
        line = lines[i]
        stripped = line.lstrip()
        if not stripped:  # blank line
            insert_after = i
            break
        current_indent = len(line) - len(stripped)
        if current_indent <= cmd_indent and stripped and not stripped.startswith('#'):
            # We've exited the block — check if it's a return, elif, else, etc.
            if stripped.startswith('return'):
                insert_after = i
            else:
                insert_after = i - 1
            break
        insert_after = i

    return insert_after, lines[insert_after].strip()[:80]


def adapt_block(source: str, block: str) -> str:
    """Adapt the code block to match the actual codebase conventions.

    - Replace send_message() with the actual function name used
    - Detect if methods use self. prefix
    - Detect if the code is async
    """
    adapted = block

    # Detect actual send function
    send_fn = find_send_message_name(source)
    if send_fn and send_fn != 'send_message':
        adapted = adapted.replace('send_message(', f'{send_fn}(')
        print(f"  Adapted send function: send_message -> {send_fn}")

    # Check if _handle_message is async
    handle_match = re.search(r'async\s+def\s+_handle_message\b', source)
    if not handle_match:
        handle_match = re.search(r'async\s+def\s+(handle_message|_process_message)\b', source)
    if handle_match:
        # Add await before subprocess and send calls
        adapted = adapted.replace('result = subprocess.run(', 'result = await asyncio.to_thread(subprocess.run, ')  # noqa
        # Actually, simpler: subprocess.run is sync, keep it. But send might need await
        adapted = adapted.replace('result = await asyncio.to_thread(subprocess.run, ', 'result = subprocess.run(')
        if send_fn:
            adapted = adapted.replace(f'{send_fn}(', f'await {send_fn}(')
        else:
            adapted = adapted.replace('send_message(', 'await send_message(')
        print("  Detected async handler — added await to send calls")

    # Detect the variable name for the text (might be `text`, `msg`, `message`, etc.)
    text_var_match = re.search(
        r'if\s+(\w+)\s*==\s*["\']/(?:status|help|ping|usage)',
        source,
    )
    if text_var_match:
        actual_var = text_var_match.group(1)
        if actual_var != 'text':
            adapted = adapted.replace('text ==', f'{actual_var} ==')
            adapted = adapted.replace('text.startswith(', f'{actual_var}.startswith(')
            adapted = adapted.replace('text.split(', f'{actual_var}.split(')
            print(f"  Adapted text variable: text -> {actual_var}")

    return adapted


def ensure_subprocess_import(source: str) -> str:
    """Ensure `import subprocess` exists at the top of the file."""
    if re.search(r'^import subprocess\b', source, re.MULTILINE):
        print("  import subprocess already present")
        return source
    if re.search(r'^from subprocess import', source, re.MULTILINE):
        print("  subprocess already imported (from-style)")
        return source

    # Insert after the last top-level import
    lines = source.split('\n')
    last_import = 0
    for i, line in enumerate(lines):
        if re.match(r'^(import |from \S+ import )', line):
            last_import = i

    lines.insert(last_import + 1, 'import subprocess')
    print(f"  Added 'import subprocess' after line {last_import + 1}")
    return '\n'.join(lines)


def check_already_patched(source: str) -> bool:
    """Return True if /doctor handler is already present."""
    return '/doctor' in source and 'doctor_check.py' in source


def main():
    dry_run = '--dry-run' in sys.argv

    print(f"=== Add /doctor command to Chaguli agent.py ===")
    print(f"Target: {AGENT_PY}")
    print(f"Mode:   {'DRY RUN' if dry_run else 'LIVE'}")
    print()

    # --- Read ---
    if not AGENT_PY.exists():
        print(f"ERROR: {AGENT_PY} not found")
        sys.exit(1)

    source = AGENT_PY.read_text()
    print(f"  Read {len(source)} bytes, {source.count(chr(10))} lines")

    # --- Check idempotence ---
    if check_already_patched(source):
        print("\n  /doctor handler already present — nothing to do.")
        sys.exit(0)

    # --- Find insertion point ---
    insert_after, context = find_insertion_point(source)
    print(f"  Will insert after line {insert_after + 1}: {context}")

    # --- Adapt the block ---
    block = adapt_block(source, DOCTOR_BLOCK)

    # --- Ensure subprocess import ---
    source = ensure_subprocess_import(source)

    # --- Insert the block ---
    lines = source.split('\n')
    # Detect indentation from nearby code
    ref_line = lines[insert_after] if insert_after < len(lines) else lines[insert_after - 1]
    # The block already has 8-space indent (two levels), which is typical

    lines.insert(insert_after + 1, block)
    new_source = '\n'.join(lines)

    # --- Preview ---
    print(f"\n--- Code to insert ({block.count(chr(10))} lines) ---")
    for line in block.split('\n')[:10]:
        print(f"  | {line}")
    if block.count('\n') > 10:
        print(f"  | ... ({block.count(chr(10)) - 10} more lines)")
    print("--- end preview ---\n")

    if dry_run:
        print("DRY RUN — no changes written.")
        sys.exit(0)

    # --- Backup ---
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = AGENT_PY.with_suffix(f".py.bak.{ts}")
    shutil.copy2(AGENT_PY, backup)
    print(f"  Backup: {backup}")

    # --- Write ---
    AGENT_PY.write_text(new_source)
    print(f"  Wrote {len(new_source)} bytes to {AGENT_PY}")
    print("\nDone. Restart Chaguli to pick up the new commands.")
    print("  /doctor          — full health report")
    print("  /doctor fix NAME — run a specific runbook")
    print("  /logs SERVICE    — show recent logs")


if __name__ == '__main__':
    main()
