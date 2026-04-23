#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# weekly_optimize.sh — Weekly self-acting optimization pipeline
#
#   1. REPORT  — search web for new models/engines/techniques (existing)
#   2. DISCOVER — query HuggingFace API for new GGUF models
#   3. DOWNLOAD — fetch top candidates that fit in RAM
#   4. BUILD   — update inference engines if new commits available
#   5. BENCHMARK — benchmark all model×engine combos and auto-switch
#
# Usage:
#   ./weekly_optimize.sh              # full pipeline
#   ./weekly_optimize.sh --dry-run    # report + discover only, no downloads/builds
#   ./weekly_optimize.sh --skip-report # skip web search, go straight to action
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

SEARXNG_URL="${SEARXNG_URL:-http://localhost:8118}"
LLM_URL="${LLM_PRIMARY_URL:-http://localhost:8080}"
WEEKLY_REPORT="${AH_REPORTS_DIR}/weekly_$(timestamp).md"
MODEL_DIR="${AH_MODEL_DIR:-/home/rohit/models}"
CANDIDATES_FILE="${AH_DATA_DIR}/model_candidates.json"

# Flags
DRY_RUN=false
SKIP_REPORT=false
MAX_NEW_MODELS=2

for arg in "$@"; do
    case "${arg}" in
        --dry-run)   DRY_RUN=true ;;
        --skip-report) SKIP_REPORT=true ;;
    esac
done

# Load environment
[ -f "${AH_DATA_DIR}/hw_profile.env" ] && source "${AH_DATA_DIR}/hw_profile.env"
[ -f "${AH_DATA_DIR}/best_config.env" ] && source "${AH_DATA_DIR}/best_config.env"

# =============================================================================
# EXISTING REPORT FUNCTIONS (unchanged)
# =============================================================================

search() {
    local query="$1"
    local max_results="${2:-10}"

    curl -sf "${SEARXNG_URL}/search?q=$(python3 -c "import urllib.parse; print(urllib.parse.quote('${query}'))")&format=json&engines=google,duckduckgo,github" \
        2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    results = data.get('results', [])[:${max_results}]
    for r in results:
        print(json.dumps({'title': r.get('title',''), 'url': r.get('url',''), 'content': r.get('content','')[:200]}))
except:
    pass
" 2>/dev/null
}

ask_llm() {
    local prompt="$1"
    local max_tokens="${2:-500}"

    local response
    response=$(curl -sf --max-time 600 "${LLM_URL}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "
import json
print(json.dumps({
    'messages': [
        {'role': 'system', 'content': 'You are a concise technical analyst for a homelab running on a Ryzen 4700U with 36GB RAM (4+32 mismatched DDR4). Focus on practical recommendations for CPU-only LLM inference. Current setup: ${BEST_MODEL:-unknown} on ${BEST_ENGINE:-ik-llama}. Be specific about model names, sizes, and expected performance.'},
        {'role': 'user', 'content': '''${prompt}'''}
    ],
    'max_tokens': ${max_tokens},
    'temperature': 0.3
}))
" 2>/dev/null)" 2>/dev/null) || { echo "(LLM unavailable — raw results only)"; return; }

    echo "${response}" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d['choices'][0]['message']['content'])
except:
    print('(Failed to parse LLM response)')
" 2>/dev/null
}

search_new_models() {
    log_info "Searching for new LLM models..."

    local queries=(
        "new GGUF models 2026 llama.cpp small MoE tool calling"
        "best local LLM models homelab CPU inference $(date +%Y) site:reddit.com/r/LocalLLaMA"
        "Qwen Gemma Mistral new model release $(date +%B) $(date +%Y) GGUF"
        "MoE model small active parameters CPU inference $(date +%Y)"
        "best function calling local LLM model $(date +%Y)"
    )

    local all_results=""
    for query in "${queries[@]}"; do
        local results
        results=$(search "${query}" 5)
        all_results+="${results}"$'\n'
    done

    echo "## New Models" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"

    local unique_results
    unique_results=$(echo "${all_results}" | sort -u | head -20)

    echo "${unique_results}" | while read -r line; do
        if [ -n "${line}" ]; then
            local title url
            title=$(echo "${line}" | python3 -c "import sys,json; print(json.loads(sys.stdin.readline()).get('title',''))" 2>/dev/null || echo "")
            url=$(echo "${line}" | python3 -c "import sys,json; print(json.loads(sys.stdin.readline()).get('url',''))" 2>/dev/null || echo "")
            [ -n "${title}" ] && echo "- [${title}](${url})" >> "${WEEKLY_REPORT}"
        fi
    done

    echo "" >> "${WEEKLY_REPORT}"

    local analysis
    analysis=$(ask_llm "Based on these search results about new LLM models, identify any models that could improve our setup. Current: ${BEST_MODEL:-Qwen3.5-35B-A3B}. Requirements: fits in 36GB RAM, CPU-only, good tool calling. Search results: ${unique_results}")
    echo "### Analysis" >> "${WEEKLY_REPORT}"
    echo "${analysis}" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"
}

search_new_engines() {
    log_info "Searching for inference engine updates..."

    local queries=(
        "ik_llama.cpp update CPU performance $(date +%Y)"
        "llama.cpp new release CPU optimization MoE $(date +%Y)"
        "local LLM inference engine faster than llama.cpp CPU $(date +%Y)"
        "PowerInfer MoE CPU inference update $(date +%Y)"
        "speculative decoding CPU improvements $(date +%Y)"
    )

    local all_results=""
    for query in "${queries[@]}"; do
        local results
        results=$(search "${query}" 5)
        all_results+="${results}"$'\n'
    done

    echo "## Inference Engines & Optimization" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"

    local unique_results
    unique_results=$(echo "${all_results}" | sort -u | head -15)

    echo "${unique_results}" | while read -r line; do
        if [ -n "${line}" ]; then
            local title url
            title=$(echo "${line}" | python3 -c "import sys,json; print(json.loads(sys.stdin.readline()).get('title',''))" 2>/dev/null || echo "")
            url=$(echo "${line}" | python3 -c "import sys,json; print(json.loads(sys.stdin.readline()).get('url',''))" 2>/dev/null || echo "")
            [ -n "${title}" ] && echo "- [${title}](${url})" >> "${WEEKLY_REPORT}"
        fi
    done

    echo "" >> "${WEEKLY_REPORT}"

    local analysis
    analysis=$(ask_llm "Any new inference engine updates or optimization techniques that could speed up our CPU inference? Current: ${BEST_ENGINE:-ik-llama}. We have AVX2 (no AVX-512). Search results: ${unique_results}")
    echo "### Analysis" >> "${WEEKLY_REPORT}"
    echo "${analysis}" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"
}

search_techniques() {
    log_info "Searching for new techniques and optimizations..."

    local queries=(
        "KV cache optimization llama.cpp CPU $(date +%Y)"
        "quantization technique GGUF improvement $(date +%Y)"
        "prompt caching local LLM optimization $(date +%Y)"
        "homelab AI agent best practices $(date +%Y) site:reddit.com"
        "MCP server new tools Docker homelab $(date +%Y)"
    )

    local all_results=""
    for query in "${queries[@]}"; do
        local results
        results=$(search "${query}" 5)
        all_results+="${results}"$'\n'
    done

    echo "## Techniques & Best Practices" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"

    local unique_results
    unique_results=$(echo "${all_results}" | sort -u | head -15)

    echo "${unique_results}" | while read -r line; do
        if [ -n "${line}" ]; then
            local title url
            title=$(echo "${line}" | python3 -c "import sys,json; print(json.loads(sys.stdin.readline()).get('title',''))" 2>/dev/null || echo "")
            url=$(echo "${line}" | python3 -c "import sys,json; print(json.loads(sys.stdin.readline()).get('url',''))" 2>/dev/null || echo "")
            [ -n "${title}" ] && echo "- [${title}](${url})" >> "${WEEKLY_REPORT}"
        fi
    done

    echo "" >> "${WEEKLY_REPORT}"

    local analysis
    analysis=$(ask_llm "Any new techniques for optimizing local LLM performance on CPU? Focus on practical things we can apply to our setup. Search results: ${unique_results}")
    echo "### Analysis" >> "${WEEKLY_REPORT}"
    echo "${analysis}" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"
}

check_engine_updates() {
    log_info "Checking for engine updates..."

    echo "## Engine Update Status" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"

    for dir in /opt/ik_llama /opt/llama.cpp; do
        if [ -d "${dir}/.git" ]; then
            local name
            name=$(basename "${dir}")
            cd "${dir}"
            local current_hash
            current_hash=$(git rev-parse --short HEAD)
            local behind
            behind=$(git fetch --dry-run 2>&1 | wc -l)

            if [ "${behind}" -gt 0 ]; then
                local latest
                latest=$(git ls-remote --heads origin main 2>/dev/null | cut -c1-7 || echo "unknown")
                echo "- **${name}**: Current ${current_hash}, remote has updates. Consider rebuilding." >> "${WEEKLY_REPORT}"
            else
                echo "- **${name}**: Up to date (${current_hash})" >> "${WEEKLY_REPORT}"
            fi
        fi
    done

    echo "" >> "${WEEKLY_REPORT}"
}

generate_action_items() {
    log_info "Generating action items..."

    echo "## Action Items" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"

    local report_content
    report_content=$(cat "${WEEKLY_REPORT}")

    local actions
    actions=$(ask_llm "Based on this weekly optimization report, list the top 3-5 concrete action items, prioritized by impact. For each, specify: what to do, expected improvement, and effort (low/medium/high). Only recommend actions with clear evidence of improvement. Report: ${report_content}" 800)

    echo "${actions}" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"
}

# =============================================================================
# PHASE 2: DISCOVER — Query HuggingFace API for new GGUF models
# =============================================================================

discover_new_models() {
    log_header "Phase 2: Discovering New Models (HuggingFace API)"

    # Calculate available RAM for models
    local total_ram="${TOTAL_RAM_GB:-35}"
    local docker_ram
    docker_ram=$(docker stats --no-stream --format "{{.MemUsage}}" 2>/dev/null | \
        awk -F'/' '{gsub(/[^0-9.]/, "", $1); sum += $1} END {printf "%.0f", sum/1024}' 2>/dev/null || echo "4")
    # Available = total - OS(2GB) - Docker - KV cache reserve(2GB)
    local max_model_gb
    max_model_gb=$(echo "${total_ram} - 2 - ${docker_ram} - 2" | bc 2>/dev/null || echo "20")
    log_info "Max model size: ~${max_model_gb}GB (total=${total_ram}GB, docker=${docker_ram}GB)"

    # Get list of already-downloaded model filenames (lowercase for matching)
    local existing_models
    existing_models=$(find "${MODEL_DIR}" -name "*.gguf" -printf '%f\n' 2>/dev/null | tr '[:upper:]' '[:lower:]' | sort -u)

    # Query HuggingFace API for popular GGUF models
    log_info "Querying HuggingFace API..."

    python3 -c "
import json, sys, os, urllib.request, urllib.error
from datetime import datetime, timedelta

max_model_gb = float('${max_model_gb}')
model_dir = '${MODEL_DIR}'
existing = set('''${existing_models}'''.strip().split('\n')) if '''${existing_models}'''.strip() else set()

# Search terms that find good GGUF repos
# Trusted model families that work well for tool calling on CPU
TRUSTED_FAMILIES = {'qwen', 'gemma', 'llama', 'mistral', 'phi', 'deepseek', 'internlm', 'glm', 'yi', 'command-r', 'granite'}

search_terms = [
    'qwen gguf q4_k_m instruct tool',
    'gemma gguf q4_k_m instruct',
    'llama gguf q4_k_m instruct',
    'mistral gguf q4_k_m instruct',
    'phi gguf q4_k_m instruct',
    'deepseek gguf moe q4_k_m',
    'internlm gguf q4_k_m tool calling',
    'glm gguf q4_k_m instruct',
]

candidates = []
seen_repos = set()

for term in search_terms:
    try:
        url = f'https://huggingface.co/api/models?search={urllib.request.quote(term)}&filter=gguf&sort=downloads&direction=-1&limit=30'
        req = urllib.request.Request(url, headers={'User-Agent': 'AgentHarness/1.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            models = json.loads(resp.read())
    except Exception as e:
        print(f'Warning: HF API search failed for \"{term}\": {e}', file=sys.stderr)
        continue

    for model in models:
        repo_id = model.get('id', '')
        if repo_id in seen_repos:
            continue
        seen_repos.add(repo_id)

        # Must have GGUF files
        tags = model.get('tags', [])
        if 'gguf' not in tags:
            continue

        # Skip repos we likely already have (check by repo name fragments)
        repo_lower = repo_id.lower()

        # Get siblings (files) to find Q4_K_M GGUF files
        try:
            files_url = f'https://huggingface.co/api/models/{repo_id}?blobs=true'
            req2 = urllib.request.Request(files_url, headers={'User-Agent': 'AgentHarness/1.0'})
            with urllib.request.urlopen(req2, timeout=15) as resp2:
                model_info = json.loads(resp2.read())
        except Exception:
            continue

        siblings = model_info.get('siblings', [])
        gguf_files = []
        for sib in siblings:
            fname = sib.get('rfilename', '')
            if not fname.lower().endswith('.gguf'):
                continue
            size_bytes = sib.get('size', 0)
            size_gb = size_bytes / (1024**3) if size_bytes else 0

            # Prefer Q4_K_M quantization
            fname_lower = fname.lower()
            is_q4km = 'q4_k_m' in fname_lower
            is_q4ks = 'q4_k_s' in fname_lower
            is_preferred_quant = is_q4km or is_q4ks

            if not is_preferred_quant:
                continue

            # Skip if too large for RAM
            if size_gb > max_model_gb:
                continue

            # Skip if already downloaded
            if fname_lower in existing:
                continue

            gguf_files.append({
                'filename': fname,
                'size_gb': round(size_gb, 2),
                'is_q4km': is_q4km,
            })

        if not gguf_files:
            continue

        # Pick best file (prefer Q4_K_M over Q4_K_S)
        gguf_files.sort(key=lambda f: (not f['is_q4km'], f['size_gb']))
        best_file = gguf_files[0]

        # Check recency (prefer models updated in last 90 days)
        last_modified = model.get('lastModified', '')
        is_recent = False
        if last_modified:
            try:
                mod_dt = datetime.fromisoformat(last_modified.replace('Z', '+00:00'))
                is_recent = (datetime.now(mod_dt.tzinfo) - mod_dt) < timedelta(days=90)
            except Exception:
                pass

        downloads = model.get('downloads', 0)

        candidates.append({
            'repo_id': repo_id,
            'filename': best_file['filename'],
            'size_gb': best_file['size_gb'],
            'downloads': downloads,
            'is_recent': is_recent,
            'last_modified': last_modified[:10] if last_modified else 'unknown',
            'tags': [t for t in tags if t in ('text-generation', 'tool-calling', 'function-calling', 'chat', 'instruct', 'moe')],
        })

# Filter out experimental/pruned/abliterated repos
candidates = [c for c in candidates if not any(
    w in c['repo_id'].lower() or w in c['filename'].lower()
    for w in ('abliterated', 'pruned', 'harmful', 'uncensored', 'experimental')
)]

# Score and sort candidates
# Prefer: trusted family, recent, high downloads, good size, relevant tags
for c in candidates:
    score = 0
    repo_lower = c['repo_id'].lower()
    # Big bonus for trusted model families
    if any(fam in repo_lower for fam in TRUSTED_FAMILIES):
        score += 40
    # Official repos (org name matches model name) get extra trust
    org = repo_lower.split('/')[0] if '/' in repo_lower else ''
    if org in ('qwen', 'google', 'meta-llama', 'mistralai', 'microsoft', 'deepseek-ai', 'internlm', 'thudm'):
        score += 30
    if c['is_recent']:
        score += 50
    # Downloads indicate community validation
    score += min(c['downloads'] / 1000, 100)
    if any(t in c['tags'] for t in ('tool-calling', 'function-calling')):
        score += 30
    if 'moe' in c['tags']:
        score += 20
    if 2 <= c['size_gb'] <= 20:
        score += 10
    c['score'] = round(score, 1)

candidates.sort(key=lambda c: c['score'], reverse=True)

# Output top candidates
output = candidates[:20]
print(json.dumps(output, indent=2))
" > "${CANDIDATES_FILE}" 2>/dev/null

    local n_candidates
    n_candidates=$(python3 -c "import json; print(len(json.load(open('${CANDIDATES_FILE}'))))" 2>/dev/null || echo "0")
    log_ok "Found ${n_candidates} candidate models"

    if [ "${n_candidates}" -gt 0 ]; then
        log_info "Top candidates:"
        python3 -c "
import json
candidates = json.load(open('${CANDIDATES_FILE}'))
for i, c in enumerate(candidates[:5]):
    print(f\"  {i+1}. {c['repo_id']} / {c['filename']} ({c['size_gb']}GB, score={c['score']}, downloads={c['downloads']})\")
" 2>/dev/null

        # Append to report
        echo "## Auto-Discovery Results" >> "${WEEKLY_REPORT}"
        echo "" >> "${WEEKLY_REPORT}"
        echo "Found ${n_candidates} new model candidates via HuggingFace API:" >> "${WEEKLY_REPORT}"
        echo "" >> "${WEEKLY_REPORT}"
        python3 -c "
import json
candidates = json.load(open('${CANDIDATES_FILE}'))
for c in candidates[:10]:
    tags = ', '.join(c['tags']) if c['tags'] else 'none'
    print(f\"- **{c['repo_id']}** / \`{c['filename']}\` — {c['size_gb']}GB, score={c['score']}, tags: {tags}\")
" >> "${WEEKLY_REPORT}" 2>/dev/null
        echo "" >> "${WEEKLY_REPORT}"
    fi
}

# =============================================================================
# PHASE 3: DOWNLOAD — Fetch top candidates
# =============================================================================

download_candidates() {
    log_header "Phase 3: Downloading New Models"

    if [ ! -f "${CANDIDATES_FILE}" ]; then
        log_warn "No candidates file found. Run discover first."
        return 0
    fi

    local n_candidates
    n_candidates=$(python3 -c "import json; print(len(json.load(open('${CANDIDATES_FILE}'))))" 2>/dev/null || echo "0")
    if [ "${n_candidates}" -eq 0 ]; then
        log_info "No new model candidates to download."
        return 0
    fi

    # Use venv Python for downloading (has huggingface_hub installed)
    VENV_PYTHON="/home/rohit/agentharness/venv/bin/python3"
    if ! "${VENV_PYTHON}" -c "from huggingface_hub import hf_hub_download" 2>/dev/null; then
        log_info "Installing huggingface_hub into venv..."
        /home/rohit/agentharness/venv/bin/pip install --quiet huggingface_hub || {
            log_error "Failed to install huggingface_hub"
            return 1
        }
    fi

    # Check available disk space (GB)
    local disk_avail_gb
    disk_avail_gb=$(df --output=avail "${MODEL_DIR}" 2>/dev/null | tail -1 | awk '{printf "%.0f", $1/1048576}')
    log_info "Available disk space: ~${disk_avail_gb}GB"

    # Download candidates using Python API (avoids system pip issues)
    local downloaded=0 failed=0
    local dl_output
    dl_output="$("${VENV_PYTHON}" << DLEOF
import json, os, sys
from huggingface_hub import hf_hub_download

candidates = json.load(open("${CANDIDATES_FILE}"))
model_dir = "${MODEL_DIR}"
max_new = ${MAX_NEW_MODELS}

downloaded = 0
failed = 0

for c in candidates:
    if downloaded >= max_new:
        break

    repo_id = c["repo_id"]
    filename = c["filename"]
    size_gb = c["size_gb"]

    # Check disk space
    st = os.statvfs(model_dir)
    disk_avail = (st.f_bavail * st.f_frsize) / (1024**3)
    if disk_avail < size_gb + 5:
        print(f"[WARN] Skipping {repo_id}/{filename} -- needs {size_gb+5:.0f}GB but only {disk_avail:.0f}GB available")
        continue

    dest = os.path.join(model_dir, filename)
    if os.path.exists(dest):
        print(f"[INFO] {filename} already exists. Skipping.")
        continue

    print(f"[INFO] Downloading {repo_id} / {filename} ({size_gb}GB)...")
    try:
        path = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=model_dir,
            local_dir_use_symlinks=False,
        )
        if path and os.path.exists(path) and os.path.abspath(path) != os.path.abspath(dest):
            os.rename(path, dest)
        if os.path.exists(dest):
            actual_gb = os.path.getsize(dest) / (1024**3)
            print(f"[OK] Downloaded: {filename} ({actual_gb:.1f}GB)")
            downloaded += 1
        else:
            print(f"[ERROR] File not found after download: {dest}")
            failed += 1
    except Exception as e:
        print(f"[ERROR] Failed to download {repo_id}/{filename}: {e}")
        failed += 1

print(f"Downloads complete: {downloaded} new, {failed} failed")
DLEOF
)"
    echo "${dl_output}"

    # Parse download counts from Python output
    if [[ "${dl_output}" =~ Downloads\ complete:\ ([0-9]+)\ new,\ ([0-9]+)\ failed ]]; then
        downloaded="${BASH_REMATCH[1]}"
        failed="${BASH_REMATCH[2]}"
    fi

    # Update model catalog
    if [ -f "${SCRIPT_DIR}/download_models.sh" ]; then
        # Rebuild catalog by scanning model dir
        log_info "Rebuilding model catalog..."
        python3 -c "
import json, os, glob

model_dir = '${MODEL_DIR}'
catalog = []

for gguf in sorted(glob.glob(os.path.join(model_dir, '*.gguf'))):
    name = os.path.basename(gguf)
    size_gb = round(os.path.getsize(gguf) / (1024**3), 1)
    catalog.append({
        'name': name,
        'path': gguf,
        'size_gb': size_gb,
        'format': 'gguf',
        'gguf_path': gguf,
        'type': 'chat',
    })

with open('${AH_DATA_DIR}/model_catalog.json', 'w') as f:
    json.dump(catalog, f, indent=2)

print(f'Catalog updated: {len(catalog)} models')
" 2>/dev/null
    fi

    echo "## Download Results" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"
    echo "- Downloaded: ${downloaded} new model(s)" >> "${WEEKLY_REPORT}"
    [ "${failed}" -gt 0 ] && echo "- Failed: ${failed}" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"

    log_ok "Downloads complete: ${downloaded} new, ${failed} failed"
}

# =============================================================================
# PHASE 4: BUILD — Update inference engines if new commits available
# =============================================================================

update_engines() {
    log_header "Phase 4: Checking Engine Updates"

    local engines_updated=0

    for dir in /opt/ik_llama /opt/llama.cpp; do
        if [ ! -d "${dir}/.git" ]; then
            log_info "${dir} not found, skipping"
            continue
        fi

        local name
        name=$(basename "${dir}")
        cd "${dir}"

        local current_hash
        current_hash=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

        # Fetch remote to check for updates
        log_info "Checking ${name} for updates (current: ${current_hash})..."
        git fetch origin 2>/dev/null || {
            log_warn "${name}: git fetch failed (network issue?)"
            continue
        }

        # Compare local vs remote
        local local_hash remote_hash
        local_hash=$(git rev-parse HEAD 2>/dev/null)
        remote_hash=$(git rev-parse origin/main 2>/dev/null || git rev-parse origin/master 2>/dev/null || echo "")

        if [ -z "${remote_hash}" ]; then
            log_warn "${name}: couldn't determine remote HEAD"
            continue
        fi

        if [ "${local_hash}" = "${remote_hash}" ]; then
            log_ok "${name}: already up to date (${current_hash})"
            continue
        fi

        local new_hash
        new_hash=$(echo "${remote_hash}" | cut -c1-7)
        local commits_behind
        commits_behind=$(git rev-list --count HEAD..origin/main 2>/dev/null || git rev-list --count HEAD..origin/master 2>/dev/null || echo "?")
        log_info "${name}: ${commits_behind} commits behind (${current_hash} → ${new_hash})"

        # Determine binary prefix
        local bin_prefix
        case "${name}" in
            ik_llama) bin_prefix="ik-llama" ;;
            llama.cpp) bin_prefix="llama" ;;
            *) bin_prefix="${name}" ;;
        esac

        # Backup existing binaries
        local backup_dir="/tmp/engine_backup_${name}_$(date +%s)"
        mkdir -p "${backup_dir}"
        for bin in server bench cli; do
            local bin_path="/usr/local/bin/${bin_prefix}-${bin}"
            [ -f "${bin_path}" ] && cp "${bin_path}" "${backup_dir}/" 2>/dev/null || true
        done
        log_info "Backed up existing binaries to ${backup_dir}"

        # Pull and build
        log_info "Pulling and building ${name}..."
        if git pull --ff-only 2>/dev/null; then
            # Detect CPU features for build flags
            local cmake_flags="-DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DLLAMA_CURL=ON"
            grep -q 'avx2' /proc/cpuinfo && cmake_flags+=" -DGGML_AVX2=ON"
            grep -q 'avx512' /proc/cpuinfo && cmake_flags+=" -DGGML_AVX512=ON"

            rm -rf build
            if cmake -B build ${cmake_flags} 2>/dev/null && \
               cmake --build build -j"$(nproc)" 2>/dev/null; then

                # Install new binaries
                sudo ln -sf "${dir}/build/bin/llama-server" "/usr/local/bin/${bin_prefix}-server"
                sudo ln -sf "${dir}/build/bin/llama-bench" "/usr/local/bin/${bin_prefix}-bench"
                sudo ln -sf "${dir}/build/bin/llama-cli" "/usr/local/bin/${bin_prefix}-cli"

                log_ok "${name}: built and installed (${new_hash})"
                engines_updated=$((engines_updated + 1))

                echo "- **${name}**: Updated ${current_hash} → ${new_hash} (${commits_behind} commits)" >> "${WEEKLY_REPORT}"
            else
                log_error "${name}: build failed, restoring backup"
                for bin in "${backup_dir}"/*; do
                    [ -f "${bin}" ] && sudo cp "${bin}" "/usr/local/bin/" 2>/dev/null || true
                done
                # Reset to previous state
                git reset --hard "${local_hash}" 2>/dev/null || true

                echo "- **${name}**: Build failed, reverted to ${current_hash}" >> "${WEEKLY_REPORT}"
            fi
        else
            log_warn "${name}: git pull failed (merge conflict?)"
            echo "- **${name}**: Pull failed, staying at ${current_hash}" >> "${WEEKLY_REPORT}"
        fi

        # Cleanup backup
        rm -rf "${backup_dir}"
    done

    if [ "${engines_updated}" -gt 0 ]; then
        echo "" >> "${WEEKLY_REPORT}"
        log_ok "${engines_updated} engine(s) updated"
    else
        log_info "No engine updates needed"
    fi
}

# =============================================================================
# PHASE 5: BENCHMARK — Run benchmarks and auto-switch
# =============================================================================

run_benchmarks() {
    log_header "Phase 5: Benchmarking All Configurations"

    if [ ! -f "${SCRIPT_DIR}/benchmark.sh" ]; then
        log_error "benchmark.sh not found at ${SCRIPT_DIR}/benchmark.sh"
        return 1
    fi

    local old_best="${BEST_MODEL:-unknown}"
    local old_score="${BEST_COMPOSITE:-0}"

    log_info "Running full benchmark suite..."
    log_info "Previous best: ${old_best} (score: ${old_score})"

    bash "${SCRIPT_DIR}/benchmark.sh" 2>&1 | tail -30

    # Reload best config
    [ -f "${AH_DATA_DIR}/best_config.env" ] && source "${AH_DATA_DIR}/best_config.env"

    local new_best="${BEST_MODEL:-unknown}"
    local new_score="${BEST_COMPOSITE:-0}"

    echo "## Benchmark Results" >> "${WEEKLY_REPORT}"
    echo "" >> "${WEEKLY_REPORT}"

    if [ "${old_best}" != "${new_best}" ]; then
        echo "**Switched model**: ${old_best} (${old_score}) → **${new_best}** (${new_score})" >> "${WEEKLY_REPORT}"
        log_ok "NEW BEST: ${new_best} (score: ${new_score}, was: ${old_best} at ${old_score})"
        notify "Weekly optimization found a better config: ${new_best} (score: ${new_score}, was: ${old_best} at ${old_score})"
    else
        echo "**No change**: ${new_best} remains best (score: ${new_score})" >> "${WEEKLY_REPORT}"
        log_info "No improvement found. Keeping: ${new_best} (${new_score})"
    fi

    echo "" >> "${WEEKLY_REPORT}"
}

# =============================================================================
# Notification helper
# =============================================================================

notify() {
    local message="$1"
    bash "${AH_SCRIPTS_DIR}/alert.sh" INFO "${message}" weekly_optimize 2>/dev/null || true
}

# =============================================================================
# Main
# =============================================================================

main() {
    log_header "Weekly Optimization Pipeline"

    if ${DRY_RUN}; then
        log_warn "DRY RUN — will discover but not download, build, or benchmark"
    fi

    ensure_dir "${REPORT_DIR}"

    # Initialize report
    cat > "${WEEKLY_REPORT}" << EOF
# AgentHarness Weekly Optimization Report
**Date**: $(date '+%Y-%m-%d %H:%M')
**Current Setup**: ${BEST_MODEL:-unknown} on ${BEST_ENGINE:-unknown} (score: ${BEST_COMPOSITE:-N/A}/10)
**Hardware**: ${CPU_MODEL:-unknown}, ${TOTAL_RAM_GB:-36}GB RAM
**Mode**: $(${DRY_RUN} && echo "Dry Run" || echo "Full Pipeline")

---

EOF

    # --- Phase 1: Report (web search + LLM analysis) ---
    if ! ${SKIP_REPORT}; then
        if curl -sf "${SEARXNG_URL}/healthz" &>/dev/null || \
           curl -sf "${SEARXNG_URL}/search?q=test&format=json" &>/dev/null; then
            search_new_models
            search_new_engines
            search_techniques
            check_engine_updates
            generate_action_items
        else
            log_warn "SearXNG not reachable. Skipping web search phase."
            echo "## Web Search" >> "${WEEKLY_REPORT}"
            echo "SearXNG unavailable — skipped." >> "${WEEKLY_REPORT}"
            echo "" >> "${WEEKLY_REPORT}"
        fi
    fi

    # --- Phase 2: Discover (HuggingFace API) ---
    discover_new_models

    if ${DRY_RUN}; then
        log_info "Dry run complete. Report: ${WEEKLY_REPORT}"
        cat >> "${WEEKLY_REPORT}" << EOF

---
*Dry run — no downloads, builds, or benchmarks performed.*
*Run without --dry-run to execute action items.*
EOF
        cat "${WEEKLY_REPORT}"
        return 0
    fi

    # --- Phase 3: Download ---
    download_candidates

    # --- Phase 4: Build engines ---
    update_engines

    # --- Phase 5: Benchmark + auto-switch ---
    run_benchmarks

    # Finalize report
    cat >> "${WEEKLY_REPORT}" << EOF

---
*Report generated by AgentHarness weekly_optimize.sh (full pipeline)*
*Next scan: $(date -d '+7 days' '+%Y-%m-%d' 2>/dev/null || date -v+7d '+%Y-%m-%d' 2>/dev/null || echo "next week")*
EOF

    log_ok "Report saved to: ${WEEKLY_REPORT}"
    notify "Weekly optimization complete. Report: ${WEEKLY_REPORT}"

    echo ""
    cat "${WEEKLY_REPORT}"
}

main "$@"
