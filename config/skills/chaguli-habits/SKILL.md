---
name: chaguli-habits
description: Track habits and goals, provide gentle nudges, celebrate milestones
requires:
  binaries: ["python3"]
---

# Habits & Goals

When Rohit sets a goal or asks you to track a habit:

## Add a Habit/Goal

```bash
python3 -c "
import json, os
from datetime import datetime

HABITS_FILE = '/opt/agentharness/habits.json'
habits = []
if os.path.exists(HABITS_FILE):
    habits = json.load(open(HABITS_FILE))

habits.append({
    'name': 'HABIT_NAME',
    'description': 'DESCRIPTION',
    'frequency': 'daily|weekly|whenever',
    'created_at': datetime.now().isoformat(),
    'check_ins': [],
    'active': True
})

json.dump(habits, open(HABITS_FILE, 'w'), indent=2)
print('Habit tracked. I will nudge you about it.')
"
```

## Check In on a Habit

```bash
python3 -c "
import json
from datetime import datetime

habits = json.load(open('/opt/agentharness/habits.json'))
for h in habits:
    if 'HABIT_NAME' in h['name'].lower() and h['active']:
        h['check_ins'].append({
            'date': datetime.now().isoformat(),
            'note': 'OPTIONAL_NOTE'
        })
        streak = len(h['check_ins'])
        print(f'Checked in! Streak: {streak}')
        break

json.dump(habits, open('/opt/agentharness/habits.json', 'w'), indent=2)
"
```

## Nudge Logic (used by morning briefing)

When generating the morning briefing, check habits:

```bash
python3 -c "
import json
from datetime import datetime, timedelta

habits = json.load(open('/opt/agentharness/habits.json'))
now = datetime.now()
nudges = []

for h in habits:
    if not h.get('active'):
        continue

    last_checkin = None
    if h['check_ins']:
        last_checkin = datetime.fromisoformat(h['check_ins'][-1]['date'])

    freq = h.get('frequency', 'daily')
    overdue = False

    if freq == 'daily' and (not last_checkin or (now - last_checkin).days >= 1):
        overdue = True
    elif freq == 'weekly' and (not last_checkin or (now - last_checkin).days >= 7):
        overdue = True

    if overdue:
        streak = len(h['check_ins'])
        if streak == 0:
            nudges.append(f\"Haven't started '{h['name']}' yet. Today?\")
        else:
            nudges.append(f\"'{h['name']}' — streak: {streak}. Don't break it!\")

for n in nudges:
    print(n)
"
```

## Milestones

Celebrate streaks at: 7, 14, 30, 50, 100 days.

When a streak hits a milestone, include in the morning briefing:
> "Your homelab has been running 30 days straight. Nice."

## How to Nudge

- Be gentle, not nagging
- One nudge per day max per habit
- If ignored for 2 weeks, ask: "Still tracking [habit]? Want me to stop nudging?"
- Celebrate milestones genuinely but briefly
