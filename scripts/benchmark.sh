#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# benchmark.sh — Benchmark all model x engine combinations, generate comparison
#                chart, and auto-switch to the best configuration
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

BENCHMARK_RESULTS="${AH_DATA_DIR}/benchmark_results.json"
BEST_CONFIG="${AH_DATA_DIR}/best_config.env"

# Test prompts for different capabilities
TOOL_CALL_PROMPT='You have access to a function called check_container(name: str) -> str. The user asks: "Is jellyfin running?" Call the appropriate function.'
REASONING_PROMPT='A Docker container is restarting every 30 seconds. The logs show "Error: ENOSPC". What is the most likely cause and how do you fix it? Be concise.'
JSON_PROMPT='Output a JSON object with keys: "service", "status", "action" for restarting the pihole container. Output only valid JSON, no explanation.'

# -----------------------------------------------------------------------------
# Detect existing benchmarking tools
# -----------------------------------------------------------------------------
detect_benchmark_tools() {
    log_info "Scanning for existing benchmarking tools..."

    FOUND_TOOLS=()

    # Check for llama-bench (built-in)
    for prefix in ik-llama llama; do
        if command -v "${prefix}-bench" &>/dev/null; then
            FOUND_TOOLS+=("${prefix}-bench")
            log_ok "Found: ${prefix}-bench"
        fi
    done

    # Check for custom benchmark scripts in common locations
    for path in \
        "${AH_DATA_DIR}"/benchmark_*.py \
        "${AH_DATA_DIR}"/bench_*.sh \
        ~/benchmark*.py \
        ~/bench*.sh \
        /opt/llm-bench/*.py \
        /opt/llm-benchmark/*.py; do
        for f in ${path}; do
            if [ -f "$f" ]; then
                FOUND_TOOLS+=("$f")
                log_ok "Found existing tool: $f"
            fi
        done
    done

    # Check for Python benchmarking packages
    if python3 -c "import llm_benchmark" 2>/dev/null; then
        FOUND_TOOLS+=("python:llm_benchmark")
        log_ok "Found: Python llm_benchmark package"
    fi

    if [ ${#FOUND_TOOLS[@]} -eq 0 ]; then
        log_warn "No existing benchmark tools found. Using built-in benchmarks."
    else
        log_info "Found ${#FOUND_TOOLS[@]} benchmark tool(s)"
    fi
}

# -----------------------------------------------------------------------------
# Run llama-bench for raw throughput numbers
# -----------------------------------------------------------------------------
run_throughput_bench() {
    local engine_bin="$1"   # e.g., "ik-llama-bench" or "llama-bench"
    local model_path="$2"
    local model_name="$3"
    local engine_name="$4"

    log_info "Benchmarking throughput: ${model_name} on ${engine_name}..."

    local output
    output=$("${engine_bin}" \
        -m "${model_path}" \
        -t "${CPU_CORES:-8}" \
        -p 512 -n 128 \
        -r 3 \
        2>&1) || {
        log_warn "Throughput benchmark failed for ${model_name} on ${engine_name}"
        echo "FAILED"
        return
    }

    # Parse llama-bench output — extract pp (prompt processing) and tg (token generation) speeds
    local pp_speed tg_speed
    pp_speed=$(echo "${output}" | grep -oP 'pp512[^\d]*\K[\d.]+' | tail -1 || echo "0")
    tg_speed=$(echo "${output}" | grep -oP 'tg128[^\d]*\K[\d.]+' | tail -1 || echo "0")

    # Fallback: try alternate parsing
    if [ "${pp_speed}" = "0" ] || [ "${tg_speed}" = "0" ]; then
        pp_speed=$(echo "${output}" | awk '/pp/ {for(i=1;i<=NF;i++) if($i ~ /^[0-9]+\.[0-9]+$/) {print $i; exit}}' || echo "0")
        tg_speed=$(echo "${output}" | awk '/tg/ {for(i=1;i<=NF;i++) if($i ~ /^[0-9]+\.[0-9]+$/) {print $i; exit}}' || echo "0")
    fi

    echo "${pp_speed}|${tg_speed}"
}

# -----------------------------------------------------------------------------
# Run quality benchmark via llama-server API
# -----------------------------------------------------------------------------
run_quality_bench() {
    local server_url="$1"
    local model_name="$2"

    log_info "Benchmarking quality: ${model_name}..."

    local scores=()

    # Test 1: Tool calling
    local tool_response
    tool_response=$(curl -sf --max-time 300 "${server_url}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$(cat <<JSONEOF
{
    "messages": [{"role": "user", "content": "${TOOL_CALL_PROMPT}"}],
    "max_tokens": 200,
    "temperature": 0.1
}
JSONEOF
)" 2>/dev/null) || tool_response=""

    local tool_score=0
    if echo "${tool_response}" | grep -qi "check_container\|jellyfin"; then
        tool_score=1
    fi
    scores+=("tool:${tool_score}")

    # Test 2: Reasoning
    local reason_response
    reason_response=$(curl -sf --max-time 300 "${server_url}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$(cat <<JSONEOF
{
    "messages": [{"role": "user", "content": "${REASONING_PROMPT}"}],
    "max_tokens": 300,
    "temperature": 0.1
}
JSONEOF
)" 2>/dev/null) || reason_response=""

    local reason_score=0
    if echo "${reason_response}" | grep -qi "disk\|space\|full\|storage\|no space"; then
        reason_score=1
    fi
    scores+=("reason:${reason_score}")

    # Test 3: JSON output
    local json_response
    json_response=$(curl -sf --max-time 300 "${server_url}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$(cat <<JSONEOF
{
    "messages": [{"role": "user", "content": "${JSON_PROMPT}"}],
    "max_tokens": 200,
    "temperature": 0.1
}
JSONEOF
)" 2>/dev/null) || json_response=""

    local json_score=0
    # Extract content and check if it's valid JSON
    local content
    content=$(echo "${json_response}" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    c = d['choices'][0]['message']['content']
    # Try to parse the content as JSON
    # Strip markdown code fences if present
    c = c.strip()
    if c.startswith('\`\`\`'):
        c = c.split('\n', 1)[1].rsplit('\`\`\`', 1)[0].strip()
    parsed = json.loads(c)
    if 'service' in parsed and 'status' in parsed:
        print('VALID')
    else:
        print('PARTIAL')
except:
    print('INVALID')
" 2>/dev/null || echo "INVALID")

    if [ "${content}" = "VALID" ]; then
        json_score=2
    elif [ "${content}" = "PARTIAL" ]; then
        json_score=1
    fi
    scores+=("json:${json_score}")

    # Calculate total (out of 4)
    local total=$((tool_score + reason_score + json_score))
    echo "${total}|${scores[*]}"
}

# -----------------------------------------------------------------------------
# Measure time-to-first-token and tokens-per-second via API
# -----------------------------------------------------------------------------
measure_interactive_speed() {
    local server_url="$1"
    local prompt="List 3 Docker best practices. Be brief."

    log_info "Measuring interactive response speed..."

    local start_ms
    start_ms=$(date +%s%N)

    local response
    response=$(curl -sf --max-time 120 "${server_url}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "{\"messages\":[{\"role\":\"user\",\"content\":\"${prompt}\"}],\"max_tokens\":150,\"temperature\":0.1}" \
        2>/dev/null) || { echo "0|0"; return; }

    local end_ms
    end_ms=$(date +%s%N)

    local elapsed_ms=$(( (end_ms - start_ms) / 1000000 ))

    # Extract token count from usage
    local total_tokens
    total_tokens=$(echo "${response}" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('usage', {}).get('completion_tokens', 0))
except:
    print(0)
" 2>/dev/null || echo "0")

    local tps=0
    if [ "${total_tokens}" -gt 0 ] && [ "${elapsed_ms}" -gt 0 ]; then
        tps=$(echo "scale=1; ${total_tokens} * 1000 / ${elapsed_ms}" | bc 2>/dev/null || echo "0")
    fi

    echo "${elapsed_ms}|${tps}|${total_tokens}"
}

# -----------------------------------------------------------------------------
# Start a temporary llama-server for testing
# -----------------------------------------------------------------------------
start_temp_server() {
    local engine_bin="$1"
    local model_path="$2"
    local port="${3:-8090}"

    # Kill any existing temp server on this port
    fuser -k "${port}/tcp" 2>/dev/null || true
    sleep 1

    "${engine_bin}" \
        --model "${model_path}" \
        --threads "${CPU_CORES:-8}" \
        --numa distribute \
        --mlock \
        --ctx-size 4096 \
        --host 127.0.0.1 \
        --port "${port}" \
        &>/dev/null &

    local server_pid=$!
    echo "${server_pid}"

    # Wait for server to be ready
    local tries=0
    while ! curl -sf "http://127.0.0.1:${port}/health" &>/dev/null; do
        sleep 2
        ((tries++))
        if [ "${tries}" -gt 120 ]; then
            log_error "Server failed to start within 4 minutes"
            kill "${server_pid}" 2>/dev/null || true
            return 1
        fi
    done
    log_ok "Server ready on port ${port} (PID ${server_pid})"
}

# -----------------------------------------------------------------------------
# Run all benchmarks for all model x engine combinations
# -----------------------------------------------------------------------------
run_all_benchmarks() {
    log_header "Running Full Benchmark Suite"

    # Load model catalog
    if [ ! -f "${AH_DATA_DIR}/model_catalog.json" ]; then
        log_error "Model catalog not found. Run download_models.sh first."
        return 1
    fi

    # Load hardware profile
    if [ -f "${AH_DATA_DIR}/hw_profile.env" ]; then
        source "${AH_DATA_DIR}/hw_profile.env"
    else
        CPU_CORES=$(nproc)
    fi

    # Get list of models and engines
    local models
    models=$(python3 -c "
import json
catalog = json.load(open('${AH_DATA_DIR}/model_catalog.json'))
for m in catalog:
    if 'draft' not in m['name']:
        print(f\"{m['name']}|{m['gguf_path']}|{m['type']}\")
" 2>/dev/null)

    local engines=()
    if command -v ik-llama-bench &>/dev/null; then
        engines+=("ik-llama|ik-llama-bench|ik-llama-server")
    fi
    if command -v llama-bench &>/dev/null; then
        engines+=("stock|llama-bench|llama-server")
    fi

    if [ ${#engines[@]} -eq 0 ]; then
        log_error "No inference engines found. Run build_inference.sh first."
        return 1
    fi

    # Results array
    local results_json="["
    local first_result=true
    local test_port=8090

    while IFS='|' read -r model_name model_path model_type; do
        for engine_entry in "${engines[@]}"; do
            IFS='|' read -r engine_name bench_bin server_bin <<< "${engine_entry}"

            log_header "Testing: ${model_name} on ${engine_name}"

            # 1. Throughput benchmark (doesn't need server)
            local throughput
            throughput=$(run_throughput_bench "${bench_bin}" "${model_path}" "${model_name}" "${engine_name}")
            local pp_speed tg_speed
            IFS='|' read -r pp_speed tg_speed <<< "${throughput}"

            # 2. Start temp server for quality + interactive tests
            local quality_total="0" quality_detail="skipped" interactive_ms="0" interactive_tps="0"

            if [ "${throughput}" != "FAILED" ]; then
                local server_pid
                server_pid=$(start_temp_server "${server_bin}" "${model_path}" "${test_port}") || {
                    log_warn "Skipping quality tests — server failed to start"
                    continue
                }

                # Quality benchmark
                local quality_result
                quality_result=$(run_quality_bench "http://127.0.0.1:${test_port}" "${model_name}")
                IFS='|' read -r quality_total quality_detail <<< "${quality_result}"

                # Interactive speed
                local interactive_result
                interactive_result=$(measure_interactive_speed "http://127.0.0.1:${test_port}")
                IFS='|' read -r interactive_ms interactive_tps _ <<< "${interactive_result}"

                # Kill temp server
                kill "${server_pid}" 2>/dev/null || true
                wait "${server_pid}" 2>/dev/null || true
                sleep 2
            fi

            # Calculate composite score (weighted)
            # Speed: 40%, Quality: 40%, Interactive: 20%
            local composite
            composite=$(python3 -c "
tg = float('${tg_speed}') if '${tg_speed}' != 'FAILED' else 0
quality = int('${quality_total}')
interactive_tps = float('${interactive_tps}')

# Normalize: tg_speed out of 20 tok/s max, quality out of 4, interactive out of 15 tok/s
speed_norm = min(tg / 20.0, 1.0) * 10
quality_norm = (quality / 4.0) * 10
interactive_norm = min(interactive_tps / 15.0, 1.0) * 10

composite = speed_norm * 0.4 + quality_norm * 0.4 + interactive_norm * 0.2
print(f'{composite:.2f}')
" 2>/dev/null || echo "0")

            # Append to results
            if [ "${first_result}" = true ]; then
                first_result=false
            else
                results_json+=","
            fi

            results_json+=$(cat <<ENTRY

  {
    "model": "${model_name}",
    "model_type": "${model_type}",
    "engine": "${engine_name}",
    "pp_tok_s": ${pp_speed:-0},
    "tg_tok_s": ${tg_speed:-0},
    "quality_score": ${quality_total},
    "quality_detail": "${quality_detail}",
    "interactive_ms": ${interactive_ms},
    "interactive_tps": ${interactive_tps},
    "composite_score": ${composite},
    "tested_at": "$(date -Iseconds)"
  }
ENTRY
)

            log_info "Result: tg=${tg_speed} tok/s, quality=${quality_total}/4, interactive=${interactive_tps} tok/s, composite=${composite}/10"
        done
    done <<< "${models}"

    results_json+=$'\n]'
    echo "${results_json}" > "${BENCHMARK_RESULTS}"
    log_ok "Results saved to ${BENCHMARK_RESULTS}"
}

# -----------------------------------------------------------------------------
# Generate comparison chart (ASCII + HTML)
# -----------------------------------------------------------------------------
generate_comparison_chart() {
    log_header "Benchmark Comparison"

    if [ ! -f "${BENCHMARK_RESULTS}" ]; then
        log_error "No benchmark results found. Run benchmarks first."
        return 1
    fi

    # ASCII chart
    python3 << 'PYSCRIPT'
import json
from datetime import datetime

results = json.load(open(os.environ.get("AH_DATA_DIR", "/opt/agentharness") + "/benchmark_results.json"))
results.sort(key=lambda x: x["composite_score"], reverse=True)

# ASCII table
print("")
header = f"{'RANK':<5} {'MODEL':<28} {'ENGINE':<10} {'TG tok/s':<10} {'QUALITY':<10} {'INT tok/s':<10} {'COMPOSITE':<10}"
print(header)
print("=" * len(header))

for i, r in enumerate(results):
    marker = " <-- BEST" if i == 0 else ""
    print(f"{i+1:<5} {r['model']:<28} {r['engine']:<10} {r['tg_tok_s']:<10.1f} {r['quality_score']:<10}/4 {r['interactive_tps']:<10.1f} {r['composite_score']:<10.2f}{marker}")

print("")
print(f"Tested: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print(f"Scoring: 40% speed + 40% quality + 20% interactive responsiveness")

# Save best config
best = results[0]
with open(os.environ.get("AH_DATA_DIR", "/opt/agentharness") + "/best_config.env", "w") as f:
    f.write(f'BEST_MODEL="{best["model"]}"\n')
    f.write(f'BEST_ENGINE="{best["engine"]}"\n')
    f.write(f'BEST_COMPOSITE={best["composite_score"]}\n')
    f.write(f'BEST_TG_SPEED={best["tg_tok_s"]}\n')
    f.write(f'BENCHMARK_DATE="{datetime.now().isoformat()}"\n')

print(f"\nBest config: {best['model']} on {best['engine']} (score: {best['composite_score']:.2f}/10)")
print(f"Saved to {os.environ.get('AH_DATA_DIR', '/opt/agentharness')}/best_config.env")
PYSCRIPT

    # HTML report
    python3 << 'PYSCRIPT'
import json
from datetime import datetime

results = json.load(open(os.environ.get("AH_DATA_DIR", "/opt/agentharness") + "/benchmark_results.json"))
results.sort(key=lambda x: x["composite_score"], reverse=True)

html = f"""<!DOCTYPE html>
<html><head><title>AgentHarness Benchmark Report</title>
<style>
  body {{ font-family: monospace; background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
  h1 {{ color: #0f3460; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th {{ background: #16213e; color: #e94560; padding: 8px; text-align: left; }}
  td {{ padding: 8px; border-bottom: 1px solid #333; }}
  tr:first-child td {{ background: #0f3460; font-weight: bold; }}
  .bar {{ background: #e94560; height: 16px; border-radius: 3px; }}
  .score {{ display: inline-block; min-width: 40px; }}
</style></head><body>
<h1>AgentHarness Benchmark Report</h1>
<p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<table>
<tr><th>Rank</th><th>Model</th><th>Engine</th><th>TG tok/s</th><th>Quality</th><th>Interactive</th><th>Composite</th><th>Visual</th></tr>
"""

for i, r in enumerate(results):
    bar_width = int(r["composite_score"] * 10)
    best_marker = " (BEST)" if i == 0 else ""
    html += f"""<tr>
<td>{i+1}</td><td>{r['model']}{best_marker}</td><td>{r['engine']}</td>
<td>{r['tg_tok_s']:.1f}</td><td>{r['quality_score']}/4</td><td>{r['interactive_tps']:.1f}</td>
<td><span class="score">{r['composite_score']:.2f}</span>/10</td>
<td><div class="bar" style="width:{bar_width}%"></div></td>
</tr>\n"""

html += "</table></body></html>"

report_path = os.environ.get("AH_REPORTS_DIR", "/opt/agentharness/reports") + "/benchmark_" + datetime.now().strftime('%Y%m%d_%H%M') + ".html"
with open(report_path, "w") as f:
    f.write(html)
print(f"HTML report: {report_path}")
PYSCRIPT
}

# -----------------------------------------------------------------------------
# Auto-switch to the best model + engine combination
# -----------------------------------------------------------------------------
auto_switch() {
    log_header "Auto-Switching to Best Configuration"

    if [ ! -f "${BEST_CONFIG}" ]; then
        log_error "No best config found. Run benchmarks first."
        return 1
    fi

    source "${BEST_CONFIG}"

    log_info "Best combination: ${BEST_MODEL} on ${BEST_ENGINE} (score: ${BEST_COMPOSITE}/10)"

    # Determine the correct server binary and model path
    local server_bin model_path
    if [ "${BEST_ENGINE}" = "ik-llama" ]; then
        server_bin="ik-llama-server"
    else
        server_bin="llama-server"
    fi

    model_path=$(python3 -c "
import json
catalog = json.load(open('${AH_DATA_DIR}/model_catalog.json'))
for m in catalog:
    if m['name'] == '${BEST_MODEL}':
        print(m['gguf_path'])
        break
" 2>/dev/null)

    if [ -z "${model_path}" ]; then
        log_error "Could not find model path for ${BEST_MODEL}"
        return 1
    fi

    log_info "Server binary: ${server_bin}"
    log_info "Model path: ${model_path}"

    # Update systemd service
    local service_file="/etc/systemd/system/llama-primary.service"
    local template="${SCRIPT_DIR}/../config/systemd/llama-primary.service"

    if [ -f "${template}" ]; then
        local threads="${CPU_CORES:-8}"
        sudo cp "${template}" "${service_file}"
        sudo sed -i "s|__MODEL_PATH__|${model_path}|g" "${service_file}"
        sudo sed -i "s|__THREADS__|${threads}|g" "${service_file}"

        # Switch server binary if stock llama.cpp won
        if [ "${BEST_ENGINE}" = "stock" ]; then
            sudo sed -i "s|/usr/local/bin/ik-llama-server|/usr/local/bin/llama-server|g" "${service_file}"
        fi

        sudo systemctl daemon-reload

        # Restart if currently running
        if systemctl is-active llama-primary &>/dev/null; then
            log_info "Restarting llama-primary with new configuration..."
            sudo systemctl restart llama-primary
            sleep 10

            if curl -sf http://localhost:8080/health &>/dev/null; then
                log_ok "Service restarted successfully with best config"
            else
                log_error "Service failed to start with new config. Check: sudo journalctl -u llama-primary"
            fi
        else
            log_info "Service not running. Start with: sudo systemctl start llama-primary"
        fi
    else
        log_warn "Service template not found at ${template}. Manual update needed."
    fi
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
main() {
    log_header "AgentHarness Benchmark Suite"

    ensure_dir "${REPORT_DIR}"

    detect_benchmark_tools
    run_all_benchmarks
    generate_comparison_chart
    auto_switch

    log_header "Benchmark Complete"
    log_info "Results: ${BENCHMARK_RESULTS}"
    log_info "Best config: ${BEST_CONFIG}"
    log_info "Reports: ${REPORT_DIR}/"
}

main "$@"
