---
name: chaguli-research
description: Deep research assistant — investigate topics, compare options, summarize findings with actionable recommendations
requires:
  binaries: ["curl"]
---

# Research Assistant

When Rohit asks you to research something, compare options, or investigate a topic — use this structured approach.

## Quick Lookup (single question)

```bash
SEARXNG_URL="${SEARXNG_URL:-http://localhost:8888}"
curl -sf "${SEARXNG_URL}/search?q=QUERY_HERE&format=json" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for r in data.get('results', [])[:5]:
    print(f'• {r.get(\"title\", \"\")}')
    print(f'  {r.get(\"url\", \"\")}')
    print(f'  {r.get(\"content\", \"\")[:150]}')
    print()
"
```

## Deep Research (comparison, decision support)

When the question requires comparing options or making a decision:

1. **Search multiple angles** — run 3-5 searches with different queries
2. **Check Reddit** — add `site:reddit.com` for real user experiences
3. **Check your memory** — have you researched this before?

```bash
bash /opt/agentharness/scripts/chaguli_memory.sh context | grep -i "TOPIC"
```

4. **Synthesize** — don't just list results. Give a recommendation:
   - "For YOUR setup (36GB RAM, Docker, Debian), I'd go with X because..."
   - "The community consensus is X, but given your constraint Y, consider Z"

5. **Save the finding** if it's reusable:

```bash
bash /opt/agentharness/scripts/chaguli_memory.sh add knowledge "TOPIC: KEY FINDING" interaction
```

## Response Format for Research

### Quick questions
2-3 sentences. Answer + source.

### Comparisons
Short table format:
| Option | Pros | Cons | Fits Your Setup? |
Then a 1-sentence recommendation.

### "Should I..." decisions
1. What the options are (brief)
2. What matters for YOUR situation (specific to homelab constraints)
3. Recommendation with reasoning
4. "Want me to set it up?" (if actionable)
