#!/usr/bin/env python3
"""
Local LLM TPS Monitor — tracks real-time tokens/sec for the running llama-server.
Usage:
  python3 tps_monitor.py           # Interactive live monitor
  python3 tps_monitor.py --once    # Single-shot stats print
  python3 tps_monitor.py --json    # JSON output for agents
"""

import subprocess
import json
import time
import os
import sys
from datetime import datetime
from pathlib import Path

STATE_FILE = Path("/home/rohit/.hermes/tps_stats.json")
POLL_INTERVAL = 10  # seconds


def get_journal_timings(limit=5):
    """Extract TPS values from journalctl for Ollama.service or llama-bench.service."""
    timings = []
    for unit in ["Ollama.service", "llama-bench.service"]:
        try:
            result = subprocess.run(
                ["journalctl", "-u", unit, "--no-pager",
                 "-n", str(limit), "--since", "5 minutes ago"],
                capture_output=True, text=True, timeout=10
            )
            lines = result.stdout.strip().split("\n")
            for line in lines:
                if "tokens per second" in line:
                    try:
                        tps_str = line.split("tokens per second")[0].strip().split()[-1]
                        tps = float(tps_str)
                        if "prompt eval time" in line or "prompt processing" in line:
                            timings.append({"type": "prompt", "tps": tps})
                        elif "eval time" in line:
                            timings.append({"type": "generation", "tps": tps})
                    except (ValueError, IndexError):
                        continue
        except Exception:
            continue
    return timings


def get_server_info():
    """Get current model and server info."""
    try:
        result = subprocess.run(
            ["curl", "-s", "http://127.0.0.1:11434/v1/models"],
            capture_output=True, text=True, timeout=5
        )
        data = json.loads(result.stdout)
        if "data" in data and len(data["data"]) > 0:
            model_id = data["data"][0].get("id", "unknown")
            ctx = data["data"][0].get("meta", {}).get("n_ctx", "?")
            # Shorten model name
            name = model_id.split("/")[-1].replace(".gguf", "") if "/" in model_id else model_id
            return {"model": name, "ctx": ctx, "full_model": model_id}
    except Exception:
        pass
    return {"model": "unknown", "ctx": "?", "full_model": "unknown"}


def get_cpu_info():
    """Get current CPU frequencies and governor."""
    freqs = []
    for i in range(8):
        try:
            with open(f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq") as f:
                freqs.append(int(f.read().strip()) // 1000)
        except Exception:
            freqs.append(0)
    try:
        with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor") as f:
            governor = f.read().strip()
    except Exception:
        governor = "unknown"
    return freqs, governor


def get_process_info():
    """Get llama-server process stats."""
    try:
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.split("\n"):
            if "llama-server" in line and "grep" not in line:
                parts = line.split()
                if len(parts) >= 11:
                    return {
                        "pid": parts[1],
                        "cpu_pct": parts[2],
                        "mem_pct": parts[3],
                        "rss_mb": str(int(parts[4]) // 1024),
                        "vsz_mb": str(int(parts[5]) // 1024),
                    }
    except Exception:
        pass
    return {}


def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "current_model": "unknown",
        "session_start": None,
        "prompt_tps": {"count": 0, "sum": 0, "avg": 0, "peak": 0, "min": 999},
        "gen_tps": {"count": 0, "sum": 0, "avg": 0, "peak": 0, "min": 999},
        "history": [],
        "last_update": None,
        "benchmarks": {}
    }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def update_stats(timings, state):
    now = datetime.now().isoformat()
    for t in timings:
        key = "prompt_tps" if t["type"] == "prompt" else "gen_tps"
        s = state[key]
        s["count"] += 1
        s["sum"] += t["tps"]
        s["avg"] = round(s["sum"] / s["count"], 2)
        s["peak"] = max(s["peak"], round(t["tps"], 2))
        if t["tps"] > 0:
            s["min"] = min(s["min"], round(t["tps"], 2))
        state["history"].append({"time": now, "type": t["type"], "tps": round(t["tps"], 2)})
    # Keep last 360 entries (1 hour at 10s intervals)
    state["history"] = state["history"][-360:]
    state["last_update"] = now


def display(state, server_info, cpu_freqs, governor, proc_info):
    # Only clear if terminal
    if sys.stdout.isatty():
        os.system("clear")
    print("=" * 60)
    print("  🤖 Local LLM — Live TPS Monitor")
    print("=" * 60)
    print(f"  Model:   {server_info['model']}")
    print(f"  Context: {server_info['ctx']} tokens")
    if proc_info:
        print(f"  PID: {proc_info.get('pid','?')} | CPU: {proc_info.get('cpu_pct','?')}% | RAM: {proc_info.get('rss_mb','?')} MB")
    print(f"  Updated: {state['last_update'] or 'waiting...'}")
    print()
    print(f"  Governor: {governor}")
    print(f"  CPU: {' '.join(f'{f}M' for f in cpu_freqs)}")
    print()
    print("  ┌─────────────────────────────────────────────┐")
    print("  │  PROMPT EVAL                                │")
    p = state["prompt_tps"]
    print(f"  │  Avg: {p['avg']:6.2f} TPS  Peak: {p['peak']:6.2f} TPS       │")
    print(f"  │  Min: {p['min']:6.2f} TPS  Count: {p['count']:5d}           │")
    print("  ├─────────────────────────────────────────────┤")
    print("  │  GENERATION                                 │")
    g = state["gen_tps"]
    print(f"  │  Avg: {g['avg']:6.2f} TPS  Peak: {g['peak']:6.2f} TPS       │")
    print(f"  │  Min: {g['min']:6.2f} TPS  Count: {g['count']:5d}           │")
    print("  └─────────────────────────────────────────────┘")
    print()
    # Sparkline of recent generation TPS
    recent = [h for h in state["history"] if h["type"] == "generation"][-18:]
    if recent:
        max_tps = max(h["tps"] for h in recent) if recent else 1
        print("  Generation TPS (last 3 min):")
        for h in recent:
            bar_len = int((h["tps"] / max(max_tps, 0.01)) * 30)
            bar = "█" * bar_len + "░" * (30 - bar_len)
            print(f"    {h['tps']:5.2f} │{bar}│")
    print()
    # Show benchmark comparison if available
    if state.get("benchmarks"):
        print("  ┌─ Model Comparison (100-token gen) ─────────┐")
        for name, data in state["benchmarks"].items():
            tps = data.get("gen_tps", "?")
            mem = data.get("memory_mb", "?")
            print(f"  │  {name:15s} {tps:5s} TPS  {mem:>6s} MB          │")
        print("  └─────────────────────────────────────────────┘")
    print()
    print("  Ctrl+C to exit. Stats: ~/.hermes/tps_stats.json")


def main():
    if "--json" in sys.argv:
        state = load_state()
        timings = get_journal_timings(10)
        if timings:
            update_stats(timings, state)
        server_info = get_server_info()
        state["current_model"] = server_info["model"]
        save_state(state)
        p = state["prompt_tps"]
        g = state["gen_tps"]
        print(json.dumps({
            "model": server_info["model"],
            "prompt_tps_avg": p["avg"],
            "prompt_tps_peak": p["peak"],
            "gen_tps_avg": g["avg"],
            "gen_tps_peak": g["peak"],
            "last_update": state["last_update"]
        }))
        return

    if "--once" in sys.argv:
        state = load_state()
        timings = get_journal_timings(10)
        if timings:
            update_stats(timings, state)
        server_info = get_server_info()
        state["current_model"] = server_info["model"]
        save_state(state)
        p = state["prompt_tps"]
        g = state["gen_tps"]
        print(f"prompt_avg={p['avg']} prompt_peak={p['peak']} gen_avg={g['avg']} gen_peak={g['peak']}")
        return

    state = load_state()
    print("Starting TPS monitor... (polling every {}s)".format(POLL_INTERVAL))

    try:
        while True:
            timings = get_journal_timings(10)
            if timings:
                update_stats(timings, state)
            server_info = get_server_info()
            state["current_model"] = server_info["model"]
            cpu_freqs, governor = get_cpu_info()
            proc_info = get_process_info()
            save_state(state)
            display(state, server_info, cpu_freqs, governor, proc_info)
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\nMonitor stopped. Stats saved.")


if __name__ == "__main__":
    main()
