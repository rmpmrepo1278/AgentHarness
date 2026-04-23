#!/usr/bin/env python3
"""evolution_loop.py — The self-evolution feedback cycle.

Runs daily via scheduler. Collects metrics, detects patterns, compiles
briefing, pushes feedback to agent inbox.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Ensure we can import core modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

DATA_DIR = os.environ.get('AH_DATA_DIR', os.path.expanduser('~/agentharness/data'))


def step_resource_snapshot():
    """Record a resource usage snapshot."""
    print('[1/5] Recording resource snapshot...')
    try:
        import psutil
        from core.observe.metrics_emitter import emit_resource
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        usb_mounted = os.path.ismount('/mnt/usb')
        emit_resource(DATA_DIR, cpu=psutil.cpu_percent(interval=1),
                      mem_used_mb=mem.used / 1048576, mem_total_mb=mem.total / 1048576,
                      disk_pct=disk.percent, usb_mounted=usb_mounted)
        usb_str = "mounted" if usb_mounted else "NOT MOUNTED"
        print(f'  CPU={psutil.cpu_percent():.0f}% MEM={mem.percent:.0f}% DISK={disk.percent:.0f}% USB={usb_str}')
    except Exception as e:
        print(f'  [WARN] Resource recording failed: {e}')


def step_synthesizer():
    """Detect patterns in metrics.jsonl and generate proposals."""
    print('[2/5] Running pattern detection (Synthesizer)...')
    try:
        from core.feedback.synthesizer import Synthesizer
        from core.resilience.atomic_json import atomic_write_json
        s = Synthesizer(DATA_DIR)
        proposals = s.propose()
        if proposals:
            proposals_dir = Path(DATA_DIR) / 'proposals'
            proposals_dir.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f'synth_{ts}.json'
            atomic_write_json(proposals_dir / fname, proposals)
            print(f'  {len(proposals)} proposal(s) saved to {fname}')
            for p in proposals:
                reason = p.get("reason", "?")[:80]
                print(f'    - {reason}')
        else:
            print('  No patterns detected (need more data)')
    except Exception as e:
        print(f'  [WARN] Synthesizer failed: {e}')


def step_distiller():
    """Compile daily briefing from all data sources."""
    print('[3/5] Compiling daily briefing (Distiller)...')
    try:
        from core.feedback.distiller import Distiller
        from core.resilience.atomic_json import atomic_write_json
        d = Distiller(DATA_DIR)
        briefing = d.compile()
        briefings_dir = Path(DATA_DIR) / 'briefings'
        briefings_dir.mkdir(exist_ok=True)
        ds = datetime.now().strftime("%Y%m%d")
        fname = f'briefing_{ds}.json'
        atomic_write_json(briefings_dir / fname, briefing)
        health = briefing.get('health', {})
        passed = health.get("checks_passed", 0)
        failed = health.get("checks_failed", 0)
        tools = briefing.get("tools", {})
        total_tools = tools.get("total_runs", 0)
        success_tools = tools.get("success", 0)
        proposals = briefing.get("proposals", {})
        n_proposals = len(proposals) if isinstance(proposals, list) else proposals.get("pending", 0) if isinstance(proposals, dict) else 0
        print(f'  Checks: {passed} passed, {failed} failed')
        print(f'  Tools: {total_tools} run, {success_tools} succeeded')
        print(f'  Proposals: {n_proposals} pending')
        print(f'  Saved: {fname}')
        return briefing
    except Exception as e:
        print(f'  [WARN] Distiller failed: {e}')
        return None


def step_bridge(briefing):
    """Push feedback to agent inbox."""
    print('[4/5] Pushing feedback to agent inbox (Bridge)...')
    if not briefing:
        print('  No briefing to push')
        return
    try:
        from core.feedback.bridge import FeedbackBridge
        bridge_dir = str(Path(DATA_DIR) / 'inbox')
        bridge = FeedbackBridge(data_dir=DATA_DIR, bridge_dir=bridge_dir)
        bridge.push_briefing(briefing)
        print('  Briefing pushed to inbox')
    except Exception as e:
        print(f'  [WARN] Bridge push failed: {e}')


def step_status():
    """Print pipeline status."""
    print('[5/5] Evolution cycle complete.')
    print()
    print('=== Pipeline Status ===')
    metrics_file = Path(DATA_DIR) / 'metrics.jsonl'
    if metrics_file.exists():
        lines = sum(1 for _ in open(metrics_file))
        print(f'  metrics.jsonl: {lines} events')
    else:
        print('  metrics.jsonl: not yet created')
    proposals_dir = Path(DATA_DIR) / 'proposals'
    n_proposals = len(list(proposals_dir.glob('*.json'))) if proposals_dir.exists() else 0
    print(f'  proposals: {n_proposals} file(s)')
    briefings_dir = Path(DATA_DIR) / 'briefings'
    n_briefings = len(list(briefings_dir.glob('*.json'))) if briefings_dir.exists() else 0
    print(f'  briefings: {n_briefings} file(s)')


def main():
    ts = datetime.now().isoformat()
    print(f'=== Evolution Loop: {ts} ===')
    step_resource_snapshot()
    step_synthesizer()
    briefing = step_distiller()
    step_bridge(briefing)
    step_status()


if __name__ == '__main__':
    main()
