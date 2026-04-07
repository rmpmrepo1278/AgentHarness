"""Tests for core.security.audit — JSONL exec audit trail with secret redaction."""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from core.security.audit import AuditLogger, MAX_OUTPUT_LENGTH


# --- test_log_execution ---

def test_log_execution():
    """Log one execution, verify JSONL file has tool, trigger, exit_code, timestamp."""
    with tempfile.TemporaryDirectory() as tmp:
        logger = AuditLogger(tmp)
        logger.log_execution(
            tool="git",
            trigger="schedule",
            args={"repo": "my-repo"},
            exit_code=0,
            output="ok",
        )
        with open(logger.log_file) as f:
            line = f.readline()
        entry = json.loads(line)
        assert entry["tool"] == "git"
        assert entry["trigger"] == "schedule"
        assert entry["exit_code"] == 0
        assert "timestamp" in entry
        assert "epoch" in entry
        assert "pid" in entry
        assert "user" in entry
        assert entry["args"] == {"repo": "my-repo"}
        assert entry["output"] == "ok"


# --- test_log_truncates_long_output ---

def test_log_truncates_long_output():
    """Output > 2000 chars gets truncated."""
    with tempfile.TemporaryDirectory() as tmp:
        logger = AuditLogger(tmp)
        long_output = "x" * 5000
        logger.log_execution(
            tool="curl",
            trigger="manual",
            args={},
            exit_code=0,
            output=long_output,
        )
        with open(logger.log_file) as f:
            entry = json.loads(f.readline())
        assert len(entry["output"]) <= MAX_OUTPUT_LENGTH + 50  # allow for truncation marker
        assert entry["output"].endswith("...[truncated]")


# --- test_log_redacts_secrets ---

def test_log_redacts_secrets():
    """api_key values and token-like strings in output are redacted."""
    with tempfile.TemporaryDirectory() as tmp:
        logger = AuditLogger(tmp)
        secret_output = "token=sk-abc12345678 and key=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA done"
        logger.log_execution(
            tool="curl",
            trigger="manual",
            args={"api_key": "my-secret-key-value", "name": "safe"},
            exit_code=0,
            output=secret_output,
        )
        with open(logger.log_file) as f:
            entry = json.loads(f.readline())
        # Output secrets redacted
        assert "sk-abc12345678" not in entry["output"]
        assert "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" not in entry["output"]
        assert "REDACTED" in entry["output"]
        # Arg secrets redacted
        assert "my-secret-key-value" not in json.dumps(entry["args"])
        assert entry["args"]["name"] == "safe"
        assert "REDACTED" in entry["args"]["api_key"]


# --- test_multiple_entries_are_jsonl ---

def test_multiple_entries_are_jsonl():
    """Two entries produce two JSON lines."""
    with tempfile.TemporaryDirectory() as tmp:
        logger = AuditLogger(tmp)
        logger.log_execution(tool="git", trigger="a", args={}, exit_code=0, output="one")
        logger.log_execution(tool="docker", trigger="b", args={}, exit_code=1, output="two")
        with open(logger.log_file) as f:
            lines = f.readlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["tool"] == "git"
        assert json.loads(lines[1])["tool"] == "docker"


# --- optional fields ---

def test_optional_fields_recorded():
    """approval_id, sandbox_mode, duration_ms are stored when provided."""
    with tempfile.TemporaryDirectory() as tmp:
        logger = AuditLogger(tmp)
        logger.log_execution(
            tool="npm",
            trigger="approval",
            args={},
            exit_code=0,
            output="ok",
            approval_id="APR-42",
            sandbox_mode="strict",
            duration_ms=123,
        )
        with open(logger.log_file) as f:
            entry = json.loads(f.readline())
        assert entry["approval_id"] == "APR-42"
        assert entry["sandbox_mode"] == "strict"
        assert entry["duration_ms"] == 123
