#!/usr/bin/env python3
"""Generate monthly finance report."""
import json
import sys
from datetime import datetime

subs_file = sys.argv[1]
bills_file = sys.argv[2]
budget_file = sys.argv[3]

today = datetime.now()
month_str = today.strftime("%Y-%m")

lines = [f"💰 **Monthly Finance Report** — {month_str}\n"]

# Subscriptions
try:
    with open(subs_file) as f:
        subs = json.load(f)
    active = [s for s in subs.get("subscriptions", []) if s.get("status") == "active"]
    total_monthly = sum(s.get("amount", 0) for s in active)
    lines.append(f"📋 **Subscriptions**: {len(active)} active, ${total_monthly:.2f}/month")
    for s in sorted(active, key=lambda x: x.get("amount", 0), reverse=True)[:10]:
        lines.append(f"  • {s['name']}: ${s.get('amount', 0):.2f}/{s.get('frequency', 'monthly')}")
    lines.append("")
except Exception as e:
    lines.append(f"📋 Subscriptions: Error reading data ({e})\n")

# Bills
try:
    with open(bills_file) as f:
        bills = json.load(f)
    active_bills = bills.get("bills", [])
    total_bills = sum(b.get("amount", 0) for b in active_bills)
    lines.append(f"🧾 **Bills**: {len(active_bills)} tracked, ${total_bills:.2f}/month")
    for b in sorted(active_bills, key=lambda x: x.get("due_day", 0)):
        lines.append(f"  • {b['name']}: ${b.get('amount', 0):.2f} (due: {b.get('due_day', '?')})")
    lines.append("")
except Exception as e:
    lines.append(f"🧾 Bills: Error reading data ({e})\n")

# Budget vs Actual
try:
    with open(budget_file) as f:
        budget = json.load(f)
    monthly = budget.get("monthly_budget", {})
    actuals = budget.get("actuals", {}).get(month_str, {})
    if monthly:
        lines.append("📊 **Budget vs Actual**:")
        for cat, target in monthly.items():
            actual = actuals.get(cat, 0)
            diff = target - actual
            emoji = "✅" if diff >= 0 else "🔴"
            lines.append(f"  {emoji} {cat}: ${actual:.0f} / ${target:.0f} (${diff:+.0f})")
except Exception as e:
    lines.append(f"📊 Budget: Error reading data ({e})")

print("\n".join(lines))
