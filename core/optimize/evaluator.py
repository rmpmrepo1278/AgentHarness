"""Score finding applicability against current and future hardware."""
from __future__ import annotations
from typing import Any, Optional

NPU_KEYWORDS = ["npu", "xdna", "neural processing", "ai accelerator"]
GPU_KEYWORDS = ["gpu", "cuda", "rocm", "vulkan", "opencl", "radeon"]


def evaluate_finding(
    finding: dict[str, Any],
    hardware: dict[str, Any],
    planned_hardware: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Evaluate whether a finding is applicable to current/future hardware."""
    body = (finding.get("body", "") + " " + finding.get("name", "")).lower()
    tags = [t.lower() for t in finding.get("tags", [])]
    all_text = body + " " + " ".join(tags)

    needs_npu = any(kw in all_text for kw in NPU_KEYWORDS)
    needs_gpu = any(kw in all_text for kw in GPU_KEYWORDS)

    applicable_now = True
    if needs_npu and not hardware.get("has_npu", False):
        applicable_now = False
    if needs_gpu and not hardware.get("has_amd_gpu", False) and not hardware.get("has_nvidia", False):
        applicable_now = False

    applicable_future = applicable_now
    if not applicable_now and planned_hardware:
        if needs_npu and planned_hardware.get("has_npu", False):
            applicable_future = True
        if needs_gpu and (planned_hardware.get("has_amd_gpu", False) or planned_hardware.get("has_nvidia", False)):
            applicable_future = True

    return {
        "finding": finding,
        "applicable_now": applicable_now,
        "applicable_future": applicable_future,
        "needs_npu": needs_npu,
        "needs_gpu": needs_gpu,
        "action": "apply" if applicable_now else ("bookmark" if applicable_future else "skip"),
    }
