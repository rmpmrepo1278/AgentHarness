# tests/test_feedback_preference.py
from __future__ import annotations
import pytest


@pytest.fixture
def pref_dir(tmp_path):
    return tmp_path


def test_record_approval(pref_dir):
    from core.feedback.preference import PreferenceModel
    pm = PreferenceModel(data_dir=str(pref_dir))
    pm.record("container_restart", "approved")
    pm.record("container_restart", "approved")
    history = pm.get_history("container_restart")
    assert history["approved"] == 2


def test_record_rejection(pref_dir):
    from core.feedback.preference import PreferenceModel
    pm = PreferenceModel(data_dir=str(pref_dir))
    pm.record("container_restart", "rejected")
    pm.record("container_restart", "rejected")
    pm.record("container_restart", "rejected")
    history = pm.get_history("container_restart")
    assert history["rejected"] == 3


def test_suggest_suppression_after_threshold(pref_dir):
    from core.feedback.preference import PreferenceModel
    pm = PreferenceModel(data_dir=str(pref_dir), min_data_points=5)
    for _ in range(5):
        pm.record("auto_restart_jellyfin", "rejected")
    suggestion = pm.get_suggestion("auto_restart_jellyfin")
    assert suggestion is not None
    assert suggestion["action"] == "suppress"


def test_no_suggestion_below_threshold(pref_dir):
    from core.feedback.preference import PreferenceModel
    pm = PreferenceModel(data_dir=str(pref_dir), min_data_points=5)
    pm.record("cleanup", "approved")
    pm.record("cleanup", "approved")
    suggestion = pm.get_suggestion("cleanup")
    assert suggestion is None  # Only 2 data points, need 5


def test_reset_clears_all(pref_dir):
    from core.feedback.preference import PreferenceModel
    pm = PreferenceModel(data_dir=str(pref_dir))
    pm.record("test", "approved")
    pm.reset()
    history = pm.get_history("test")
    assert history["approved"] == 0


def test_persistence(pref_dir):
    from core.feedback.preference import PreferenceModel
    pm1 = PreferenceModel(data_dir=str(pref_dir))
    pm1.record("test", "approved")
    pm2 = PreferenceModel(data_dir=str(pref_dir))
    assert pm2.get_history("test")["approved"] == 1
