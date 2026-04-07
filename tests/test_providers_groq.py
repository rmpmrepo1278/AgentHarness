"""Tests for the Groq cloud LLM provider."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from core.providers.base import LLMRequest
from core.providers.groq import GroqProvider


def _mock_response(status_code: int, json_data: dict | None = None, text: str = "") -> MagicMock:
    """Build a fake httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


def test_groq_complete_success():
    provider = GroqProvider(api_key="sk-test")
    request = LLMRequest(prompt="Hello")

    fake_body = {
        "choices": [{"message": {"content": "Hi there!"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
    with patch("core.providers.groq.httpx.post", return_value=_mock_response(200, fake_body)):
        result = provider.complete(request)

    assert result.success is True
    assert result.text == "Hi there!"
    assert result.provider == "groq"
    assert result.tokens_in == 5
    assert result.tokens_out == 3
    assert provider._usage_today == 1


def test_groq_rate_limited():
    provider = GroqProvider(api_key="sk-test")
    request = LLMRequest(prompt="Hello")

    with patch("core.providers.groq.httpx.post", return_value=_mock_response(429, text="Too many requests")):
        result = provider.complete(request)

    assert result.success is False
    assert "429" in (result.error or "")
    assert provider._usage_today == 0


def test_groq_no_api_key():
    provider = GroqProvider(api_key="")
    assert provider.is_available() is False


def test_groq_budget_status():
    provider = GroqProvider(api_key="sk-test", daily_limit=200)
    status = provider.budget_status()

    assert status.cost_model == "per_request"
    assert status.estimated_remaining == 200
