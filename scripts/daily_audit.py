import os
import sys
import subprocess
import json
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration of paths
# ---------------------------------------------------------------------------
BASE_DIR = "/home/rohit/agentharness"
DATA_DIR = os.path.join(BASE_DIR, "data")
INBOX_DIR = os.path.join(DATA_DIR, "inbox")
LOG_DIR = os.path.join(DATA_DIR, "logs")

def run_cmd(cmd):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        return res.stdout.strip()
    except:
        return ""

# 1. Disk & Bloat Audit
def audit_bloat():
    report = []
    # Check Docker reclaimable
    docker_df = run_cmd("sudo docker system df --format {{.Type}}: {{.Reclaimable}}")
    report.append(f"Docker Reclaimable:\n{docker_df}")
    return "\n".join(report)

# 2. Performance Audit (LLM Benchmark)
def benchmark_llm():
    start = time.time()
    # Simple prompt to local LLM via Proxy (8080)
    cmd = """curl -sf -m 30 http://localhost:8080/v1/chat/completions \
        -H "Content-Type: application/json" \
        -d "{\\"messages\\":[{\\"role\\":\\"user\\",\\"content\\":\\"Hello, status check. Respond with OK.\\"}], \\"max_tokens\\": 10}" """
    res = run_cmd(cmd)
    duration = time.time() - start
    
    if res:
        return f"Local LLM Proxy Benchmark: OK ({duration:.2f}s)"
    else:
        return "ALERT: Local LLM Proxy Benchmark FAILED or timed out."

# 3. Ollama Audit
def audit_ollama():
    res = run_cmd("ollama list")
    if "NAME" in res:
        return f"Ollama Engine: Healthy\nInventory:\n{res}"
    else:
        return "ALERT: Ollama Engine not responding!"

# 4. Security Audit
def audit_security():
    ports = run_cmd("netstat -tulpn 2>/dev/null | grep LISTEN | awk \"{print \\$4}\" | cut -d: -f2 | sort -nu | xargs")
    return f"Active Listening Ports: {ports}"

def run_audit():
    print(f"Starting Daily Audit: {datetime.now()}")
    
    sections = {
        "Bloat & Storage": audit_bloat(),
        "Ollama Intelligence": audit_ollama(),
        "LLM Performance": benchmark_llm(),
        "Security": audit_security()
    }
    
    full_report = "# 🌙 Nightly Homelab Audit Report\n"
    full_report += f"Date: {datetime.now().strftime(%Y-%m-%d %H:%M:%S)}\n\n"
    
    for title, content in sections.items():
        full_report += f"## {title}\n{content}\n\n"
    
    # Save to inbox
    audit_id = int(time.time())
    report_file = os.path.join(INBOX_DIR, f"audit_{audit_id}.json")
    
    payload = {
        "id": audit_id,
        "type": "audit_report",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "content": full_report,
        "_source": "daily_audit_cron"
    }
    
    os.makedirs(INBOX_DIR, exist_ok=True)
    with open(report_file, "w") as f:
        json.dump(payload, f, indent=2)
    
    print(f"Audit complete. Report saved to {report_file}")

if __name__ == "__main__":
    run_audit()
