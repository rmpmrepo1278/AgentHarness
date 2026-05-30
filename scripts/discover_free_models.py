#!/usr/bin/env python3
"""
discover_free_models.py — Query all LLM providers for currently-free models.

Queries Google, Groq, Cerebras, SambaNova, and OpenRouter APIs to discover
which models are available for free RIGHT NOW. Outputs a unified JSON report
and optionally updates config.yaml + models.json.

Usage:
    python3 discover_free_models.py                    # Print report
    python3 discover_free_models.py --update           # Update config.yaml + models.json
    python3 discover_free_models.py --update --reload  # Update + restart proxy
    python3 discover_free_models.py --json             # Machine-readable JSON only
    python3 discover_free_models.py --dry-run          # Show what would change

Provider keys are read from ~/.hermes/.env (symlink to master secrets).
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime

import urllib.request
import urllib.error

log = logging.getLogger("discover")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HOME = Path.home()
DOTENV = HOME / ".hermes" / ".env"
HERMES_CONFIG = HOME / ".hermes" / "config.yaml"
MODELS_JSON = HOME / ".hermes" / "lib" / "costguard" / "models.json"
AH_DATA_DIR = HOME / "agentharness" / "data"
PROXY_LOG = AH_DATA_DIR / "logs" / "discover.log"

# ---------------------------------------------------------------------------
# Provider discovery configs
# Each entry: (name, url, key_env_var, parser_func_name)
# ---------------------------------------------------------------------------

def _load_keys() -> dict:
    """Load API keys from ~/.hermes/.env."""
    keys = {}
    if not DOTENV.exists():
        log.warning("Env file not found: %s", DOTENV)
        return keys
    with open(DOTENV) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                keys[k.strip()] = v.strip().strip('"').strip("'")
    return keys


def _http_get(url: str, headers: dict = None, timeout: float = 15.0) -> dict | list | None:
    """Make an HTTP GET request and return parsed JSON, or None on failure."""
    hdrs = {"User-Agent": "AgentHarness-Discover/1.0"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        log.debug("GET %s failed: %s", url, exc)
        return None


def _http_get_text(url: str, headers: dict = None, timeout: float = 15.0) -> str | None:
    """Make an HTTP GET request and return raw text, or None on failure."""
    hdrs = {"User-Agent": "AgentHarness-Discover/1.0"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode()
    except Exception as exc:
        log.debug("GET %s failed: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Per-provider discovery functions
# ---------------------------------------------------------------------------

def discover_google(keys: dict) -> list[dict]:
    """Discover free Google Gemini models via AI Studio API."""
    api_key = keys.get("GOOGLE_FREE_API_KEY") or keys.get("GOOGLE_API_KEY")
    if not api_key:
        log.info("GOOGLE_FREE_API_KEY not set — skipping Google")
        return []

    data = _http_get(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
        timeout=10,
    )
    if not data:
        return []

    models = []
    for m in data.get("models", []):
        name = m.get("name", "").replace("models/", "")
        methods = m.get("supportedGenerationMethods", [])
        if "generateContent" not in methods:
            continue
        ctx = m.get("inputTokenLimit", 0)

        # Skip TTS, image generation, robotics, and other non-LLM models
        skip_keywords = [
            "tts", "vision-only", "robotics", "computer-use",
            "deep-research", "antigravity", "nano-banana", "preview-tts",
            "clip-preview", "lyria", "embedding",
            "-image", "-image-preview", "-image-",
        ]
        if any(kw in name.lower() for kw in skip_keywords):
            continue

        # Skip ultra-short context models (< 16K)
        if ctx < 16384:
            continue

        models.append({
            "id": f"google/{name}",
            "model": name,
            "provider": "google",
            "context_length": ctx,
            "tools": True,  # All Gemini models support function calling
            "price_prompt": 0.0,
            "price_completion": 0.0,
            "note": f"Google free tier — {ctx//1024}K ctx",
        })

    log.info("Google: found %d free models", len(models))
    return models


def discover_groq(keys: dict) -> list[dict]:
    """Discover free Groq models."""
    api_key = keys.get("GROQ_API_KEY")
    if not api_key:
        log.info("GROQ_API_KEY not set — skipping Groq")
        return []

    data = _http_get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if not data:
        return []

    models = []
    for m in data.get("data", []):
        if not m.get("active", True):
            continue
        mid = m.get("id", "")
        ctx = m.get("context_window", 0)

        # Skip utility models (audio, image gen, guard, whisper)
        if any(x in mid for x in ["whisper", "prompt-guard", "tts", "image"]):
            continue
        if ctx < 8192:
            continue

        # Groq free tier supports function calling on most models
        models.append({
            "id": mid,
            "model": mid,
            "provider": "groq",
            "context_length": ctx,
            "tools": True,
            "price_prompt": 0.0,
            "price_completion": 0.0,
            "note": f"Groq free tier — {ctx//1024}K ctx",
        })

    log.info("Groq: found %d free models", len(models))
    return models


def discover_cerebras(keys: dict) -> list[dict]:
    """Discover free Cerebras models."""
    api_key = keys.get("CEREBRAS_API_KEY")
    if not api_key:
        log.info("CEREBRAS_API_KEY not set — skipping Cerebras")
        return []

    data = _http_get(
        "https://api.cerebras.ai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if not data:
        return []

    models = []
    for m in data.get("data", []):
        mid = m.get("id", "")
        if not mid:
            continue

        # Cerebras free tier — fast inference on premium hardware
        ctx_map = {
            "gpt-oss-120b": 131072,
            "zai-glm-4.5": 131072,
            "zai-glm-4.7": 131072,
            "cerebras-glm-4.7": 131072,
        }
        ctx = ctx_map.get(mid, 131072)

        models.append({
            "id": f"cerebras/{mid}",
            "model": mid,
            "provider": "cerebras",
            "context_length": ctx,
            "tools": True,
            "price_prompt": 0.0,
            "price_completion": 0.0,
            "note": f"Cerebras free tier — {ctx//1024}K ctx",
        })

    log.info("Cerebras: found %d free models", len(models))
    return models


def discover_sambanova(keys: dict) -> list[dict]:
    """Discover SambaNova models (free credits / very low cost)."""
    api_key = keys.get("SAMBANOVA_API_KEY")
    if not api_key:
        log.info("SAMBANOVA_API_KEY not set — skipping SambaNova")
        return []

    data = _http_get(
        "https://api.sambanova.ai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if not data:
        return []

    models = []
    for m in data.get("data", []):
        mid = m.get("id", "")
        ctx = m.get("context_length", 0)
        pricing = m.get("pricing", {})
        p_prompt = float(pricing.get("prompt", "0").replace("$", "").strip() or "0")
        p_completion = float(pricing.get("completion", "0").replace("$", "").strip() or "0")

        # Skip ultra-short context
        if ctx < 8192:
            continue

        # Determine if truly free or just cheap
        is_free = p_prompt == 0.0 and p_completion == 0.0
        models.append({
            "id": mid,
            "model": mid,
            "provider": "sambanova",
            "context_length": ctx,
            "tools": True,
            "price_prompt": p_prompt,
            "price_completion": p_completion,
            "is_free": is_free,
            "note": (f"SambaNova free tier" if is_free
                     else f"SambaNova ${p_prompt}/1M in ${p_completion}/1M out") + f" — {ctx//1024}K ctx",
        })

    log.info("SambaNova: found %d models", len(models))
    return models


def discover_openrouter(keys: dict) -> list[dict]:
    """Discover free OpenRouter models (pricing.prompt == '0' AND pricing.completion == '0')."""
    api_key = keys.get("OPENROUTER_API_KEY")
    if not api_key:
        log.info("OPENROUTER_API_KEY not set — skipping OpenRouter")
        return []

    data = _http_get(
        "https://openrouter.ai/api/v1/models",
        headers={
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/rohitmishra/agentharness",
        },
    )
    if not data:
        return []

    models = []
    for m in data.get("data", []):
        pricing = m.get("pricing", {})
        p_prompt = pricing.get("prompt", "1")
        p_completion = pricing.get("completion", "1")

        # Only truly free models (both prompt AND completion are zero)
        try:
            is_free = abs(float(p_prompt)) < 1e-10 and abs(float(p_completion)) < 1e-10
        except (ValueError, TypeError):
            continue
        if not is_free:
            continue

        ctx = m.get("context_length", 0)
        if ctx < 8192:
            continue

        # Check architecture for modality
        arch = m.get("architecture", {})
        modality = arch.get("modality", "text->text")

        # Skip non-chat models: TTS, image-gen, audio-gen, video-gen
        # Keep: text->text, text+image->text, text+image+video->text, etc.
        if "->" in modality:
            input_mods = set(modality.split("->")[0].split("+"))
            output_mods = set(modality.split("->")[-1].split("+"))
        else:
            input_mods = set()
            output_mods = set()

        # Skip if output includes audio (TTS) or is image-only
        if "audio" in output_mods or output_mods == {"image"}:
            continue
        # Skip if input doesn't include text
        if "text" not in input_mods:
            continue
        # Skip if output doesn't include text (pure media generation)
        if "text" not in output_mods and output_mods:
            continue

        mid = m.get("id", "")
        models.append({
            "id": mid,
            "model": mid,
            "provider": "openrouter",
            "context_length": ctx,
            "tools": True,  # Most OpenRouter free models support tools
            "modality": modality,
            "price_prompt": 0.0,
            "price_completion": 0.0,
            "note": f"OpenRouter free — {ctx//1024}K ctx — {modality}",
        })

    # Sort by context length descending (best first)
    models.sort(key=lambda x: x["context_length"], reverse=True)

    log.info("OpenRouter: found %d free models", len(models))
    return models


# ---------------------------------------------------------------------------
# Scoring and selection
# ---------------------------------------------------------------------------

# Model name keywords that indicate a weaker/smaller variant
_WEAK_NAME_MARKERS = [
    "instant", "mini", "tiny", "nano", "micro", "small", "lite",
    "1b", "2b", "3b", "4b", "7b", "8b", "9b",
]
_STRONG_NAME_MARKERS = [
    "pro", "ultra", "max", "super", "-large", "70b", "120b", "235b", "405b",
    "maverick", "scout", "flash", "coder", "thinking",
]


def score_model(model: dict) -> float:
    """Score a model for inclusion priority (higher = better).

    Scoring:
    - Context length: 40% (bigger = better, normalize to 1M)
    - Tool support: 30% (mandatory for agentic use)
    - Model quality: 20% (parameter size + name heuristics)
    - Multi-modal: 10% (nice to have)
    """
    score = 0.0
    mid = model.get("model", model.get("id", "")).lower()

    # ── Context length (40%) ──
    ctx = model.get("context_length", 0)
    score += 0.40 * min(ctx / 1_000_000, 1.0)

    # ── Tool support (30%) ──
    if model.get("tools"):
        score += 0.30

    # ── Model quality heuristic (20%) ──
    quality = 0.5  # baseline
    if any(m in mid for m in _STRONG_NAME_MARKERS):
        quality = 1.0
    if any(m in mid for m in _WEAK_NAME_MARKERS):
        quality = 0.2
    # Big parameter models (100B+) get top score
    import re
    param_match = re.search(r'(\d+)b', mid)
    if param_match:
        params = int(param_match.group(1))
        if params >= 100:
            quality = 1.0
        elif params >= 70:
            quality = 0.9
        elif params >= 30:
            quality = 0.7
        elif params >= 17:
            quality = 0.5
        elif params <= 8:
            quality = 0.2
    score += 0.20 * quality

    # ── Multi-modal bonus (10%) ──
    modality = model.get("modality", "")
    if "image" in modality or "video" in modality:
        score += 0.10

    return round(score, 3)


def select_best_per_provider(models: list[dict], max_per_provider: int = 3) -> dict:
    """Select the top N models per provider, scored and deduplicated."""
    by_provider = {}
    for m in models:
        p = m["provider"]
        by_provider.setdefault(p, []).append(m)

    result = {}
    for provider, plist in by_provider.items():
        # Score and sort
        for m in plist:
            m["_score"] = score_model(m)
        plist.sort(key=lambda x: x["_score"], reverse=True)

        # Deduplicate by model name stem (avoid gemini-2.0-flash + gemini-2.0-flash-001)
        seen_stems = set()
        selected = []
        for m in plist:
            stem = re.sub(r"[-.]001$|[-\.]preview$|[-]\d{4}$", "", m["model"].lower())
            stem = re.sub(r"[-_](latest|preview|lite)$", "", stem)
            if stem not in seen_stems:
                seen_stems.add(stem)
                selected.append(m)
            if len(selected) >= max_per_provider:
                break

        result[provider] = selected

    return result


# ---------------------------------------------------------------------------
# Config updaters
# ---------------------------------------------------------------------------

def load_yaml(path: Path) -> dict:
    """Load YAML file. Simple approach using PyYAML if available, else manual."""
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        log.error("PyYAML not installed. Run: pip3 install pyyaml")
        sys.exit(1)


def save_yaml(path: Path, data: dict) -> None:
    """Save YAML file atomically."""
    import yaml
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    tmp.replace(path)
    log.info("Saved %s", path)


def update_hermes_config(selected: dict, existing_yaml: dict) -> dict:
    """Update Hermes config.yaml with discovered free models.

    Strategy:
    - Enable providers that have keys but are disabled
    - Update model IDs to best available free models
    - Add new OpenRouter models as separate proxy entries
    - Keep the routing order sensible (biggest context first)
    """
    proxy = existing_yaml.setdefault("proxy", {})
    providers = proxy.setdefault("providers", {})
    routing = proxy.setdefault("routing", {})

    # ── Provider endpoint map ──
    # Maps provider config name → (api_base_url, env_key)
    PROVIDER_ENDPOINTS = {
        "google-alt": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                       "GOOGLE_FREE_API_KEY"),
        "google-alt-2": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                         "GOOGLE_FREE_API_KEY"),
        "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY"),
        "cerebras": ("https://api.cerebras.ai/v1/chat/completions", "CEREBRAS_API_KEY"),
        "sambanova": ("https://api.sambanova.ai/v1/chat/completions", "SAMBANOVA_API_KEY"),
        "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
        "owl": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
        "laguna": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
        "laguna-m1": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
        "qwen-coder": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
        "trinity": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
    }

    changes = []

    # ── Google: upgrade models ──
    google_models = selected.get("google", [])
    if google_models:
        for i, cfg_name in enumerate(["google-alt", "google-alt-2"]):
            if i < len(google_models):
                m = google_models[i]
                model_id = m["model"]
                old = providers.get(cfg_name, {}).get("model", "N/A")
                old_enabled = providers.get(cfg_name, {}).get("enabled", False)
                if old != model_id or not old_enabled:
                    changes.append(f"  Google {cfg_name}: {old} → {model_id} ({m['context_length']//1024}K ctx)")
                providers[cfg_name] = {
                    "daily_limit": 1500,
                    "enabled": True,
                    "model": model_id,
                }

    # ── Groq: enable with best model ──
    groq_models = selected.get("groq", [])
    if groq_models:
        best_groq = groq_models[0]
        old = providers.get("groq", {}).get("model", "N/A")
        new_model = best_groq["model"]
        # Only upgrade if new model is significantly bigger or better quality
        old_ctx = 131072  # current groq ctx
        new_ctx = best_groq.get("context_length", 0)
        if (old != new_model and new_ctx > old_ctx * 1.5) or not providers.get("groq", {}).get("enabled", False):
            changes.append(f"  Groq: {old} → {new_model} ({new_ctx//1024}K ctx)")
        providers["groq"] = {
            "daily_limit": 12000,
            "enabled": True,
            "model": new_model,
        }

    # ── Cerebras: enable (was disabled!) ──
    cerebras_models = selected.get("cerebras", [])
    if cerebras_models:
        best_cb = cerebras_models[0]
        old = providers.get("cerebras", {}).get("model", "N/A")
        new_model = best_cb["model"]
        was_enabled = providers.get("cerebras", {}).get("enabled", False)
        if not was_enabled:
            changes.append(f"  Cerebras: ENABLED (was disabled) → {new_model}")
            providers["cerebras"] = {
                "daily_limit": 50000,
                "enabled": True,
                "model": new_model,
            }
        providers["cerebras"] = {
            "daily_limit": 50000,
            "enabled": True,
            "model": new_model,
        }

    # ── SambaNova: enable (was disabled!) ──
    sambanova_models = selected.get("sambanova", [])
    if sambanova_models:
        best_sn = sambanova_models[0]
        old = providers.get("sambanova", {}).get("model", "N/A")
        new_model = best_sn["model"]
        was_enabled = providers.get("sambanova", {}).get("enabled", False)
        if not was_enabled:
            changes.append(f"  SambaNova: ENABLED (was disabled) → {new_model}")
            providers["sambanova"] = {
                "daily_limit": 50000,
                "enabled": True,
                "model": new_model,
            }
        providers["sambanova"] = {
            "daily_limit": 50000,
            "enabled": True,
            "model": new_model,
        }

    # ── OpenRouter: update existing + add new providers for top free models ──
    or_models = selected.get("openrouter", [])
    if or_models:
        # Update existing OpenRouter providers with best available free models
        or_config_names = ["owl", "laguna", "laguna-m1", "trinity", "qwen-coder", "openrouter"]
        used_models = set()

        for cfg_name in or_config_names:
            if not or_models:
                break
            # Find next OR model not already assigned
            m = None
            for candidate in or_models:
                if candidate["id"] not in used_models:
                    m = candidate
                    break
            if m is None:
                break
            used_models.add(m["id"])

            old_model = providers.get(cfg_name, {}).get("model", "N/A")
            new_model = m["id"]
            if old_model != new_model:
                changes.append(f"  OR {cfg_name}: {old_model} → {new_model} ({m['context_length']//1024}K ctx)")

            # Preserve existing daily_limit if present
            old_limit = providers.get(cfg_name, {}).get("daily_limit", 50000)

            providers[cfg_name] = {
                "daily_limit": old_limit,
                "enabled": True,
                "model": new_model,
            }

        # Add brand-new OpenRouter providers for free models not yet configured
        existing_or_models = {providers.get(n, {}).get("model", "") for n in or_config_names}
        new_or_idx = 0
        for m in or_models:
            if m["id"] not in existing_or_models and m["id"] not in used_models:
                new_or_idx += 1
                if new_or_idx > 3:  # Max 3 new providers per run
                    break
                safe_name = m["id"].split("/")[-1].replace(":", "-").replace(".", "-")[:30]
                cfg_name = f"or-{safe_name}"
                changes.append(f"  OR NEW provider '{cfg_name}': {m['id']} ({m['context_length']//1024}K ctx)")
                providers[cfg_name] = {
                    "daily_limit": 10000,
                    "enabled": True,
                    "model": m["id"],
                }

    # ── Update routing order: big-context models first ──
    def ctx_for_provider(name):
        m = providers.get(name, {}).get("model", "")
        # Look up context length from our discovered models
        for pmodels in selected.values():
            for pm in pmodels:
                if pm["model"] == m or pm["id"] == m:
                    return pm.get("context_length", 0)
        return 0

    all_cloud = [n for n in providers if providers[n].get("enabled") and n != "local"]
    sorted_by_ctx = sorted(all_cloud, key=lambda n: ctx_for_provider(n), reverse=True)

    for tier in ["low", "medium", "high", "critical"]:
        if tier in routing and isinstance(routing[tier], list):
            # Preserve the sort order but only include currently-enabled providers
            old_list = routing[tier]
            new_list = [n for n in sorted_by_ctx if n in providers and providers[n].get("enabled")]
            # Keep any that were in old list but might not be in discovery (manual additions)
            for n in old_list:
                if n not in new_list and n in providers and providers[n].get("enabled"):
                    new_list.append(n)
            if new_list != old_list:
                changes.append(f"  Routing [{tier}]: reorder by context ({len(new_list)} providers)")
            routing[tier] = new_list

    if changes:
        log.info("Config changes:\n%s", "\n".join(changes))
    else:
        log.info("No config changes needed — all up to date")

    existing_yaml["_config_version"] = (existing_yaml.get("_config_version", 0) + 1)
    return existing_yaml, changes


def update_models_json(selected: dict, existing: dict) -> tuple[dict, list[str]]:
    """Update CostGuard models.json with discovered free models."""
    changes = []
    free_models = existing.get("free_models", {})

    for provider, plist in selected.items():
        for m in plist[:3]:  # Top 3 per provider
            # Store the primary key (full id with provider prefix)
            model_key = m["id"]
            entry = {
                "provider": m["provider"],
                "context_length": m["context_length"],
                "tools": m.get("tools", True),
                "tool_choice": False,
                "tier": 1 if m["provider"] == "openrouter" else 2,
                "note": m.get("note", ""),
            }
            if model_key not in free_models:
                changes.append(f"  FREE: +{model_key}")
            free_models[model_key] = entry

            # Google: also store bare model name (e.g., "gemini-2.5-flash")
            # because tool_providers uses bare names and is_free() does exact match
            if m["provider"] == "google":
                bare_key = m["model"]  # e.g., "gemini-2.5-flash" (no "google/" prefix)
                if bare_key != model_key and bare_key not in free_models:
                    free_models[bare_key] = dict(entry)
                    changes.append(f"  FREE: +{bare_key} (bare name)")

            # Cerebras: also store bare model name (e.g., "gpt-oss-120b")
            if m["provider"] == "cerebras":
                bare_key = m["model"]
                if bare_key != model_key and bare_key not in free_models:
                    free_models[bare_key] = dict(entry)
                    changes.append(f"  FREE: +{bare_key} (bare name)")

            # SambaNova: also store bare model name
            if m["provider"] == "sambanova":
                bare_key = m["model"]
                if bare_key != model_key and bare_key not in free_models:
                    free_models[bare_key] = dict(entry)
                    changes.append(f"  FREE: +{bare_key} (bare name)")

    existing["free_models"] = free_models
    existing["updated"] = datetime.now().strftime("%Y-%m-%d")

    # ── Update hermes_proxy.tool_providers (used by CostGuard routing) ──
    hermes_proxy = existing.setdefault("hermes_proxy", {})
    tool_providers = hermes_proxy.get("tool_providers", [])

    # Config provider slot → which discovery provider to draw from
    # OpenRouter slots: distribute different OR models across them
    OPENROUTER_SLOTS = ["owl", "laguna", "laguna-m1", "qwen-coder", "openrouter"]
    GOOGLE_SLOTS = ["google-alt", "google-alt-2"]

    or_models = selected.get("openrouter", [])
    google_models = selected.get("google", [])

    def pick_or_model(slot_idx):
        """Pick a distinct OpenRouter model for each slot."""
        # Use slot_idx to pick different models for different slots
        if slot_idx < len(or_models):
            return or_models[slot_idx]["id"]
        return or_models[0]["id"] if or_models else "openrouter/owl-alpha"

    def pick_google_model(slot_idx):
        """Pick a distinct Google model for each slot.
        Returns model IDs that match free_models keys (no 'google/' prefix).
        """
        # Known good Google model IDs that exist in free_models
        known_google_free = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]
        # Filter to only those actually in our discovered models
        discovered_ids = {m["model"] for m in google_models}
        available = [m for m in known_google_free if m in discovered_ids]
        if not available:
            available = ["gemini-2.0-flash"]
        if slot_idx < len(available):
            return available[slot_idx]
        return available[0]

    new_tool_providers = []
    seen_names = set()
    or_idx = 0
    google_idx = 0

    for cfg_name in ["owl", "laguna", "laguna-m1", "qwen-coder", "openrouter",
                     "google-alt", "google-alt-2", "groq", "cerebras", "sambanova", "local"]:
        model_id = ""
        if cfg_name in OPENROUTER_SLOTS:
            model_id = pick_or_model(or_idx)
            or_idx += 1
        elif cfg_name in GOOGLE_SLOTS:
            model_id = pick_google_model(google_idx)
            google_idx += 1
        elif cfg_name == "groq":
            model_id = selected.get("groq", [{}])[0].get("id", "llama-3.3-70b-versatile")
        elif cfg_name == "cerebras":
            cerebras_models = selected.get("cerebras", [])
            model_id = cerebras_models[0]["model"] if cerebras_models else "gpt-oss-120b"
        elif cfg_name == "sambanova":
            sn_models = selected.get("sambanova", [])
            model_id = sn_models[0]["model"] if sn_models else "gpt-oss-120b"
        elif cfg_name == "local":
            model_id = "deepseek-moe-16b"

        if model_id and cfg_name not in seen_names:
            entry = {"name": cfg_name, "model": model_id}
            new_tool_providers.append(entry)
            seen_names.add(cfg_name)
            old_model = next((tp["model"] for tp in tool_providers if tp["name"] == cfg_name), "")
            if model_id != old_model:
                changes.append(f"  TOOL PROVIDER {cfg_name}: {old_model} → {model_id}")

    hermes_proxy["tool_providers"] = new_tool_providers

    return existing, changes


# ---------------------------------------------------------------------------
# Proxy reload
# ---------------------------------------------------------------------------

def restart_proxy():
    """Restart the proxy server via supervisorctl or systemd."""
    # Try supervisorctl first
    result = subprocess.run(
        ["supervisorctl", "restart", "llm-proxy"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode == 0:
        log.info("Proxy restarted via supervisorctl")
        return True

    # Try systemd
    result = subprocess.run(
        ["sudo", "/usr/bin/systemctl", "restart", "llm-proxy"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode == 0:
        log.info("Proxy restarted via systemd")
        return True

    # Try direct process signal
    result = subprocess.run(
        ["pkill", "-f", "proxy_server"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0:
        log.info("Proxy process killed (should be auto-restarted)")
        return True

    log.error("Failed to restart proxy — no supervisorctl, systemd service, or process found")
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Discover free LLM models across providers")
    parser.add_argument("--update", action="store_true", help="Update config.yaml and models.json")
    parser.add_argument("--reload", action="store_true", help="Restart proxy after update")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON only")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    keys = _load_keys()

    # ── Phase 1: Discover from all providers ──
    print("🔍 Discovering free models across all providers...\n")

    all_models = []
    discoverers = [
        ("Google", discover_google),
        ("Groq", discover_groq),
        ("Cerebras", discover_cerebras),
        ("SambaNova", discover_sambanova),
        ("OpenRouter", discover_openrouter),
    ]

    for name, fn in discoverers:
        try:
            models = fn(keys)
            all_models.extend(models)
            print(f"  {name:15s}: {len(models):2d} free models")
        except Exception as exc:
            log.error("%s discovery failed: %s", name, exc)
            print(f"  {name:15s}: ERROR ({exc})")

    print(f"\n  Total: {len(all_models)} free models found")

    if not all_models:
        print("❌ No free models found — check your API keys")
        sys.exit(1)

    # ── Phase 2: Score and select ──
    selected = select_best_per_provider(all_models, max_per_provider=3)

    # ── Phase 3: Output ──
    if args.json:
        output = {
            "timestamp": datetime.utcnow().isoformat(),
            "total_found": len(all_models),
            "selected": {
                p: [{"id": m["id"], "ctx": m["context_length"], "score": m["_score"]} for m in ms]
                for p, ms in selected.items()
            },
            "all_models": [{"id": m["id"], "provider": m["provider"], "ctx": m["context_length"]} for m in all_models],
        }
        print(json.dumps(output, indent=2))
        return

    # Human-readable summary
    print("\n📋 Top free models per provider:")
    print("-" * 80)
    for provider, plist in selected.items():
        print(f"\n  [{provider.upper()}]")
        for m in plist:
            ctx_str = f"{m['context_length']//1024}K" if m['context_length'] >= 1024 else str(m['context_length'])
            print(f"    {m['id']:60s} ctx={ctx_str:>6s}  score={m['_score']}")

    # ── Phase 4: Update configs (if requested) ──
    if args.update or args.dry_run:
        print("\n⚙️  Checking config updates...\n")

        # Update Hermes config.yaml
        hermes_cfg = load_yaml(HERMES_CONFIG)
        hermes_cfg, cfg_changes = update_hermes_config(selected, hermes_cfg)

        # Update CostGuard models.json
        models_data = json.load(open(MODELS_JSON))
        models_data, json_changes = update_models_json(selected, models_data)

        if cfg_changes:
            print("  Config.yaml changes:")
            for c in cfg_changes:
                print(c)
        else:
            print("  config.yaml: no changes needed ✓")

        if json_changes:
            print("\n  models.json changes:")
            for c in json_changes:
                print(c)
        else:
            print("  models.json: no changes needed ✓")

        if args.dry_run:
            print("\n  (dry-run — no files modified)")
        elif cfg_changes or json_changes:
            save_yaml(HERMES_CONFIG, hermes_cfg)

            # Atomic write for models.json
            tmp = MODELS_JSON.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(models_data, f, indent=4, ensure_ascii=False)
            tmp.replace(MODELS_JSON)
            print(f"\n  ✅ Saved {HERMES_CONFIG}")
            print(f"  ✅ Saved {MODELS_JSON}")

            if args.reload:
                print("\n  🔄 Restarting proxy...")
                time.sleep(2)
                if restart_proxy():
                    print("  ✅ Proxy restarted")
                    time.sleep(5)
                else:
                    print("  ⚠️  Proxy restart failed — restart manually")
        else:
            print("\n  ✅ All configs already up to date")


if __name__ == "__main__":
    main()
