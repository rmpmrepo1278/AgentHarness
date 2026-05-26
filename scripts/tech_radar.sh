#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# tech_radar.sh — Weekly technology radar scan
#
# Scans:
# 1. GitHub trending (AI/ML, agent, homelab, MCP repos)
# 2. Hacker News top stories (AI/agent related)
# 3. New model releases (from known sources)
# 4. New MCP servers
# 5. OpenClaw skill updates
#
# Generates a report and sends top findings to Chaguli/Telegram.
# Schedule: Weekly via harness_registry.yaml
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

REPORTS_DIR="${AH_REPORTS_DIR:-/home/rohit/agentharness/reports}"
DATA_DIR="${AH_DATA_DIR:-/home/rohit/agentharness/data}"
REPORT_FILE="${REPORTS_DIR}/tech_radar_$(date +%Y%m%d).json"
LATEST_FILE="${DATA_DIR}/tech_radar_latest.json"
TMP_DIR=$(mktemp -d)

mkdir -p "${REPORTS_DIR}" "${DATA_DIR}"

log_info "Starting weekly tech radar scan..."

# --- 1. GitHub Trending (AI/Agent/Homelab) ---
log_info "Scanning GitHub trending..."
GITHUB_TRENDING=$(curl -sf --max-time 15 "https://api.github.com/search/repositories?q=topic:ai-agent+topic:llm+created:>$(date -u -d '7 days ago' +%Y-%m-%d)&sort=stars&order=desc&per_page=10" 2>/dev/null) || GITHUB_TRENDING=""

if [ -n "${GITHUB_TRENDING}" ]; then
    echo "${GITHUB_TRENDING}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
repos = []
for item in data.get('items', [])[:10]:
    repos.append({
        'name': item['full_name'],
        'stars': item['stargazers_count'],
        'description': (item.get('description') or '')[:120],
        'url': item['html_url'],
        'created': item['created_at'][:10]
    })
with open('${TMP_DIR}/github.json', 'w') as f:
    json.dump(repos, f, indent=2)
print(f'GitHub: {len(repos)} trending repos')
" 2>/dev/null || echo "[]" > "${TMP_DIR}/github.json"
else
    echo "[]" > "${TMP_DIR}/github.json"
fi

# --- 2. Hacker News Top Stories (AI-related) ---
log_info "Scanning Hacker News..."
HN_TOP=$(curl -sf --max-time 15 "https://hacker-news.firebaseio.com/v0/topstories.json" 2>/dev/null) || HN_TOP=""

if [ -n "${HN_TOP}" ]; then
    # Get top 30 story IDs, fetch each one
    STORY_IDS=$(echo "${HN_TOP}" | python3 -c "import json,sys; ids=json.load(sys.stdin)[:30]; print(' '.join(str(i) for i in ids))" 2>/dev/null || echo "")

    echo "[]" > "${TMP_DIR}/hn_stories.json"
    for story_id in ${STORY_IDS}; do
        STORY=$(curl -sf --max-time 5 "https://hacker-news.firebaseio.com/v0/item/${story_id}.json" 2>/dev/null) || continue

        python3 -c "
import json, sys
story = json.loads(sys.stdin.read())
title = story.get('title', '').lower()
# Filter for AI/agent/LLM/homelab relevant
keywords = ['ai', 'llm', 'gpt', 'claude', 'agent', 'mcp', 'docker', 'homelab', 'openai', 'anthropic', 'gemini', 'copilot', 'rag', 'vector', 'ollama', 'llama']
if any(kw in title for kw in keywords):
    with open('${TMP_DIR}/hn_stories.json') as f:
        stories = json.load(f)
    stories.append({
        'title': story.get('title', ''),
        'url': story.get('url', f'https://news.ycombinator.com/item?id={story.get(\"id\",\"\"))}'),
        'score': story.get('score', 0),
        'comments': story.get('descendants', 0)
    })
    with open('${TMP_DIR}/hn_stories.json', 'w') as f:
        json.dump(stories, f, indent=2)
" <<< "${STORY}" 2>/dev/null || true
    done
fi

# --- 3. New Model Releases (from HuggingFace) ---
log_info "Checking for new model releases..."
MODELS=$(curl -sf --max-time 15 "https://huggingface.co/api/models?sort=modified&direction=-1&limit=20&filter=text-generation" 2>/dev/null) || MODELS=""

if [ -n "${MODELS}" ]; then
    echo "${MODELS}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
models = []
for m in data[:10]:
    models.append({
        'name': m.get('modelId', ''),
        'downloads': m.get('downloads', 0),
        'likes': m.get('likes', 0),
        'last_modified': m.get('lastModified', '')[:10]
    })
with open('${TMP_DIR}/models.json', 'w') as f:
    json.dump(models, f, indent=2)
print(f'Models: {len(models)} recent')
" 2>/dev/null || echo "[]" > "${TMP_DIR}/models.json"
else
    echo "[]" > "${TMP_DIR}/models.json"
fi

# --- 4. MCP Server Discovery ---
log_info "Checking for new MCP servers..."
MCP_SERVERS=$(curl -sf --max-time 15 "https://api.github.com/search/repositories?q=model-context-protocol+OR+mcp-server+OR+mcp-tool&sort=stars&order=desc&per_page=10" 2>/dev/null) || MCP_SERVERS=""

if [ -n "${MCP_SERVERS}" ]; then
    echo "${MCP_SERVERS}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
servers = []
for item in data.get('items', [])[:10]:
    servers.append({
        'name': item['full_name'],
        'stars': item['stargazers_count'],
        'description': (item.get('description') or '')[:120],
        'url': item['html_url']
    })
with open('${TMP_DIR}/mcp.json', 'w') as f:
    json.dump(servers, f, indent=2)
print(f'MCP: {len(servers)} servers found')
" 2>/dev/null || echo "[]" > "${TMP_DIR}/mcp.json"
else
    echo "[]" > "${TMP_DIR}/mcp.json"
fi

# --- Combine Report ---
python3 << PYEOF
import json, datetime

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return []

report = {
    "scan_date": datetime.datetime.now().isoformat(),
    "github_trending": load_json("${TMP_DIR}/github.json"),
    "hacker_news": load_json("${TMP_DIR}/hn_stories.json"),
    "new_models": load_json("${TMP_DIR}/models.json"),
    "mcp_servers": load_json("${TMP_DIR}/mcp.json"),
    "summary": {
        "github_count": len(load_json("${TMP_DIR}/github.json")),
        "hn_count": len(load_json("${TMP_DIR}/hn_stories.json")),
        "models_count": len(load_json("${TMP_DIR}/models.json")),
        "mcp_count": len(load_json("${TMP_DIR}/mcp.json"))
    }
}

with open("${REPORT_FILE}", "w") as f:
    json.dump(report, f, indent=2)
with open("${LATEST_FILE}", "w") as f:
    json.dump(report, f, indent=2)

s = report["summary"]
print(f"Tech Radar: {s['github_count']} GitHub, {s['hn_count']} HN, {s['models_count']} models, {s['mcp_count']} MCP")
PYEOF

rm -rf "${TMP_DIR}"
log_ok "Weekly tech radar complete"
