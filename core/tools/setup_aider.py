"""Configure Aider to use AgentHarness LLM providers."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PROVIDER_CONFIGS = {
    "groq": {
        "api_base": "https://api.groq.com/openai/v1",
        "model": "groq/llama-3.3-70b-versatile",
        "api_key_env": "GROQ_API_KEY",
    },
    "local": {
        "api_base": os.environ.get("LLM_PRIMARY_URL", "http://localhost:8080") + "/v1",
        "model": "openai/local-model",
        "api_key_env": "",
    },
    "cerebras": {
        "api_base": "https://api.cerebras.ai/v1",
        "model": "cerebras/llama-3.3-70b",
        "api_key_env": "CEREBRAS_API_KEY",
    },
    "google": {
        "api_base": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini/gemini-2.5-flash",
        "api_key_env": "GOOGLE_API_KEY",
    },
}


def generate_aider_config(
    llm_endpoint: str = "",
    api_key: str = "",
    provider: str = "groq",
) -> dict:
    """Generate Aider configuration dict."""
    provider_cfg = PROVIDER_CONFIGS.get(provider, PROVIDER_CONFIGS["groq"])

    config = {
        "model": provider_cfg["model"],
        "openai-api-base": llm_endpoint or provider_cfg["api_base"],
        "openai-api-key": api_key or f"${{{provider_cfg['api_key_env']}}}",
        "auto-commits": False,
        "dark-mode": True,
        "no-auto-lint": True,
    }
    return config


def write_aider_config(config: dict, config_dir: str = "") -> str:
    """Write Aider config to .aider.conf.yml."""
    import yaml
    config_path = Path(config_dir or Path.home()) / ".aider.conf.yml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    log.info(f"Aider config written: {config_path}")
    return str(config_path)


def generate_setup_script(provider: str = "groq", api_key_env: str = "GROQ_API_KEY") -> str:
    """Generate a bash setup script for installing and configuring Aider."""
    provider_cfg = PROVIDER_CONFIGS.get(provider, PROVIDER_CONFIGS["groq"])

    script = f"""#!/bin/bash
# AgentHarness — Aider Setup Script
# Configures Aider to use {provider} as the LLM backend.

set -e

echo "Installing Aider..."
pip3 install --user aider-chat

echo "Configuring Aider for {provider}..."
export OPENAI_API_BASE="{provider_cfg['api_base']}"

if [ -n "${{{api_key_env}:-}}" ]; then
    export OPENAI_API_KEY="${{{api_key_env}}}"
    echo "Using {api_key_env} from environment."
else
    echo "WARNING: {api_key_env} not set. Set it in your .env file."
fi

echo ""
echo "Aider is ready. Run:"
echo "  cd /path/to/agentharness"
echo "  aider --model {provider_cfg['model']}"
echo ""
echo "Or for local LLM:"
echo "  aider --model openai/local-model --openai-api-base http://localhost:8080/v1"
"""
    return script
