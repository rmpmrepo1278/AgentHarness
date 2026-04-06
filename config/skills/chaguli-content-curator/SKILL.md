---
name: chaguli-content-curator
description: Proactively find and curate interesting content about AI, homelabs, self-hosting, and topics Rohit cares about
requires:
  binaries: ["curl", "python3"]
---

# Proactive Content Curation

You curate interesting content for Rohit. You can be triggered proactively (via scheduled tasks) or on-demand when he asks "what's new?" or "anything interesting today?"

## On-Demand: "What's interesting today?"

Search for recent content across Rohit's interest areas and summarize the best finds.

```bash
SEARXNG_URL="${SEARXNG_URL:-http://localhost:8888}"

# Search multiple interest areas
for topic in \
  "local LLM new model release site:reddit.com/r/LocalLLaMA" \
  "self-hosted new tool site:reddit.com/r/selfhosted" \
  "homelab project site:reddit.com/r/homelab" \
  "openclaw new skill site:reddit.com" \
  "llama.cpp ik_llama performance improvement"; do

  echo "=== Searching: ${topic%% site:*} ==="
  curl -sf "${SEARXNG_URL}/search?q=$(python3 -c "import urllib.parse; print(urllib.parse.quote('${topic}'))")&format=json&time_range=week" 2>/dev/null | \
    python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for r in data.get('results', [])[:3]:
        print(f\"  • {r.get('title', '')} — {r.get('url', '')}\")
        content = r.get('content', '')[:150]
        if content:
            print(f\"    {content}\")
except:
    print('  (search failed)')
"
  echo ""
done
```

## Curate and Save Interesting Finds

After searching, save noteworthy items to the knowledge store:

```bash
bash /opt/agentharness/scripts/chaguli_memory.sh add knowledge "DESCRIPTION OF INTERESTING FIND — URL" system
```

## Proactive Curation (called by scheduler)

This generates a curated digest. The scheduler calls this during online hours and Chaguli sends the best finds via Telegram.

```bash
bash /opt/agentharness/scripts/weekly_optimize.sh
```

The weekly_optimize.sh already searches for new models, tools, and techniques. Its report is at `/opt/agentharness/reports/weekly_*.md`.

## How to Respond

When sharing curated content:
1. Lead with the most interesting/actionable find
2. Keep each item to 2-3 sentences max
3. Always include the URL
4. If something is directly relevant to Rohit's setup (e.g., a model that fits his RAM), call that out explicitly
5. Offer to take action: "Want me to save this?" / "Want me to deploy this tonight?" / "Want me to benchmark this?"

## Interest Profile

Rohit cares about:
- **Local LLMs**: New models, quantization techniques, inference speedups, MoE optimization
- **Self-hosting**: New tools, Docker setups, privacy-focused alternatives
- **Homelab**: Hardware, networking, automation, monitoring
- **AI agents**: OpenClaw skills, agent frameworks, tool use
- **Cost optimization**: Free/cheap alternatives, efficient setups

He does NOT care about:
- Cloud-only AI services
- Enterprise/corporate tooling
- Crypto/blockchain
- Social media trends
