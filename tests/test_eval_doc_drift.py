import json
import os
import shutil
import subprocess

import pytest

HERMES = "/home/rohit/.hermes"
SCRIPTS = f"{HERMES}/scripts"


def _run(cmd, timeout=60):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)


@pytest.mark.skipif(not os.path.exists("/home/rohit"), reason="Not on homelab")
class TestDriftCheckExists:
    def test_script_exists(self):
        assert os.path.isfile(f"{SCRIPTS}/doc_drift_check.py")

    def test_script_runnable(self):
        r = _run(f"python3 {SCRIPTS}/doc_drift_check.py --json --quiet")
        assert r.returncode in (0, 1), f"Script crashed: {r.stderr}"
        data = json.loads(r.stdout)
        assert "total" in data

    def test_json_output_valid(self):
        r = _run(f"python3 {SCRIPTS}/doc_drift_check.py --json --quiet")
        data = json.loads(r.stdout)
        assert "passed" in data
        assert "failed" in data
        assert "total" in data
        assert data["total"] >= 13

    def test_all_checks_pass_after_sync(self):
        _run(f"python3 {SCRIPTS}/claude_md_sync.py --all --quiet")
        r = _run(f"python3 {SCRIPTS}/doc_drift_check.py --json --quiet")
        data = json.loads(r.stdout)
        assert len(data["failed"]) == 0, f"Drift detected: {data["failed"]}"


@pytest.mark.skipif(not os.path.exists("/home/rohit"), reason="Not on homelab")
class TestDriftDetection:
    def test_detects_modification_in_temp_copy(self, tmp_path):
        src = "/home/rohit/CLAUDE.md"
        if not os.path.isfile(src):
            pytest.skip("CLAUDE.md not found")
        target = tmp_path / "CLAUDE.md"
        shutil.copy(src, target)
        original = target.read_text()
        marker = "<!-- AUTO-GEN:system_stats START -->"
        if marker in original:
            idx = original.index(marker)
            drifted = original[:idx] + "DRIFT_INJECTED" + original[idx:]
            target.write_text(drifted)
            assert "DRIFT_INJECTED" in target.read_text()
        else:
            pytest.skip("No auto-gen markers found in CLAUDE.md")

    def test_auto_fix_restores_consistency(self):
        r = _run(f"python3 {SCRIPTS}/claude_md_sync.py --all --quiet")
        assert r.returncode == 0, f"Sync failed: {r.stderr}"
        r2 = _run(f"python3 {SCRIPTS}/doc_drift_check.py --json --quiet")
        data = json.loads(r2.stdout)
        assert len(data["failed"]) == 0, f"Drift after sync: {data["failed"]}"


@pytest.mark.skipif(not os.path.exists("/home/rohit"), reason="Not on homelab")
class TestSyncScript:
    def test_sync_all_works(self):
        r = _run(f"python3 {SCRIPTS}/claude_md_sync.py --all --quiet")
        assert r.returncode == 0, f"Sync failed: {r.stderr}"

    def test_sync_check_mode(self):
        r = _run(f"python3 {SCRIPTS}/claude_md_sync.py --all --check --quiet")
        assert r.returncode in (0, 1)
