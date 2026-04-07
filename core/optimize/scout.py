"""Search external sources for new models, techniques, and tools."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from core.resilience.atomic_json import safe_read_json

log = logging.getLogger(__name__)

DEFAULT_GITHUB_REPOS = [
    "ggml-org/llama.cpp",
    "ikawrakow/ik_llama.cpp",
]


class Scout:
    """Search for optimization opportunities."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.state = safe_read_json(self.data_dir / "state.json", default={})

    def search_all(self) -> list[dict[str, Any]]:
        """Run all search sources."""
        findings = []
        findings.extend(self.search_github_releases(DEFAULT_GITHUB_REPOS))
        findings.extend(self.search_huggingface())
        return findings

    def search_github_releases(self, repos: list[str]) -> list[dict[str, Any]]:
        """Check GitHub repos for new releases."""
        findings = []
        for repo in repos:
            try:
                resp = httpx.get(
                    f"https://api.github.com/repos/{repo}/releases",
                    params={"per_page": 3},
                    timeout=10,
                    headers={"Accept": "application/vnd.github.v3+json"},
                )
                if resp.status_code != 200:
                    continue
                for release in resp.json()[:3]:
                    findings.append({
                        "source": "github",
                        "repo": repo,
                        "tag": release.get("tag_name", ""),
                        "name": release.get("name", ""),
                        "body": (release.get("body", "") or "")[:500],
                        "url": release.get("html_url", ""),
                    })
            except Exception as e:
                log.warning(f"GitHub search failed for {repo}: {e}")
        return findings

    def search_huggingface(self) -> list[dict[str, Any]]:
        """Search HuggingFace for new models matching hardware."""
        hw = self.state.get("hardware", {})
        ram_gb = hw.get("total_ram_gb", 0)
        if ram_gb <= 0:
            return []

        max_size_gb = ram_gb * 0.7  # Leave room for OS
        findings = []
        try:
            resp = httpx.get(
                "https://huggingface.co/api/models",
                params={"sort": "lastModified", "direction": -1, "limit": 5,
                        "filter": "gguf"},
                timeout=15,
            )
            if resp.status_code == 200:
                for model in resp.json()[:5]:
                    findings.append({
                        "source": "huggingface",
                        "model_id": model.get("modelId", ""),
                        "last_modified": model.get("lastModified", ""),
                        "tags": model.get("tags", [])[:10],
                    })
        except Exception as e:
            log.warning(f"HuggingFace search failed: {e}")
        return findings
