"""Metrics emitter — writes structured events to metrics.jsonl.

This is the missing link that feeds the self-evolution pipeline:
  metrics.jsonl → synthesizer → proposals → doctor → feedback loop

Every component that produces operational data should call emit() here.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

_METRICS_FILE = "metrics.jsonl"


def emit(data_dir: str | Path, event_type: str, **kwargs) -> None:
    """Append a structured metric event to metrics.jsonl.

    Args:
        data_dir: Path to the agentharness data directory.
        event_type: One of 'check', 'harness', 'runbook', 'resource', 'tool_call'.
        **kwargs: Event-specific fields.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / _METRICS_FILE

    entry = {
        "type": event_type,
        "timestamp": time.time(),
        "iso": datetime.now(UTC).isoformat(),
        **kwargs,
    }

    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("Failed to emit metric: %s", exc)


def emit_check(data_dir: str | Path, name: str, status: str, value=None,
               severity: str = "", message: str = "") -> None:
    """Emit a health check result."""
    emit(data_dir, "check", check=name, status=status, value=value,
         severity=severity, message=message)


def emit_harness(data_dir: str | Path, name: str, success: bool,
                 exit_code: int = 0, duration: float = 0) -> None:
    """Emit a harness execution result."""
    emit(data_dir, "harness", harness=name, success=success,
         exit_code=exit_code, duration=duration)




