#!/usr/bin/env python3
"""Generate weekly habit review."""
import json
import sys
from datetime import datetime, timedelta

habits_file = sys.argv[1]

try:
    with open(habits_file) as f:
        data = json.load(f)
except FileNotFoundError:
    print("No habits tracked yet.")
    sys.exit(0)

today = datetime.now()
habits = data.get("habits", [])

if not habits:
    print("No habits tracked yet. Add some habits to get started!")
    sys.exit(0)

lines = [f"📊 **Weekly Habit Review** — Week of {today.strftime('%Y-%m-%d')}\n"]

for habit in habits:
    name = habit.get("name", "Unknown")
    streak = habit.get("streak_current", 0)
    longest = habit.get("streak_longest", 0)
    history = habit.get("history", {})

    week_completions = 0
    for i in range(7):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        if day in history and history[day].get("done"):
            week_completions += 1

    target = habit.get("target_per_week", 7)
    pct = int(week_completions / max(target, 1) * 100)

    if pct >= 80:
        emoji = "✅"
    elif pct >= 50:
        emoji = "🟡"
    else:
        emoji = "🔴"

    lines.append(f"{emoji} **{name}**: {week_completions}/{target} ({pct}%) | Streak: {streak} days (best: {longest})")

print("\n".join(lines))
