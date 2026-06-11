#!/usr/bin/env python3
"""
cost_optimizer.py — Analyze LLM usage and optimize routing for minimum cost.

Reads the budget tracker data and proxy logs to:
1. Calculate cost-per-request per provider
2. Identify which providers give best quality/cost ratio
3. Suggest routing table adjustments
4. Generate a daily cost report

Usage:
    python3 cost_optimizer.py analyze   — full analysis
    python3 cost_optimizer.py report    — daily cost report
    python3 cost_optimizer.py suggest   — suggest routing changes
    python3 cost_optimizer.py stats     — quick stats
"""

import json
import os
import sys
from datetime import datetime, date
from pathlib import Path

AH_DATA_DIR = Path(os.environ.get("AH_DATA_DIR", "/home/rohit/agentharness/data"))
AH_LOGS_DIR = Path(os.environ.get("AH_LOGS_DIR", "/home/rohit/agentharness/logs"))

BUDGET_FILE = AH_DATA_DIR / "llm_budget.json"
PROXY_LOG = AH_LOGS_DIR / "proxy.log"
COST_REPORT_FILE = AH_DATA_DIR / "cost_report.json"

# Cost per 1M tokens (USD) — update as needed
# Free providers = $0.00
PROVIDER_COSTS = {
    "local": {"in": 0.00, "out": 0.00, "label": "Local (Ollama) — FREE"},
    "owl": {"in": 0.00, "out": 0.00, "label": "Owl Alpha — FREE"},
    "google-alt": {"in": 0.00, "out": 0.00, "label": "Google Alt — FREE"},
    "groq": {"in": 0.00, "out": 0.00, "label": "Groq — FREE (rate-limited)"},
    "cerebras": {"in": 0.00, "out": 0.00, "label": "Cerebras — FREE (rate-limited)"},
    "sambanova": {"in": 0.00, "out": 0.00, "label": "SambaNova — FREE (rate-limited)"},
    "fireworks": {"in": 0.00, "out": 0.00, "label": "Fireworks — FREE"},
    "openrouter": {"in": 0.00, "out": 0.00, "label": "OpenRouter — FREE"},
    "laguna": {"in": 0.00, "out": 0.00, "label": "Laguna — FREE"},
    "anthropic": {"in": 3.00, "out": 15.00, "label": "Anthropic — PAID"},
    "google-primary": {"in": 1.25, "out": 10.00, "label": "Google Primary — PAID"},
}


def load_budget():
    """Load budget data from JSON file."""
    if BUDGET_FILE.exists():
        with open(BUDGET_FILE) as f:
            return json.load(f)
    return {"date": date.today().isoformat(), "providers": {}}


def calculate_costs(budget_data):
    """Calculate estimated costs per provider."""
    results = {}
    for name, stats in budget_data.get("providers", {}).items():
        costs = PROVIDER_COSTS.get(name, {"in": 0.0, "out": 0.0, "label": name})
        tokens_in = stats.get("tokens_in", 0)
        tokens_out = stats.get("tokens_out", 0)
        requests = stats.get("requests", 0)
        errors = stats.get("errors", 0)

        cost_in = (tokens_in / 1_000_000) * costs["in"]
        cost_out = (tokens_out / 1_000_000) * costs["out"]
        total_cost = cost_in + cost_out

        results[name] = {
            "label": costs["label"],
            "requests": requests,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "errors": errors,
            "cost_in": round(cost_in, 4),
            "cost_out": round(cost_out, 4),
            "total_cost": round(total_cost, 4),
            "cost_per_request": round(total_cost / max(requests, 1), 6),
            "error_rate": round(errors / max(requests, 1) * 100, 1),
        }
    return results


def analyze_proxy_logs():
    """Parse proxy logs for latency and success rate data."""
    stats = {}
    if not PROXY_LOG.exists():
        return stats

    # Read last 1000 lines
    try:
        with open(PROXY_LOG) as f:
            lines = f.readlines()[-1000:]
    except Exception:
        return stats

    for line in lines:
        # Look for provider routing info
        for provider in PROVIDER_COSTS:
            if provider in line.lower() and ("success" in line.lower() or "complete" in line.lower()):
                if provider not in stats:
                    stats[provider] = {"successes": 0, "failures": 0}
                stats[provider]["successes"] += 1
            elif provider in line.lower() and ("error" in line.lower() or "429" in line.lower()):
                if provider not in stats:
                    stats[provider] = {"successes": 0, "failures": 0}
                stats[provider]["failures"] += 1

    return stats


def generate_report():
    """Generate a comprehensive cost report."""
    budget = load_budget()
    costs = calculate_costs(budget)
    log_stats = analyze_proxy_logs()

    total_cost = sum(c["total_cost"] for c in costs.values())
    total_requests = sum(c["requests"] for c in costs.values())
    total_tokens = sum(c["tokens_in"] + c["tokens_out"] for c in costs.values())

    # Merge log stats
    for name, ls in log_stats.items():
        if name in costs:
            total_ls = ls["successes"] + ls["failures"]
            if total_ls > 0:
                costs[name]["log_success_rate"] = round(ls["successes"] / total_ls * 100, 1)

    report = {
        "date": budget.get("date", date.today().isoformat()),
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_cost_today": round(total_cost, 4),
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "avg_cost_per_request": round(total_cost / max(total_requests, 1), 6),
            "free_providers_used": sum(1 for c in costs.values() if c["total_cost"] == 0),
            "paid_providers_used": sum(1 for c in costs.values() if c["total_cost"] > 0),
        },
        "providers": costs,
        "routing_suggestions": generate_suggestions(costs),
    }

    with open(COST_REPORT_FILE, "w") as f:
        json.dump(report, f, indent=2)

    return report


def generate_suggestions(costs):
    """Generate routing optimization suggestions."""
    suggestions = []

    # Find providers with high error rates
    for name, data in costs.items():
        if data["error_rate"] > 20:
            suggestions.append({
                "type": "deprioritize",
                "provider": name,
                "reason": f"High error rate: {data['error_rate']}%",
                "action": f"Move {name} lower in routing priority"
            })

    # Find expensive providers
    paid = {n: d for n, d in costs.items() if d["total_cost"] > 0}
    if paid:
        suggestions.append({
            "type": "cost_alert",
            "providers": list(paid.keys()),
            "total_cost": sum(d["total_cost"] for d in paid.values()),
            "reason": "Paid providers are being used",
            "action": "Consider routing more tasks to free providers"
        })

    # Find most efficient free provider
    free = {n: d for n, d in costs.items() if d["total_cost"] == 0 and d["requests"] > 0}
    if free:
        best = min(free.items(), key=lambda x: x[1]["error_rate"])
        suggestions.append({
            "type": "promote",
            "provider": best[0],
            "reason": f"Lowest error rate among free providers: {best[1]['error_rate']}%",
            "action": f"Consider prioritizing {best[0]} for low-complexity tasks"
        })

    return suggestions


def print_report(report):
    """Print a human-readable report."""
    s = report["summary"]
    print(f"\n{'='*60}")
    print(f"  LLM Cost Report — {report['date']}")
    print(f"{'='*60}")
    print(f"  Total Cost:     ${s['total_cost_today']:.4f}")
    print(f"  Total Requests: {s['total_requests']}")
    print(f"  Total Tokens:   {s['total_tokens']:,}")
    print(f"  Avg Cost/Req:   ${s['avg_cost_per_request']:.6f}")
    print(f"  Free Providers: {s['free_providers_used']} | Paid: {s['paid_providers_used']}")
    print(f"{'='*60}")

    if report["providers"]:
        print(f"\n  {'Provider':<18} {'Reqs':>6} {'Tokens':>10} {'Cost':>8} {'Err%':>6}")
        print(f"  {'-'*54}")
        for name, data in sorted(report["providers"].items(),
                                  key=lambda x: x[1]["total_cost"], reverse=True):
            print(f"  {name:<18} {data['requests']:>6} "
                  f"{data['tokens_in']+data['tokens_out']:>10,} "
                  f"${data['total_cost']:>7.4f} {data['error_rate']:>5.1f}%")

    if report["routing_suggestions"]:
        print(f"\n  📋 Suggestions:")
        for sug in report["routing_suggestions"]:
            print(f"    [{sug['type'].upper()}] {sug['reason']}")
            print(f"      → {sug['action']}")

    print()


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "report"

    if action == "report":
        report = generate_report()
        print_report(report)

    elif action == "analyze":
        report = generate_report()
        print_report(report)
        # Also print log analysis
        log_stats = analyze_proxy_logs()
        if log_stats:
            print("  Log Analysis (last 1000 lines):")
            for name, ls in sorted(log_stats.items()):
                total = ls["successes"] + ls["failures"]
                if total > 0:
                    rate = round(ls["successes"] / total * 100, 1)
                    print(f"    {name}: {rate}% success ({ls['successes']}/{total})")

    elif action == "suggest":
        budget = load_budget()
        costs = calculate_costs(budget)
        suggestions = generate_suggestions(costs)
        print(json.dumps(suggestions, indent=2))

    elif action == "stats":
        budget = load_budget()
        costs = calculate_costs(budget)
        total_cost = sum(c["total_cost"] for c in costs.values())
        total_reqs = sum(c["requests"] for c in costs.values())
        print(f"Cost: ${total_cost:.4f} | Requests: {total_reqs} | "
              f"Providers: {len(costs)}")

    else:
        print(f"Unknown action: {action}")
        print("Usage: cost_optimizer.py {report|analyze|suggest|stats}")
        sys.exit(1)


if __name__ == "__main__":
    main()
