#!/usr/bin/env python3
"""Run subscription audit — find unused subscriptions."""
import json
import sys
from datetime import datetime

subs_file = sys.argv[1]

try:
    with open(subs_file) as f:
        data = json.load(f)
except FileNotFoundError:
    print("No subscriptions tracked yet.")
    sys.exit(0)

subs = data.get("subscriptions", [])
if not subs:
    print("No subscriptions tracked yet. Add some to get started!")
    sys.exit(0)

today = datetime.now()
lines = ["🔍 **Subscription Audit**\n"]

# Find potentially unused subscriptions
potentially_unused = []
for s in subs:
    if s.get("status") != "active":
        continue
    last_used = s.get("last_used")
    if last_used:
        try:
            last_date = datetime.strptime(last_used, "%Y-%m-%d")
            days_since = (today - last_date).days
            if days_since > 60:
                potentially_unused.append((s, days_since))
        except (ValueError, TypeError):
            pass

if potentially_unused:
    lines.append("⚠️ **Potentially unused (60+ days since last use)**:")
    total_savings = 0
    for s, days in sorted(potentially_unused, key=lambda x: x[1], reverse=True):
        amount = s.get("amount", 0)
        freq = s.get("frequency", "monthly")
        lines.append(f"  • {s['name']}: ${amount:.2f}/{freq} — last used {days} days ago")
        if freq == "monthly":
            total_savings += amount
        elif freq == "yearly":
            total_savings += amount / 12
    lines.append(f"\n💡 Potential monthly savings: ${total_savings:.2f}")
else:
    lines.append("✅ All subscriptions appear to be in regular use.")

# Summary
active = [s for s in subs if s.get("status") == "active"]
total_monthly = sum(s.get("amount", 0) for s in active if s.get("frequency") == "monthly")
total_yearly = sum(s.get("amount", 0) for s in active if s.get("frequency") == "yearly")
combined = total_monthly + total_yearly / 12

lines.append(f"\n📊 Total: {len(active)} active subscriptions")
lines.append(f"   Monthly: ${total_monthly:.2f}/mo | Yearly: ${total_yearly:.2f}/yr")
lines.append(f"   Combined: ${combined:.2f}/mo")

print("\n".join(lines))
