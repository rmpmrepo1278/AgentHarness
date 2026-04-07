"""Tests for the llama.cpp LLM provider."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.providers.base import LLMRequest
from core.providers.llamacpp import LlamaCppProvider


def test_llamacpp_complete_success():
    """Mock httpx.post returning 200 with choices/usage, verify success."""
    provider = LlamaCppProvider()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "Hello from llama.cpp"}}],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 8,
        },
    }
    mock_response.raise_for_status = MagicMock()

    with patch("core.providers.llamacpp.httpx.post", return_value=mock_response):
        result = provider.complete(LLMRequest(prompt="Hi"))

    assert result.success is True
    assert result.text == "Hello from llama.cpp"
    assert result.tokens_in == 12
    assert result.tokens_out == 8


def test_llamacpp_complete_failure():
    """Mock httpx.post raising Exception, verify success=False with error."""
    provider = LlamaCppProvider()

    with patch(
        "core.providers.llamacpp.httpx.post",
        side_effect=Exception("Connection refused"),
    ):
        result = provider.complete(LLMRequest(prompt="Hi"))

    assert result.success is False
    assert "Connection refused" in result.error


def test_llamacpp_is_available():
    """Mock httpx.get returning 200, verify True."""
    provider = LlamaCppProvider()

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("core.providers.llamacpp.httpx.get", return_value=mock_response):
        assert provider.is_available() is True


def test_llamacpp_is_unavailable():
    """Mock httpx.get raising Exception, verify False."""
    provider = LlamaCppProvider()

    with patch(
        "core.providers.llamacpp.httpx.get",
        side_effect=Exception("Connection refused"),
    ):
        assert provider.is_available() is False


def test_llamacpp_budget_is_unlimited():
    """budget_status().cost_model == 'free', estimated_remaining is None."""
    provider = LlamaCppProvider()
    status = provider.budget_status()
    assert status.cost_model == "free"
    assert status.estimated_remaining is None
