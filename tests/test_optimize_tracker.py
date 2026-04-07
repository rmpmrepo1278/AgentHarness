# tests/test_optimize_tracker.py
from __future__ import annotations
import pytest


def test_record_finding(tmp_path):
    from core.optimize.tracker import OptimizationTracker
    t = OptimizationTracker(data_dir=str(tmp_path))
    t.record_finding({"source": "github", "repo": "test/repo", "tag": "v1.0"}, "bookmarked")
    history = t.get_history()
    assert len(history) == 1


def test_record_source_reliability(tmp_path):
    from core.optimize.tracker import OptimizationTracker
    t = OptimizationTracker(data_dir=str(tmp_path))
    t.record_source_result("github:ggml-org/llama.cpp", useful=True)
    t.record_source_result("github:ggml-org/llama.cpp", useful=True)
    t.record_source_result("github:ggml-org/llama.cpp", useful=False)
    score = t.get_source_reliability("github:ggml-org/llama.cpp")
    assert 0.5 < score < 1.0


def test_is_already_seen(tmp_path):
    from core.optimize.tracker import OptimizationTracker
    t = OptimizationTracker(data_dir=str(tmp_path))
    t.record_finding({"source": "github", "repo": "test/repo", "tag": "v1.0"}, "applied")
    assert t.is_seen("github", "test/repo", "v1.0") is True
    assert t.is_seen("github", "test/repo", "v2.0") is False
