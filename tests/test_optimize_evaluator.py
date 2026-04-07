# tests/test_optimize_evaluator.py
from __future__ import annotations
import pytest


def test_evaluate_applicable_now():
    from core.optimize.evaluator import evaluate_finding
    hw = {"total_ram_gb": 36, "has_amd_gpu": False, "has_npu": False}
    finding = {"source": "github", "repo": "ggml-org/llama.cpp", "tag": "v1.0", "body": "Faster quantization"}
    result = evaluate_finding(finding, hw)
    assert result["applicable_now"] is True


def test_evaluate_not_applicable_npu():
    from core.optimize.evaluator import evaluate_finding
    hw = {"total_ram_gb": 36, "has_amd_gpu": False, "has_npu": False}
    finding = {"source": "github", "repo": "amd/lemonade", "tag": "v10.1", "body": "NPU acceleration"}
    result = evaluate_finding(finding, hw)
    assert result["applicable_now"] is False


def test_evaluate_future_hardware():
    from core.optimize.evaluator import evaluate_finding
    hw = {"total_ram_gb": 36, "has_npu": False}
    planned = {"has_npu": True, "has_amd_gpu": True}
    finding = {"source": "github", "repo": "amd/lemonade", "tag": "v10.1", "body": "NPU support"}
    result = evaluate_finding(finding, hw, planned_hardware=planned)
    assert result["applicable_future"] is True
