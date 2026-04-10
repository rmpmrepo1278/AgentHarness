#!/usr/bin/env python3
"""Patch Chaguli's llm_client.py to route call_with_tools() through the proxy.

Run on the homelab:
    python3 ~/chaguli-proxy-routing-patcher.py

What it does:
1. Reads ~/openclaw/chaguli/clients/llm_client.py
2. Finds the call_with_tools() function
3. Replaces the direct Groq call with a proxy call
4. Adds _record_proxy_call() method
5. Creates a timestamped backup before modifying
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

LLM_CLIENT = Path.home() / "openclaw" / "chaguli" / "clients" / "llm_client.py"

# The replacement call_with_tools function
NEW_CALL_WITH_TOOLS = '''    def call_with_tools(self, messages, tools, tool_choice="auto", model=None):
        """Call LLM with tool/function calling support via proxy.

        Routes through AgentHarness proxy which handles provider rotation
        (Groq, Google, Cerebras, SambaNova, OpenRouter) and budget tracking.
        """
        proxy_url = LLAMA_URL + "/v1/chat/completions"

        payload = {
            "model": model or self.groq_model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "max_tokens": 1024,
            "temperature": 0.1,
        }

        try:
            resp = requests.post(
                proxy_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            self._record_proxy_call(data)
            return data["choices"][0]["message"]
        except Exception as e:
            logger.warning(f"Proxy tool call failed: {e}")
            # Fallback to local without tools
            return self.call_local(messages[-1]["content"] if messages else "")'''

NEW_RECORD_PROXY = '''
    def _record_proxy_call(self, data):
        """Record usage from a proxy tool call for local tracking."""
        try:
            timings = data.get("timings", {})
            usage = data.get("usage", {})
            provider = timings.get("provider", "unknown")
            tokens = usage.get("total_tokens", 0)
            logger.debug(
                f"Proxy tool call: provider={provider}, tokens={tokens}"
            )
            # If the proxy used Groq, still count against the local Groq
            # tracker so call_smart() cap tracking stays accurate
            if provider == "groq":
                self._record_groq_call()
        except Exception:
            pass'''


def find_function_bounds(lines: list[str], func_name: str) -> tuple[int, int]:
    """Find the start and end line indices of a method definition.

    Returns (start, end) where end is the last line of the function body.
    """
    start = None
    indent = None

    for i, line in enumerate(lines):
        # Match the def line
        if start is None:
            m = re.match(r'^(\s*)def ' + re.escape(func_name) + r'\(', line)
            if m:
                start = i
                indent = len(m.group(1))
                continue

        if start is not None and i > start:
            # A line that is not blank and not more indented than the def
            # means we've exited the function.
            stripped = line.rstrip()
            if stripped == '':
                continue
            line_indent = len(line) - len(line.lstrip())
            if line_indent <= indent:
                return start, i - 1

    if start is not None:
        # Function extends to end of file
        return start, len(lines) - 1

    return -1, -1


def patch(dry_run: bool = False) -> bool:
    if not LLM_CLIENT.exists():
        print(f"ERROR: File not found: {LLM_CLIENT}")
        print("Make sure you're running this on the homelab.")
        return False

    content = LLM_CLIENT.read_text()
    lines = content.splitlines(keepends=True)

    # Find call_with_tools
    start, end = find_function_bounds(
        [l.rstrip('\n') for l in lines], "call_with_tools"
    )
    if start < 0:
        print("ERROR: Could not find call_with_tools() function.")
        return False

    print(f"Found call_with_tools() at lines {start + 1}-{end + 1}")

    # Check if already patched
    func_text = ''.join(lines[start:end + 1])
    if 'proxy_url' in func_text and '_record_proxy_call' in func_text:
        print("Already patched -- call_with_tools() already uses proxy.")
        return True

    # Check if _record_proxy_call already exists
    has_record_proxy = '_record_proxy_call' in content

    if dry_run:
        print("DRY RUN -- would replace lines {}-{} with proxy version".format(
            start + 1, end + 1
        ))
        print("\n--- Current code ---")
        print(func_text)
        print("\n--- Replacement ---")
        print(NEW_CALL_WITH_TOOLS)
        if not has_record_proxy:
            print(NEW_RECORD_PROXY)
        return True

    # Create backup
    backup_name = LLM_CLIENT.with_suffix(
        f".py.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    shutil.copy2(LLM_CLIENT, backup_name)
    print(f"Backup: {backup_name}")

    # Build the new file
    new_lines = lines[:start]
    new_lines.append(NEW_CALL_WITH_TOOLS + '\n')

    # Add _record_proxy_call right after call_with_tools if it doesn't exist
    if not has_record_proxy:
        new_lines.append(NEW_RECORD_PROXY + '\n')

    new_lines.extend(lines[end + 1:])

    LLM_CLIENT.write_text(''.join(new_lines))
    print(f"Patched: {LLM_CLIENT}")
    print("Restart Chaguli: docker restart chaguli")
    return True


def main():
    dry_run = '--dry-run' in sys.argv or '-n' in sys.argv
    if dry_run:
        print("=== DRY RUN MODE ===\n")

    ok = patch(dry_run=dry_run)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
