from __future__ import annotations
import pytest
from pathlib import Path


def test_generate_aider_config(tmp_path):
    from core.tools.setup_aider import generate_aider_config
    config = generate_aider_config(
        llm_endpoint="http://localhost:8080",
        api_key="gsk_test123",
        provider="groq",
    )
    assert "model" in config
    assert isinstance(config, dict)


def test_write_aider_config(tmp_path):
    from core.tools.setup_aider import generate_aider_config, write_aider_config
    config = generate_aider_config(
        llm_endpoint="https://api.groq.com/openai/v1",
        api_key="gsk_test",
        provider="groq",
    )
    path = write_aider_config(config, config_dir=str(tmp_path))
    assert Path(path).exists()
    content = Path(path).read_text()
    assert "groq" in content.lower() or "api" in content.lower()


def test_generate_setup_script(tmp_path):
    from core.tools.setup_aider import generate_setup_script
    script = generate_setup_script(provider="groq", api_key_env="GROQ_API_KEY")
    assert "pip" in script or "aider" in script
    assert isinstance(script, str)
