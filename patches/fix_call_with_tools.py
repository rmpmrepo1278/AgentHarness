#!/usr/bin/env python3
"""Targeted fix: replace direct Groq call in call_with_tools() with proxy call.
Only modifies the call_with_tools function, leaves call_cloud untouched.
"""
import sys
from pathlib import Path

target = Path.home() / "openclaw/chaguli/clients/llm_client.py"
if not target.exists():
    print(f"ERROR: {target} not found")
    sys.exit(1)

lines = target.read_text().splitlines()

# Find call_with_tools function start
func_start = None
for i, line in enumerate(lines):
    if line.startswith("def call_with_tools("):
        func_start = i
        break

if func_start is None:
    print("ERROR: call_with_tools function not found")
    sys.exit(1)

# Find the Groq URL within call_with_tools (search from func_start onwards)
fixed = False
for i in range(func_start, min(func_start + 80, len(lines))):
    if '"https://api.groq.com/openai/v1/chat/completions"' in lines[i]:
        # Replace Groq URL with proxy
        lines[i] = lines[i].replace(
            '"https://api.groq.com/openai/v1/chat/completions"',
            'f"{LLAMA_URL}/v1/chat/completions"'
        )
        # Fix the next two lines (headers) — remove Authorization, keep Content-Type
        if i + 1 < len(lines) and "Authorization" in lines[i + 1]:
            indent = len(lines[i + 1]) - len(lines[i + 1].lstrip())
            lines[i + 1] = " " * indent + 'headers={"Content-Type": "application/json"},'
            # Remove the old Content-Type line (was on its own line)
            if i + 2 < len(lines) and "Content-Type" in lines[i + 2]:
                lines.pop(i + 2)
        fixed = True
        break

if not fixed:
    print("ERROR: Groq URL not found in call_with_tools")
    sys.exit(1)

target.write_text("\n".join(lines) + "\n")
print(f"Fixed call_with_tools in {target}")
print(f"  Line {func_start + 1}: call_with_tools function")
print(f"  Replaced Groq URL with proxy (LLAMA_URL)")
print(f"  Removed Authorization header")
