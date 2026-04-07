from __future__ import annotations
import json
import pytest
from pathlib import Path


@pytest.fixture
def dash_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "briefings").mkdir()
    (data_dir / "proposals").mkdir()
    # Write a sample briefing
    briefing = {"date": "2026-04-07", "health": {"checks_run": 10, "checks_passed": 9, "checks_failed": 1}}
    (data_dir / "briefings" / "2026-04-07.json").write_text(json.dumps(briefing))
    return data_dir


def test_dashboard_creates_app(dash_env):
    from core.observe.dashboard import create_app
    app = create_app(data_dir=str(dash_env))
    assert app is not None


def test_health_endpoint(dash_env):
    from core.observe.dashboard import create_app
    from starlette.testclient import TestClient
    app = create_app(data_dir=str(dash_env))
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


def test_briefings_endpoint(dash_env):
    from core.observe.dashboard import create_app
    from starlette.testclient import TestClient
    app = create_app(data_dir=str(dash_env))
    client = TestClient(app)
    resp = client.get("/api/briefings")
    assert resp.status_code == 200
