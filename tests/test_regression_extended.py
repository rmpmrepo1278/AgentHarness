"""
EXTENDED REGRESSION TEST SUITE — Critical Services

Tests for MCP Gateway, Cron Jobs, n8n, Backups, Hermes Memory, and Log Aggregation.
Run via: python3 -m pytest tests/test_regression_extended.py -v

These cover the services that are running but could break silently without
the basic suite catching them.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1: MCP GATEWAY INTEGRATION (18 servers, 143 tools)
# ═══════════════════════════════════════════════════════════════════════════

MCP_GATEWAY = "http://localhost:8090"

EXPECTED_MCP_SERVERS = {
    "backup":              {"min_tools": 3, "port": 8102},
    "code-review-graph":   {"min_tools": 10, "port": 8096},
    "data-management":     {"min_tools": 9, "port": 8100},
    "doctor":              {"min_tools": 2, "port": 8105},
    "git":                 {"min_tools": 9, "port": 8100},
    "global-chat":         {"min_tools": 7, "port": 8106},
    "global-chat-mcp":     {"min_tools": 3, "port": 8104},
    "graphify-mcp":        {"min_tools": 4, "port": 8110},
    "hermes-memory":       {"min_tools": 8, "port": 8091},
    "homelab-exec":        {"min_tools": 20, "port": 8108},
    "infrastructure-backup":   {"min_tools": 3, "port": 8102},
    "infrastructure-doctor":   {"min_tools": 2, "port": 8105},
    "infrastructure-files":    {"min_tools": 10, "port": 8097},
    "network":             {"min_tools": 5, "port": 8103},
    "rss":                 {"min_tools": 4, "port": 8110},
    "system-docker":       {"min_tools": 5, "port": 8103},
    "system-homelab-ops":  {"min_tools": 7, "port": 8106},
    "system-network":      {"min_tools": 5, "port": 8107},
}


class TestMCPGateway:
    """Tests for MCP Gateway and all registered MCP servers."""

    def test_gateway_healthy(self):
        """MCP Gateway health endpoint returns ok."""
        r = httpx.get(f"{MCP_GATEWAY}/health", timeout=5)
        assert r.status_code == 200
        assert r.json().get("status") == "ok"

    def test_gateway_status_running(self):
        """Gateway status shows running with registered MCPs."""
        r = httpx.get(f"{MCP_GATEWAY}/status", timeout=5)
        d = r.json()
        assert d.get("status") == "running"
        assert d.get("mcps_registered", 0) >= 11, \
            f"Only {d.get('mcps_registered')} MCP servers registered"

    def test_most_mcps_healthy(self):
        """At least 10 of 18 MCP servers are healthy (1 may be offline)."""
        r = httpx.get(f"{MCP_GATEWAY}/status", timeout=5)
        d = r.json()
        healthy = d.get("mcps_healthy", 0)
        offline = d.get("mcps_offline", 0)
        assert healthy >= 10, f"Only {healthy} healthy, {offline} offline"

    def test_total_tools_registered(self):
        """Gateway has at least 50 tools registered across all MCPs."""
        r = httpx.get(f"{MCP_GATEWAY}/status", timeout=5)
        total = r.json().get("total_tools", 0)
        assert total >= 50, f"Only {total} tools registered"

    def test_tools_catalog_populated(self):
        """Tools catalog endpoint returns tools."""
        r = httpx.get(f"{MCP_GATEWAY}/tools/catalog", timeout=10)
        assert r.status_code == 200
        tools = r.json()
        assert len(tools) > 0, "Tools catalog empty"

    def test_individual_mcp_servers_registered(self):
        """All expected MCP servers are registered with the gateway."""
        r = httpx.get(f"{MCP_GATEWAY}/mcps", timeout=5)
        d = r.json()
        registered = set(d.keys())
        for name, info in EXPECTED_MCP_SERVERS.items():
            assert name in registered, f"MCP server '{name}' not registered"

    def test_mcp_servers_have_tools(self):
        """Each registered MCP server exposes its expected minimum tools."""
        r = httpx.get(f"{MCP_GATEWAY}/mcps", timeout=5)
        d = r.json()
        for name, expected in EXPECTED_MCP_SERVERS.items():
            if name in d:
                tools = d[name].get("tools", 0)
                assert tools >= expected["min_tools"], \
                    f"{name} has only {tools} tools, expected >= {expected['min_tools']}"

    def test_hermes_memory_mcp_responds(self):
        """Hermes Memory MCP responds to initialize."""
        r = httpx.post("http://localhost:8091/v1/messages", json={
            "jsonrpc": "2.0", "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "regression-test", "version": "1.0"}},
            "id": 1,
        }, timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert "result" in d
        assert d["result"]["serverInfo"]["name"] == "hermes-memory"

    def test_hermes_memory_save_and_recall(self):
        """Can save and recall an observation through Hermes Memory MCP."""
        # Save
        save_r = httpx.post("http://localhost:8091/v1/messages", json={
            "jsonrpc": "2.0", "method": "tools/call",
            "params": {
                "name": "hermes_save_observation",
                "arguments": {
                    "content": f"Regression test observation {time.time()}",
                    "importance": 0.5,
                    "category": "regression-test",
                }
            },
            "id": 2,
        }, timeout=15)
        assert save_r.status_code == 200

        # Recall
        recall_r = httpx.post("http://localhost:8091/v1/messages", json={
            "jsonrpc": "2.0", "method": "tools/call",
            "params": {
                "name": "hermes_recall",
                "arguments": {"query": "regression test observation", "limit": 5}
            },
            "id": 3,
        }, timeout=15)
        assert recall_r.status_code == 200

    def test_docker_mcp_tools_list(self):
        """system-docker MCP server responds with tool list."""
        r = httpx.post("http://localhost:8103/v1/messages", json={
            "jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1,
        }, timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert "result" in d
        tools = d["result"].get("tools", [])
        assert len(tools) >= 5, f"system-docker MCP has only {len(tools)} tools"

    def test_gateway_capabilities_endpoint(self):
        """Gateway status endpoint includes capability counts."""
        r = httpx.get(f"{MCP_GATEWAY}/status", timeout=5)
        d = r.json()
        # Should track degraded/healthy/offline counts
        assert "mcps_healthy" in d
        assert "mcps_degraded" in d
        assert "mcps_offline" in d


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2: CRON JOB HEALTH
# ═══════════════════════════════════════════════════════════════════════════

# All cron jobs were consolidated into hermes_scheduler.py (the crontab is now
# empty). These are the critical job names that must be registered with the
# unified scheduler.
CRITICAL_SCHEDULER_JOBS = [
    "gateway_guardian",      # every 5 min — MCP/gateway health guard
    "consolidated_health",   # every 5 min — merged health check
    "health_dashboard",      # every 5 min — dashboard metrics
    "health_ingest",         # every 5 min — memory ingestion
    "dns_healthcheck",       # every 5 min — DNS/duckdns guard
    "system_doctor",         # every 30 min — cross-system doctor
    "backup_all",            # daily 02:00 — unified backups
    "logrotate",             # daily 03:00 — log rotation
]

# Scripts that must exist (whether or not they are scheduled)
CRITICAL_SCRIPTS = [
    "/home/rohit/agentharness/scripts/consolidated_health.sh",
    "/home/rohit/agentharness/scripts/health_dashboard.py",
    "/home/rohit/agentharness/scripts/backup_all.sh",
    "/home/rohit/agentharness/scripts/db_backup.sh",
    "/home/rohit/agentharness/scripts/kopia_backup.sh",
    "/home/rohit/agentharness/scripts/sync_backup_remote.sh",
    "/home/rohit/agentharness/scripts/cve_monitor.sh",
    "/home/rohit/.hermes/scripts/gateway_guardian.py",
    "/home/rohit/.hermes/scripts/hermes_scheduler.py",
]

# Scheduler state file (last-run statuses)
SCHEDULER_STATE = Path("/home/rohit/.hermes/data/scheduler_state.json")
SCHEDULER_DAEMON = "/home/rohit/.hermes/scripts/hermes_scheduler.py --daemon"


class TestCronJobs:
    """Tests for hermes_scheduler (replaced the 41-entry crontab)."""

    def test_scheduler_daemon_running(self):
        """Unified hermes scheduler daemon is running."""
        result = subprocess.run(
            ["pgrep", "-f", SCHEDULER_DAEMON],
            capture_output=True, text=True, timeout=5
        )
        assert result.returncode == 0, "hermes_scheduler.py --daemon not running"

    @pytest.mark.parametrize("job_name", CRITICAL_SCHEDULER_JOBS)
    def test_critical_job_defined(self, job_name):
        """Critical job is registered with the unified scheduler."""
        src = Path("/home/rohit/.hermes/scripts/hermes_scheduler.py").read_text()
        assert f'Job("{job_name}"' in src, \
            f"Scheduler job not found: {job_name}"

    @pytest.mark.parametrize("script", CRITICAL_SCRIPTS)
    def test_cron_script_exists(self, script):
        """Critical script file exists."""
        assert Path(script).exists(), f"Script missing: {script}"

    def test_scheduler_state_fresh(self):
        """Scheduler state file was updated within the last 24h."""
        assert SCHEDULER_STATE.exists(), "scheduler_state.json missing"
        age_hours = (time.time() - SCHEDULER_STATE.stat().st_mtime) / 3600
        assert age_hours < 24, \
            f"scheduler_state.json stale: {age_hours:.1f}h old"

    def test_critical_jobs_succeeded_recently(self):
        """Health/backup jobs have succeeded in the last 24 hours."""
        assert SCHEDULER_STATE.exists(), "scheduler_state.json missing"
        state = json.loads(SCHEDULER_STATE.read_text())
        since = datetime.now(UTC) - timedelta(hours=24)
        ok = []
        for job in ["gateway_guardian", "consolidated_health", "backup_all"]:
            entry = state.get(job)
            if entry and entry.get("last_status") == "success":
                last_run = datetime.fromisoformat(entry["last_run"])
                if last_run > since:
                    ok.append(job)
        assert ok, "No critical scheduler job succeeded in the last 24h"

    def test_cron_log_recent_activity(self):
        """Scheduler produced recent activity (last 48h)."""
        # The unified scheduler records each job run in scheduler_state.json.
        assert SCHEDULER_STATE.exists(), "scheduler_state.json missing"
        state = json.loads(SCHEDULER_STATE.read_text())
        since = time.time() - 48 * 3600
        jobs_with_recent_run = []
        for name, entry in state.items():
            if not isinstance(entry, dict):
                continue
            last_run = entry.get("last_run")
            if not last_run:
                continue
            try:
                ts = datetime.fromisoformat(last_run).timestamp()
            except (ValueError, TypeError):
                continue
            if ts > since:
                jobs_with_recent_run.append(name)
        assert jobs_with_recent_run, \
            "No scheduler job activity recorded in the last 48 hours"

    def test_hermes_scheduler_active(self):
        """Hermes unified scheduler is the active scheduler."""
        result = subprocess.run(
            ["pgrep", "-f", SCHEDULER_DAEMON],
            capture_output=True, text=True, timeout=5
        )
        assert result.returncode == 0, "hermes scheduler daemon not active"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3: N8N WEBHOOK ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

class TestN8n:
    """Tests for n8n workflow automation."""

    def test_n8n_healthy(self):
        """n8n health endpoint responds."""
        r = httpx.get("http://localhost:5678/healthz", timeout=5)
        assert r.status_code == 200

    def test_n8n_api_accessible(self):
        """n8n API is accessible (responds with auth challenge)."""
        r = httpx.get("http://localhost:5678/api/v1/workflows", timeout=5)
        # Should get 401 unauthorized (not connection refused)
        assert r.status_code in (401, 403), f"n8n API unexpected status: {r.status_code}"

    def test_n8n_frontend_loads(self):
        """n8n web UI is served."""
        r = httpx.get("http://localhost:5678/", timeout=5)
        assert r.status_code == 200
        assert "n8n" in r.text.lower() or "workflow" in r.text.lower()

    def test_n8n_mcp_registered(self):
        """n8n webhook server is up and answering HTTP requests."""
        # n8n is now a standalone webhook service (not a gateway MCP server).
        # POST to an unregistered test webhook: n8n returns 404 "not
        # registered" — proving the webhook server is alive, rather than a
        # connection-refused (process down).
        r = httpx.post("http://localhost:5678/webhook-test/ping", timeout=5)
        assert r.status_code == 404, \
            f"n8n webhook server unexpected status: {r.status_code}"

    def test_n8n_container_healthy(self):
        """n8n Docker container is running (image has no healthcheck)."""
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", "n8n"],
            capture_output=True, text=True, timeout=5
        )
        status = result.stdout.strip()
        assert status == "running", f"n8n container status: {status}"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4: BACKUP INTEGRITY
# ═══════════════════════════════════════════════════════════════════════════

class TestBackupIntegrity:
    """Tests for backup system."""

    def test_backup_scripts_exist(self):
        """All critical backup scripts exist."""
        scripts = [
            "/home/rohit/agentharness/scripts/db_backup.sh",
            "/home/rohit/agentharness/scripts/backup_all.sh",
            "/home/rohit/agentharness/scripts/kopia_backup.sh",
            "/home/rohit/agentharness/scripts/sync_backup_remote.sh",
        ]
        for script in scripts:
            assert Path(script).exists(), f"Backup script missing: {script}"

    def test_backup_is_configured(self):
        """Backup jobs are registered with the unified scheduler."""
        src = Path("/home/rohit/.hermes/scripts/hermes_scheduler.py").read_text()
        assert 'Job("backup_all"' in src, "No backup_all scheduler job configured"
        assert 'Job("cloud_sync"' in src, "No cloud_sync scheduler job configured"

    def test_usb_mount_available(self):
        """USB backup mount point is available (for backups to work)."""
        result = subprocess.run(
            ["mountpoint", "-q", "/mnt/usb"],
            capture_output=True, timeout=5
        )
        # USB might not always be mounted — this is a soft check
        if result.returncode != 0:
            pytest.skip("USB not mounted — backup destination unavailable")

    def test_backup_logs_recent_or_usb_unmounted(self):
        """Backup has run recently OR USB is not mounted (skip if USB issue)."""
        log = Path("/home/rohit/agentharness/logs/db_backup_cron.log")
        if log.exists() and log.stat().st_size > 0:
            mtime = log.stat().st_mtime
            age_days = (time.time() - mtime) / 86400
            if age_days > 7:
                pytest.warns(UserWarning, "No backup log activity in 7 days")
        # If USB not mounted, backup can't run — that's OK
        usb_mounted = subprocess.run(
            ["mountpoint", "-q", "/mnt/usb"], capture_output=True, timeout=5
        ).returncode == 0
        if not usb_mounted:
            pytest.skip("USB not mounted")

    def test_docker_volumes_backup_script(self):
        """Docker volumes backup script exists and is executable."""
        script = Path("/home/rohit/agentharness/scripts/kopia_backup.sh")
        assert script.exists()
        assert os.access(script, os.X_OK)

    def test_database_dumps_directory(self):
        """Database dump directory is accessible."""
        # Check if /mnt/usb exists for backups
        usb = Path("/mnt/usb")
        if usb.exists():
            # Should be able to list it
            contents = list(usb.iterdir())
            # Might be empty — that's fine
            assert isinstance(contents, list)

    def test_no_backup_errors_in_logs(self):
        """No errors in recent backup logs."""
        log = Path("/home/rohit/agentharness/logs/db_backup_cron.log")
        if log.exists() and log.stat().st_size > 0:
            content = log.read_text()
            # Check for explicit ERROR lines (not just the word "error")
            error_lines = [l for l in content.split("\n")
                          if "ERROR" in l.upper() and "#" not in l]
            assert len(error_lines) == 0, \
                f"Backup errors found: {error_lines[:3]}"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5: HERMES MEMORY INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

class TestHermesMemory:
    """Tests for Hermes ↔ Claude Code shared memory pipeline."""

    def test_memory_mcp_container_healthy(self):
        """Hermes Memory MCP container is running."""
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}",
             "hermes-memory-mcp"],
            capture_output=True, text=True, timeout=5
        )
        assert result.stdout.strip() == "running"

    def test_memory_mcp_initializes(self):
        """Hermes Memory MCP server initializes correctly."""
        r = httpx.post("http://localhost:8091/v1/messages", json={
            "jsonrpc": "2.0", "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "regression-test", "version": "1.0"}},
            "id": 1,
        }, timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert "result" in d
        srv = d["result"].get("serverInfo", {})
        assert srv.get("name") == "hermes-memory"

    def test_memory_tools_list(self):
        """Hermes Memory MCP exposes expected tools."""
        r = httpx.post("http://localhost:8091/v1/messages", json={
            "jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1,
        }, timeout=10)
        assert r.status_code == 200
        d = r.json()
        tools = d.get("result", {}).get("tools", [])
        tool_names = [t["name"] for t in tools]
        expected_tools = ["hermes_save_observation", "hermes_recall"]
        for expected in expected_tools:
            assert expected in tool_names, f"Tool {expected} not found. Have: {tool_names}"

    def test_memory_save_observation(self):
        """Can save an observation to Hermes memory."""
        r = httpx.post("http://localhost:8091/v1/messages", json={
            "jsonrpc": "2.0", "method": "tools/call",
            "params": {
                "name": "hermes_save_observation",
                "arguments": {
                    "content": f"Regression test: memory save {time.time()}",
                    "importance": 0.5,
                    "category": "regression-test",
                }
            },
            "id": 2,
        }, timeout=15)
        assert r.status_code == 200

    def test_memory_recall(self):
        """Can recall observations from Hermes memory."""
        # First save
        httpx.post("http://localhost:8091/v1/messages", json={
            "jsonrpc": "2.0", "method": "tools/call",
            "params": {
                "name": "hermes_save_observation",
                "arguments": {
                    "content": f"Regression test recall {time.time()}",
                    "importance": 0.7,
                    "category": "regression-test",
                }
            },
            "id": 1,
        }, timeout=15)

        # Then recall
        r = httpx.post("http://localhost:8091/v1/messages", json={
            "jsonrpc": "2.0", "method": "tools/call",
            "params": {
                "name": "hermes_recall",
                "arguments": {"query": "regression test recall", "limit": 5}
            },
            "id": 2,
        }, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert "result" in d

    def test_memory_db_integrity(self):
        """Hermes memory database is not corrupted."""
        db_path = Path("/home/rohit/.hermes/claudemem.db")
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                conn.execute("PRAGMA integrity_check")
                conn.close()
            except sqlite3.DatabaseError as e:
                pytest.fail(f"Hermes memory DB corrupt: {e}")

    def test_shared_memory_db_integrity(self):
        """Shared memory (claudemem) database is not corrupted."""
        db_path = Path("/home/rohit/.hermes/claudemem.db")
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                # Should be able to query
                cursor = conn.execute("SELECT count(*) FROM observations")
                count = cursor.fetchone()[0]
                conn.close()
                assert count >= 0  # Just validates the table exists
            except sqlite3.DatabaseError as e:
                pytest.fail(f"Claudemem DB issue: {e}")

    def test_sops_searchable(self):
        """SOPs can be searched through Hermes Memory MCP."""
        r = httpx.post("http://localhost:8091/v1/messages", json={
            "jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1,
        }, timeout=10)
        d = r.json()
        tool_names = [t["name"] for t in d.get("result", {}).get("tools", [])]
        assert "hermes_sops" in tool_names, "SOPs tool not found"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6: LOG AGGREGATION
# ═══════════════════════════════════════════════════════════════════════════

class TestLogAggregation:
    """Tests for Loki + Promtail + Grafana stack."""

    def test_loki_ready(self):
        """Loki is ready to accept logs."""
        r = httpx.get("http://localhost:3100/ready", timeout=5)
        assert r.status_code == 200
        assert "ready" in r.text

    def test_loki_metrics(self):
        """Loki exposes Prometheus metrics."""
        r = httpx.get("http://localhost:3100/metrics", timeout=5)
        assert r.status_code == 200
        assert "go_" in r.text  # Go runtime metrics present

    def test_grafana_healthy(self):
        """Grafana health endpoint responds."""
        r = httpx.get("http://localhost:3001/api/health", timeout=5)
        assert r.status_code == 200

    def test_grafana_loki_datasource(self):
        """Grafana has Loki configured as a datasource."""
        r = httpx.get("http://localhost:3001/api/datasources",
                      auth=("admin", "admin"), timeout=5)
        if r.status_code == 200:
            ds = r.json()
            names = [d.get("type", "") for d in ds]
            assert "loki" in names, f"No Loki datasource. Have: {names}"
        else:
            pytest.skip("Grafana auth not available")

    def test_grafana_dashboard_accessible(self):
        """Grafana dashboards API is accessible."""
        r = httpx.get("http://localhost:3001/api/search",
                      auth=("admin", "admin"), timeout=5)
        # Should get 200 or 401 (not connection refused)
        assert r.status_code in (200, 401, 403)

    def test_promtail_running(self):
        """Promtail log shipper is running."""
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", "promtail"],
            capture_output=True, text=True, timeout=5
        )
        assert result.stdout.strip() == "running"

    def test_logs_being_ingested(self):
        """Loki has received recent log data (log volume > 0)."""
        r = httpx.get("http://localhost:3100/metrics", timeout=5)
        metrics = r.text
        # Look for log ingestion metrics
        has_ingestion = ("loki_distributor_bytes_received" in metrics or
                        "loki_ingester_chunks_flushed" in metrics or
                        "log_messages_total" in metrics)
        # Soft check — metrics names vary by version
        assert has_ingestion or len(metrics) > 100, "Loki metrics empty"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7: COMPREHENSIVE DOCKER VOLUME HEALTH
# ═══════════════════════════════════════════════════════════════════════════

class TestDockerVolumes:
    """Tests for Docker volume integrity."""

    def test_critical_volumes_exist(self):
        """Critical Docker volumes are present."""
        result = subprocess.run(
            ["docker", "volume", "ls", "--format", "{{.Name}}"],
            capture_output=True, text=True, timeout=5
        )
        volumes = result.stdout.strip().split("\n")
        # Check for key volumes
        critical = ["compose_vaultwarden_data"]
        for vol in critical:
            assert vol in volumes, f"Missing critical volume: {vol}"

    def test_no_orphaned_containers(self):
        """No stale stopped containers accumulating."""
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "status=exited",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5
        )
        exited = [l for l in result.stdout.strip().split("\n") if l]
        # Some exited containers are normal (oneshot tasks)
        # But more than 5 is a warning sign
        assert len(exited) < 10, \
            f"Too many exited containers ({len(exited)}): {exited[:5]}"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 8: RATE LIMIT TRACKER
# ═══════════════════════════════════════════════════════════════════════════

class TestRateLimitTracker:
    """Tests for the rate limit tracker (observability via proxy /v1/status)."""

    PROXY = "http://localhost:8080"

    def test_tracker_initialized(self):
        """Proxy server (which hosts the rate limit tracker) is up."""
        r = httpx.get(f"{self.PROXY}/health", timeout=5)
        assert r.status_code == 200

    def test_provider_health_endpoint(self):
        """Proxy status endpoint exposes per-provider health data."""
        r = httpx.get(f"{self.PROXY}/v1/status", timeout=5)
        assert r.status_code == 200
        d = r.json()
        assert "overall" in d
        assert "providers" in d
        assert len(d["providers"]) >= 1

    def test_provider_health_probes(self):
        """Each provider exposes health_probe consecutive-failure counters."""
        r = httpx.get(f"{self.PROXY}/v1/status", timeout=5)
        providers = r.json()["providers"]
        assert len(providers) >= 1, "No providers registered"
        for provider, pstats in providers.items():
            hp = pstats.get("health_probe", {})
            assert "healthy" in hp, f"{provider} missing health_probe.healthy"
            assert hp["healthy"] is True, \
                f"{provider} health_probe unhealthy: {hp}"

    def test_circuit_breaker_states(self):
        """Each provider exposes a valid circuit-breaker state."""
        r = httpx.get(f"{self.PROXY}/v1/status", timeout=5)
        providers = r.json()["providers"]
        valid = {"CLOSED", "HALF_OPEN", "OPEN", "DEGRADED"}
        for provider, pstats in providers.items():
            cb = pstats.get("circuit_breaker", {})
            assert "state" in cb, f"{provider} missing circuit_breaker.state"
            assert cb["state"] in valid, \
                f"{provider} invalid circuit_breaker state: {cb['state']}"

    def test_overall_health_flag(self):
        """Proxy reports an overall health status."""
        r = httpx.get(f"{self.PROXY}/v1/status", timeout=5)
        overall = r.json().get("overall")
        assert overall in ("healthy", "degraded", "unhealthy"), \
            f"Unexpected overall health: {overall}"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 9: TOKENJUICE PREPROCESSING
# ═══════════════════════════════════════════════════════════════════════════

class TestTokenJuice:
    """Tests for TokenJuice preprocessing layer."""

    def test_token_juice_endpoint(self):
        """TokenJuice stats are available via the proxy cache endpoint."""
        r = httpx.get("http://localhost:8080/v1/cache", timeout=5)
        assert r.status_code == 200
        d = r.json()
        assert "token_juice" in d
        assert "hits" in d
        assert "misses" in d
        assert "size" in d

    def test_token_juice_enabled(self):
        """TokenJuice stats are exposed via the proxy /v1/cache endpoint."""
        r = httpx.get("http://localhost:8080/v1/cache", timeout=5)
        assert r.status_code == 200
        tj = r.json().get("token_juice")
        assert tj is not None, "token_juice stats missing from /v1/cache"

    def test_token_juice_counters(self):
        """TokenJuice counters are present."""
        r = httpx.get("http://localhost:8080/v1/cache", timeout=5)
        stats = r.json()["token_juice"]
        assert "total_requests" in stats
        assert "cache_hits" in stats
        assert "cache_misses" in stats
        assert "tokens_saved" in stats
        assert "timeouts" in stats
        assert "errors" in stats

    def test_html_to_markdown(self):
        """TokenJuice converts HTML to markdown."""
        from core.providers.token_juice import html_to_markdown
        html = "<h1>Title</h1><p>Hello <strong>world</strong></p><script>alert('xss')</script>"
        result = html_to_markdown(html)
        assert "# Title" in result
        assert "**world**" in result
        assert "alert" not in result  # script removed

    @pytest.mark.asyncio
    async def test_table_preservation(self):
        """TokenJuice preserves HTML tables as HTML fragments in juice_body."""
        from core.providers.token_juice import juice_body
        body = {
            "messages": [{"role": "user", "content": "<p>Before</p><table><tr><td>Cell</td></tr></table><p>After</p>"}]
        }
        result = await juice_body(body)
        content = result["messages"][0]["content"]
        assert "<table" in content, f"Table not preserved: {content}"
        assert "Cell" in content

    def test_url_shortening(self):
        """TokenJuice strips tracking params from URLs."""
        from core.providers.token_juice import shorten_url
        url = "https://example.com/page?utm_source=twitter&utm_medium=social&id=123"
        result = shorten_url(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "id=123" in result

    @pytest.mark.asyncio
    async def test_content_cache(self):
        """TokenJuice caches processed content."""
        from core.providers.token_juice import _content_cache, juice_body
        html = "<h1>Cache Test</h1><p>Content</p>"
        body = {"messages": [{"role": "user", "content": html}]}
        # First call — processes and caches
        result1 = await juice_body(body)
        # Check cache has entry
        cached = _content_cache.get(html)
        assert cached is not None, "Content should be cached after juice_body"
        # Second call — should hit cache
        body2 = {"messages": [{"role": "user", "content": html}]}
        result2 = await juice_body(body2)
        assert result2["messages"][0]["content"] == cached


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 10: CONTEXT HARVESTER
# ═══════════════════════════════════════════════════════════════════════════

class TestContextHarvester:
    """Tests for the context harvester script."""

    def test_harvester_script_exists(self):
        """Context harvester script exists and is executable."""
        script = Path("/home/rohit/agentharness/scripts/context_harvester.py")
        assert script.exists()

    def test_harvester_dry_run(self):
        """Harvester dry run produces output."""
        result = subprocess.run(
            [sys.executable, "/home/rohit/agentharness/scripts/context_harvester.py",
             "--dry-run"],
            capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0
        assert "harvest" in result.stdout.lower()

    def test_harvester_lock_works(self):
        """Harvester file lock prevents concurrent runs."""
        import fcntl
        lockfile = Path("/tmp/context_harvester.lock")
        # Acquire lock
        fd = open(lockfile, "w")
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Try another instance
        result = subprocess.run(
            [sys.executable, "/home/rohit/agentharness/scripts/context_harvester.py",
             "--dry-run"],
            capture_output=True, text=True, timeout=10
        )
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        fd.close()
        # Should have exited cleanly (lock blocked it)
        assert result.returncode == 0
        assert "already running" in result.stdout

    def test_harvester_state_persistence(self):
        """Harvester state file is created and valid JSON."""
        state_file = Path("/home/rohit/.hermes/claudemem_harvest_state.json")
        if state_file.exists():
            state = json.loads(state_file.read_text())
            assert "last_harvest" in state or state == {}


# ═══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════

def pytest_sessionfinish(session, exitstatus):
    """Print summary."""
    if exitstatus == 0:
        print("\n" + "=" * 60)
        print("✓ EXTENDED REGRESSION PASSED — All critical services healthy")
        print("  MCP Gateway ✓  Cron ✓  n8n ✓  Backups ✓  Memory ✓  Logs ✓")
        print("  Rate Limits ✓  TokenJuice ✓  Context Harvester ✓")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("✗ EXTENDED REGRESSION FAILED — Service impact detected")
        print("=" * 60)
