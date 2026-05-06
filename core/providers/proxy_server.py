"""LLM Proxy Server — OpenAI-compatible API that routes through AgentHarness.

Sits on port 8080 and routes requests to the best available provider
(local Gemma 4, Groq, Google, Cerebras, SambaNova, OpenRouter).

Chaguli and any other client just calls http://localhost:8080/v1/chat/completions
and gets routed automatically.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
import hashlib
from collections import OrderedDict
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response cache — avoids redundant LLM calls for identical requests.
# Keyed on hash(model + system_prompt + last_user_message + tools).
# Short TTL (120s) since LLM responses are contextual.  Primarily helps
# with Hermes monitoring queries that repeat every 15 min with the same
# system prompt.
# ---------------------------------------------------------------------------
class _ResponseCache:
    """Thread-safe LRU response cache with TTL."""

    def __init__(self, maxsize: int = 128, ttl: float = 120.0):
        self._cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl
        self.hits = 0
        self.misses = 0

    def _make_key(self, body: dict) -> str:
        """Hash the stable parts of a request for cache lookup."""
        # Extract stable parts: system prompt, tools, and last user message
        parts = []

        # System prompt (from messages or top-level system field)
        messages = body.get("messages", [])
        system = body.get("system", "")
        if system:
            parts.append(f"sys:{system[:500]}")
        for m in messages:
            if m.get("role") == "system":
                parts.append(f"sys:{str(m.get('content', ''))[:500]}")
                break

        # Last user message
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, list):
                    # Anthropic format: extract text blocks
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif isinstance(block, str):
                            text_parts.append(block)
                    content = " ".join(text_parts)
                parts.append(f"user:{str(content)[:500]}")
                break

        # Tool names (not full definitions — too verbose)
        tools = body.get("tools", [])
        if tools:
            tool_names = sorted(t.get("name", t.get("function", {}).get("name", "")) for t in tools)
            parts.append(f"tools:{','.join(tool_names)}")

        # Model
        parts.append(f"model:{body.get('model', '')}")

        # Message count (to differentiate conversation turns)
        parts.append(f"n:{len(messages)}")

        key_str = "|".join(parts)
        return hashlib.sha256(key_str.encode()).hexdigest()[:16]

    def get(self, body: dict) -> dict | None:
        key = self._make_key(body)
        if key in self._cache:
            ts, data = self._cache[key]
            if time.monotonic() - ts < self._ttl:
                self._cache.move_to_end(key)
                self.hits += 1
                return data
            else:
                del self._cache[key]
        self.misses += 1
        return None

    def put(self, body: dict, response: dict) -> None:
        key = self._make_key(body)
        self._cache[key] = (time.monotonic(), response)
        self._cache.move_to_end(key)
        while len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": f"{self.hits / total * 100:.1f}%" if total > 0 else "0%",
            "entries": len(self._cache),
            "maxsize": self._maxsize,
            "ttl_seconds": self._ttl,
        }


_response_cache = _ResponseCache(maxsize=256, ttl=120.0)

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, StreamingResponse
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


def _json_to_sse(data: dict) -> StreamingResponse:
    """Convert a non-streamed chat completion into SSE chunks.

    Clients that send stream=True expect Server-Sent Events.  This wraps
    a completed response into the standard OpenAI SSE format:
      data: {chunk}\n\n  ...  data: [DONE]\n\n
    """
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message", {})
    content = message.get("content") or ""
    tool_calls = message.get("tool_calls")
    finish_reason = choice.get("finish_reason", "stop")

    base = {
        "id": data.get("id", f"chatcmpl-ah-{int(time.time())}"),
        "object": "chat.completion.chunk",
        "created": data.get("created", int(time.time())),
        "model": data.get("model", "agentharness-proxy"),
    }

    def _generate():
        # Role chunk
        yield "data: " + json.dumps({
            **base,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }) + "\n\n"

        # Content chunk(s) or tool_calls
        if tool_calls:
            for tc in tool_calls:
                yield "data: " + json.dumps({
                    **base,
                    "choices": [{"index": 0, "delta": {
                        "tool_calls": [tc],
                    }, "finish_reason": None}],
                }) + "\n\n"
        elif content:
            yield "data: " + json.dumps({
                **base,
                "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
            }) + "\n\n"

        # Usage chunk (if present)
        usage = data.get("usage")
        if usage:
            yield "data: " + json.dumps({
                **base,
                "choices": [],
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
            }) + "\n\n"

        # Finish chunk
        yield "data: " + json.dumps({
            **base,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")



# -- Model footer helper --------------------------------------------------
def _append_model_footer(data: dict, provider: str, model: str = "") -> dict:
    """Append a small footer identifying the LLM model/provider to the
    assistant message content. Skips tool_calls-only messages."""
    try:
        choices = data.get("choices", [])
        if not choices:
            return data
        
        msg = choices[0].get("message", {})
        text = (msg.get("content") or "").strip()
        
        # Skip footer for JSON responses (internal agent communication like JSON plans)
        if text.startswith("{") and text.endswith("}"):
            return data
            
        # Skip if the message is a tool call with no text content
        if not text and msg.get("tool_calls"):
            return data
            
        if not text:
            return data

        # Format footer
        use_model = model or "model"
        footer = f"\n\n— via {provider} · {use_model}"
        
        # Avoid duplicate footers
        if footer in text:
            return data
            
        msg["content"] = text + footer
        return data
    except Exception as e:
        log.error("Error appending footer: %r", e)
        return data

def create_proxy_app(data_dir: str = "") -> object:
    """Create the LLM proxy FastAPI app."""
    if not HAS_FASTAPI:
        raise ImportError("FastAPI not installed. Run: pip install fastapi uvicorn")

    data_dir = data_dir or os.environ.get("AH_DATA_DIR", ".")

    app = FastAPI(title="AgentHarness LLM Proxy")

    # Lazy-init router on first request
    _router_cache = {}

    # Rate-limit cooldown: provider_name -> timestamp when cooldown expires.
    # When a provider returns 429, we skip it for _COOLDOWN_SECONDS to avoid
    # burning through all providers on rapid-fire auxiliary requests.
    _rate_cooldowns: dict[str, float] = {}
    _COOLDOWN_SECONDS = 60  # initial cooldown after a 429
    _MAX_COOLDOWN_SECONDS = 3600  # max cooldown (1 hour) after repeated 429s
    _cooldown_hits: dict[str, int] = {}  # provider -> consecutive 429 count

    # Permanently disabled providers (e.g. 402 no credits).
    _disabled_providers: set[str] = set()

    # Local LLM health tracking — use a mutable dict so nested functions
    # can update without nonlocal.
    _local_health: dict[str, Any] = {
        "healthy": True,
        "last_check": 0.0,
        "restart_count": 0,
        "last_restart": 0.0,
    }
    _LOCAL_HEALTH_INTERVAL = 30  # seconds between health checks
    _LOCAL_RESTART_COOLDOWN = 120  # min seconds between restart attempts

    async def _check_local_health(local_url: str) -> bool:
        """Quick health ping to local LLM. Returns True if responsive."""
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{local_url}/", timeout=5.0)
                healthy = resp.status_code == 200
                _local_health["healthy"] = healthy
                _local_health["last_check"] = time.monotonic()
                return healthy
        except Exception:
            _local_health["healthy"] = False
            _local_health["last_check"] = time.monotonic()
            return False

    async def _restart_local_llm() -> bool:
        """Attempt to restart the local LLM via systemctl (sudoers-allowed)."""
        now = time.monotonic()
        if now - _local_health["last_restart"] < _LOCAL_RESTART_COOLDOWN:
            log.info("Local LLM restart skipped (cooldown, %ds left)",
                     int(_LOCAL_RESTART_COOLDOWN - (now - _local_health["last_restart"])))
            return False

        _local_health["last_restart"] = now
        _local_health["restart_count"] += 1
        log.warning("Local LLM unresponsive — attempting auto-restart (#%d)",
                    _local_health["restart_count"])

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["sudo", "/usr/bin/systemctl", "restart", "llama-primary"],
                    capture_output=True, text=True, timeout=30,
                ),
            )
            if result.returncode == 0:
                log.info("Local LLM restart issued, waiting for startup...")
                await asyncio.sleep(10)
                local_url = os.environ.get("LOCAL_LLM_URL", "http://localhost:8081")
                healthy = await _check_local_health(local_url)
                log.info("Local LLM post-restart health: %s", "OK" if healthy else "STILL DOWN")
                return healthy
            else:
                log.error("Local LLM restart failed: %s", result.stderr.strip())
                return False
        except Exception as exc:
            log.error("Local LLM restart error: %s", exc)
            return False

    def _sanitize_tools_for_local(tools: list) -> list:
        """Sanitize tool definitions for ik-llama-server's jinja mode.

        The local LLM's --jinja template parser chokes on tool descriptions
        that contain embedded double quotes (they break JSON parsing inside
        the Jinja template). Replace inner double quotes with single quotes.
        """
        sanitized = []
        for tool in tools:
            tool = json.loads(json.dumps(tool))  # deep copy
            fn = tool.get("function", {})
            desc = fn.get("description", "")
            if desc:
                # Replace embedded double quotes with single quotes
                fn["description"] = desc.replace('"', "'")
            # Also sanitize parameter descriptions
            params = fn.get("parameters", {})
            for prop_name, prop_val in params.get("properties", {}).items():
                if isinstance(prop_val, dict) and "description" in prop_val:
                    prop_val["description"] = prop_val["description"].replace('"', "'")
            sanitized.append(tool)
        return sanitized

    def _get_router():
        if "router" in _router_cache:
            return _router_cache["router"]

        from core.providers.budget import BudgetTracker
        from core.providers.router import Router
        from core.providers.llamacpp import LlamaCppProvider
        from core.providers.groq import GroqProvider
        from core.providers.google import GoogleProvider
        from core.providers.cerebras import CerebrasProvider
        from core.providers.sambanova import SambaNovaProvider
        from core.providers.openrouter import OpenRouterProvider
        from core.providers.ollama_cloud import OllamaCloudProvider
        from core.providers.openai_compat import OpenAICompatProvider

        bt = BudgetTracker(data_dir=data_dir)
        providers = []
        
        # OpenRouter Models (Prioritized as requested)
        if os.environ.get("OPENROUTER_API_KEY"):
            # 1. Owl-Alpha (Hermes 3 405B) - Paid tier
            providers.append(OpenRouterProvider(
                model=os.environ.get("OWL_MODEL", "nousresearch/hermes-3-llama-3.1-405b"),
                name="owl",
                daily_limit=50000
            ))
            # 2. Laguna-M.1 - Free tier/preferred
            providers.append(OpenRouterProvider(
                model=os.environ.get("LAGUNA_MODEL", "poolside/laguna-m.1:free"),
                name="laguna",
                daily_limit=5000
            ))
            # 3. Generic OpenRouter (will use default or free models)
            providers.append(OpenRouterProvider(
                name="openrouter",
                daily_limit=10000
            ))

        # Cloud free tiers / high performance
        if os.environ.get("GOOGLE_API_KEY"):
            providers.append(GoogleProvider(
                model="gemini-2.0-flash",
                name="google-alt",
                daily_limit=1500
            ))
        if os.environ.get("GROQ_API_KEY"):
            providers.append(GroqProvider())
        if os.environ.get("CEREBRAS_API_KEY"):
            providers.append(CerebrasProvider())
        if os.environ.get("SAMBANOVA_API_KEY"):
            providers.append(SambaNovaProvider())

        # Local LLM (Disaster recovery only — too slow for main loop)
        local = LlamaCppProvider(
            name="local",
            model="qwen2.5:14b",
            timeout=300,
            endpoint=os.environ.get("LOCAL_LLM_URL", "http://localhost:8081"),
        )
        providers.append(local)

        provider_names = [p.name for p in providers]
        log.info(f"LLM Proxy initialized with providers: {provider_names}")

        # Routing Table (Prioritizing Paid/Credit models first)
        tier_order = ["owl", "laguna", "google-alt", "groq", "cerebras", "sambanova", "local"]
        
        router = Router(
            providers=providers,
            budget=bt,
            routing={
                "low": tier_order,
                "medium": tier_order,
                "high": tier_order,
                "critical": tier_order,
            },
        )
        _router_cache["router"] = router
        _router_cache["budget"] = bt
        return router
    @app.get("/health")
    async def health():
        local_url = os.environ.get("LOCAL_LLM_URL", "http://localhost:8081")
        local_ok = await _check_local_health(local_url)
        status = "ok" if local_ok else "degraded"
        return JSONResponse({
            "status": status,
            "type": "agentharness_proxy",
            "local_llm": "healthy" if local_ok else "unresponsive",
        })

    @app.get("/v1/status")
    async def provider_status():
        """Dashboard endpoint — shows all providers, health, cooldowns, rate limits."""
        router = _get_router()
        budget = _router_cache.get("budget")
        local_url = os.environ.get("LOCAL_LLM_URL", "http://localhost:8081")
        local_ok = await _check_local_health(local_url)

        now = time.monotonic()
        providers_status = {}

        # Local LLM
        providers_status["local"] = {
            "type": "local",
            "healthy": local_ok,
            "model": "gemma-4-26B-A4B-it-Q4_K_M",
            "endpoint": local_url,
            "restarts": _local_health["restart_count"],
        }

        # Cloud providers
        for pname, url, env_key, default_model in _TOOL_PROVIDERS:
            has_key = bool(os.environ.get(env_key, ""))
            cooldown_until = _rate_cooldowns.get(pname, 0)
            in_cooldown = now < cooldown_until
            cooldown_remaining = max(0, int(cooldown_until - now)) if in_cooldown else 0
            disabled = pname in _disabled_providers

            status = "ready"
            if not has_key:
                status = "no_api_key"
            elif disabled:
                status = "disabled_402"
            elif in_cooldown:
                status = f"rate_limited ({cooldown_remaining}s)"

            providers_status[pname] = {
                "type": "cloud",
                "status": status,
                "model": default_model,
                "has_api_key": has_key,
                "in_cooldown": in_cooldown,
                "cooldown_seconds": cooldown_remaining,
                "disabled": disabled,
            }

        # Usage stats
        usage_data = {}
        if budget:
            usage_data = budget._data.get("providers", {})

        return JSONResponse({
            "timestamp": int(time.time()),
            "overall": "healthy" if local_ok else "degraded",
            "providers": providers_status,
            "usage_today": usage_data,
            "disabled": list(_disabled_providers),
        })

    @app.get("/v1/models")
    def models():
        return JSONResponse({
            "object": "list",
            "data": [{"id": "agentharness-proxy", "object": "model", "owned_by": "agentharness"}],
        })

    @app.get("/v1/usage")
    def usage():
        """Return today's per-provider LLM usage stats."""
        router = _get_router()
        budget = _router_cache.get("budget")
        if budget is None:
            return JSONResponse({"error": "Budget tracker not initialized"}, status_code=503)

        report = budget.daily_report()
        data = budget._data
        return JSONResponse({
            "date": data.get("date", "unknown"),
            "providers": data.get("providers", {}),
            "report": report,
            "cooldowns": {
                p: int(t - time.monotonic())
                for p, t in _rate_cooldowns.items()
                if t > time.monotonic()
            },
            "disabled": list(_disabled_providers),
        })

    @app.get("/v1/billing")
    def billing():
        """Return billing report with cost tracking."""
        budget = _router_cache.get("budget")
        if budget is None:
            return JSONResponse({"error": "Budget tracker not initialized"}, status_code=503)

        try:
            from core.providers.billing import BillingTracker
            data_dir = os.environ.get("AH_DATA_DIR", "data")
            bt = BillingTracker(data_dir)
            # Sync from budget tracker
            bt.update_from_budget(budget._data)
            report = bt.get_billing_report()
            return JSONResponse(report)
        except ImportError:
            return JSONResponse({"error": "Billing module not available"}, status_code=503)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/v1/cost")
    async def cost_summary():
        """Cost summary for Telegram /cost command."""
        import httpx
        router = _get_router()
        budget = _router_cache.get("budget")
        local_url = os.environ.get("LOCAL_LLM_URL", "http://localhost:8081")
        local_ok = await _check_local_health(local_url)
        now = time.monotonic()

        openrouter_credit = None
        or_key = os.environ.get("OPENROUTER_API_KEY")
        if or_key:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        "https://openrouter.ai/api/v1/auth/key",
                        headers={"Authorization": f"Bearer {or_key}"},
                        timeout=5.0
                    )
                    if resp.status_code == 200:
                        openrouter_credit = resp.json().get("data", {})
            except Exception as e:
                log.error("Failed to fetch OpenRouter credit: %s", e)

        # Provider status
        providers = {}
        for pname, url, env_key, default_model in _TOOL_PROVIDERS:
            has_key = bool(os.environ.get(env_key, ""))
            cooldown_until = _rate_cooldowns.get(pname, 0)
            in_cooldown = now < cooldown_until
            cooldown_left = max(0, int(cooldown_until - now)) if in_cooldown else 0
            disabled = pname in _disabled_providers
            hits = _cooldown_hits.get(pname, 0)

            status = "ready"
            if not has_key:
                status = "no_key"
            elif disabled:
                status = "disabled"
            elif in_cooldown:
                status = f"cooldown_{cooldown_left}s"

            providers[pname] = {
                "status": status,
                "model": default_model,
                "cooldown_seconds": cooldown_left,
                "consecutive_429s": hits,
            }

        providers["local"] = {
            "status": "healthy" if local_ok else "down",
            "model": "Qwen3.5-9B",
            "cooldown_seconds": 0,
            "consecutive_429s": 0,
        }

        # Usage today
        usage = {}
        if budget:
            usage = budget._data.get("providers", {})

        # Routing order
        routing_order = {
            "plain_chat": ["local", "groq", "cerebras", "sambanova", "openrouter", "google-alt", "qwen-coder"],
            "tool_calling": [p[0] for p in _TOOL_PROVIDERS],
        }

        return JSONResponse({
            "timestamp": int(time.time()),
            "providers": providers,
            "usage_today": usage,
            "routing_order": routing_order,
            "disabled": list(_disabled_providers),
            "openrouter_credit": openrouter_credit,
        })

    @app.post("/v1/routing")
    async def update_routing(request: Request):
        """Runtime routing control — switch order from Telegram."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        action = body.get("action", "")

        if action == "reset_cooldowns":
            _rate_cooldowns.clear()
            _cooldown_hits.clear()
            _disabled_providers.clear()
            return JSONResponse({"success": True, "message": "All cooldowns and disables cleared"})

        if action == "disable_provider":
            provider = body.get("provider", "")
            if provider:
                _disabled_providers.add(provider)
                return JSONResponse({"success": True, "message": f"{provider} disabled"})

        if action == "enable_provider":
            provider = body.get("provider", "")
            _disabled_providers.discard(provider)
            _rate_cooldowns.pop(provider, None)
            _cooldown_hits.pop(provider, None)
            return JSONResponse({"success": True, "message": f"{provider} enabled"})

        return JSONResponse({"error": f"Unknown action: {action}"}, status_code=400)


    @app.get("/v1/cap")
    async def get_caps():
        """View provider daily caps."""
        router = _get_router()
        caps = {}
        for name, provider in router._providers_by_name.items():
            limit = getattr(provider, 'daily_limit', None)
            usage = getattr(provider, '_usage_today', 0)
            if limit is not None:
                caps[name] = {"daily_limit": limit, "used_today": usage, "remaining": max(0, limit - usage)}
        return JSONResponse({"caps": caps})

    @app.post("/v1/cap")
    async def set_cap(request: Request):
        """Set provider daily cap at runtime."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        provider_name = body.get("provider", "").strip().lower()
        new_limit = body.get("limit")

        if not provider_name or new_limit is None:
            return JSONResponse({"error": "Required: provider, limit"}, status_code=400)

        try:
            new_limit = int(new_limit)
        except (TypeError, ValueError):
            return JSONResponse({"error": "limit must be an integer"}, status_code=400)

        if new_limit < 0 or new_limit > 100000:
            return JSONResponse({"error": "limit must be between 0 and 100000"}, status_code=400)

        router = _get_router()
        provider = router._providers_by_name.get(provider_name)
        if provider is None:
            valid = list(router._providers_by_name.keys())
            return JSONResponse({"error": f"Unknown provider: {provider_name}. Valid: {valid}"}, status_code=400)

        old_limit = getattr(provider, 'daily_limit', None)
        if old_limit is None:
            return JSONResponse({"error": f"{provider_name} does not have a daily_limit"}, status_code=400)

        provider.daily_limit = new_limit
        return JSONResponse({
            "success": True,
            "provider": provider_name,
            "old_limit": old_limit,
            "new_limit": new_limit,
        })

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        """OpenAI-compatible chat completions — routed through AgentHarness."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": {"message": "Invalid JSON"}}, status_code=400)

        # Cognitive tier hint from Hermes orchestrator
        cognitive_tier = body.pop("cognitive_tier", None)  # pop so upstream providers don't see it
        if cognitive_tier:
            log.info("Cognitive tier hint: %s", cognitive_tier)


        # --- Orchestrator Workflow ---
        # DISABLED: Hermes now has its own orchestrator implementation
        # This proxy-only orchestrator conflicts with the Hermes orchestrator
        # and causes expensive API calls to OpenRouter.
        #
        # last_user_message = ""
        messages = body.get("messages", [])
        # for m in reversed(messages):
        #     if m.get("role") == "user":
        #         last_user_message = m.get("content", "")
        #         break
        #
        # _TASK_KEYWORDS = {"fix", "debug", "investigate", "resolve", "troubleshoot"}
        # if any(w in last_user_message.lower() for w in _TASK_KEYWORDS):
        #     log.info("Task-oriented prompt detected, starting orchestrator workflow")
        #     try:
        #         result = await _orchestrator_workflow(body)
        #         # The orchestrator handles its own formatting and response
        #         return result
        #     except Exception as e:
        #         log.error(f"Orchestrator workflow failed: {e}")
        #         # Fallback to standard routing if orchestrator fails
        #         pass

        max_tokens = body.get("max_tokens", 1024)
        temperature = body.get("temperature", 0.7)
        tools = body.get("tools")
        tool_choice = body.get("tool_choice")
        stream_requested = body.get("stream", False)

        # --- Response cache check ---
        # Skip cache for streaming requests (can't cache SSE generators)
        if not stream_requested and temperature <= 0.3:
            cached = _response_cache.get(body)
            if cached is not None:
                log.info("Response cache HIT (hits=%d, rate=%s)",
                         _response_cache.hits, _response_cache.stats()["hit_rate"])
                if stream_requested:
                    return _json_to_sse(cached)
                return JSONResponse(cached)

        # If tools are present, use passthrough mode — forward the full
        # OpenAI-compatible request directly to cloud providers that support
        # tool calling.  The local LLM does not support tools, so it is
        # excluded from the candidate list.
        #
        # However, free-tier Llama models are too eager with tool-calling:
        # they call tools even for simple conversational messages.  As a
        # workaround we detect "chat-only" turns (short user message with
        # no action keywords) and strip tools so the model just responds.
        if tools:
            last_user = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    last_user = m.get("content", "")
                    break
            _ACTION_WORDS = {
                "search", "browse", "navigate", "open", "go to",
                "read", "write", "create", "delete", "run", "execute",
                "find", "look up", "fetch", "download", "install",
                "save", "remember", "memorize", "skill",
                "check", "monitor", "log", "mission", "incident", "evaluate", "eval", "scan",
            }
            is_chat = (
                len(last_user.split()) < 30
                and not any(w in last_user.lower() for w in _ACTION_WORDS)
            )
            if is_chat:
                log.info("Chat-only turn detected, stripping tools for Llama compatibility")
                resp = await _tool_call_passthrough(
                    body, messages, max_tokens, temperature, [], None,
                )
                if stream_requested and isinstance(resp, JSONResponse):
                    return _json_to_sse(json.loads(resp.body.decode()))
                return resp

        if tools:
            resp = await _tool_call_passthrough(
                body, messages, max_tokens, temperature, tools, tool_choice,
                cognitive_tier=cognitive_tier,  # ADD THIS PARAMETER
            )
            if stream_requested and isinstance(resp, JSONResponse):
                return _json_to_sse(json.loads(resp.body.decode()))
            return resp

        # --- Plain text completion (existing path) ---

        # Extract the user prompt
        prompt_parts = []
        system_prompt = None
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            # Strip model footers from history
            if content and '— via ' in content:
                idx = content.rfind('\n\n— via ')
                if idx >= 0:
                    content = content[:idx]
            if role == "system":
                system_prompt = content
            elif role == "user":
                prompt_parts.append(content)

        prompt = "\n".join(prompt_parts) if prompt_parts else ""
        if not prompt:
            return JSONResponse({"error": {"message": "No user message"}}, status_code=400)

        # Determine complexity from prompt length and context
        from core.providers.base import Complexity, LLMRequest

        # Tier override from cognitive classifier
        if str(cognitive_tier).upper().strip() in ["REASON", "PLAN_NEEDED"]: 
            complexity = Complexity.HIGH  # forces routing to stronger model
        elif cognitive_tier == "CHAT":
            complexity = Complexity.LOW   # forces routing to local/free
        else:
            # Original complexity logic (EXECUTE or no tier hint)
            token_estimate = len(prompt.split())
            if token_estimate < 5:
                complexity = Complexity.LOW
            elif token_estimate < 100:
                complexity = Complexity.MEDIUM
            else:
                complexity = Complexity.HIGH

        router = _get_router()
        llm_request = LLMRequest(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            complexity=complexity,
            system_prompt=system_prompt,
        )

        start = time.monotonic()
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, router.route, llm_request)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        if not response.success:
            return JSONResponse(
                {"error": {"message": f"All providers failed: {response.error}"}},
                status_code=503,
            )

        # Format as OpenAI response
        result = {
            "id": f"chatcmpl-ah-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": f"agentharness-proxy ({response.provider})",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": response.text},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": response.tokens_in,
                "completion_tokens": response.tokens_out,
                "total_tokens": response.total_tokens,
            },
            "timings": {
                "provider": response.provider,
                "latency_ms": elapsed_ms,
            },
        }
        result = _append_model_footer(result, response.provider, response.model)

        # Cache plain-chat responses (low temperature only)
        if not stream_requested and body.get("temperature", 0.7) <= 0.3:
            _response_cache.put(body, result)

        if stream_requested:
            return _json_to_sse(result)
        return JSONResponse(result)

    # -- Tool calling passthrough ------------------------------------------------
    # Cloud providers that support OpenAI-compatible tool calling, in priority
    # order.  Each entry is (provider_name, base_url, env_key_for_api_key, default_model).
    #
    # Models are configurable via env vars: {PROVIDER}_TOOL_MODEL overrides the
    # default.  e.g. GOOGLE_TOOL_MODEL=gemini-2.5-pro to use a different model.
    # This avoids hardcoding models that change with free-tier rotations.
    _TOOL_PROVIDER_DEFAULTS = {
        "groq": "qwen/qwen3-coder:free",
        "cerebras": "qwen-3-235b-a22b-instruct-2507",
        "sambanova": "qwen/qwen3-coder:free",
        "laguna": "qwen/qwen3-coder:free",
        "openrouter": "qwen/qwen3-coder:free",
        "qwen-coder": "qwen/qwen3-coder:free",
        "fireworks": "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "google-alt": "gemini-2.0-flash",
    }
    # Routing order: groq first (fast+free), google-alt second (free Gemini,
    # reliable tool-calling), then free-tier Llama fallbacks.
    # cerebras/sambanova Llama models often return empty after tool calls,
    # so they come after Google.
    _TOOL_PROVIDERS = [
        ("owl", "https://openrouter.ai/api/v1/chat/completions",
         "OPENROUTER_API_KEY", os.environ.get("OWL_TOOL_MODEL", "openrouter/owl-alpha")),
        ("laguna", "https://openrouter.ai/api/v1/chat/completions",
         "OPENROUTER_API_KEY", os.environ.get("LAGUNA_TOOL_MODEL", _TOOL_PROVIDER_DEFAULTS["laguna"])),
        ("groq", "https://api.groq.com/openai/v1/chat/completions",
         "GROQ_API_KEY", os.environ.get("GROQ_TOOL_MODEL", _TOOL_PROVIDER_DEFAULTS["groq"])),
        ("google-alt", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
         "GOOGLE_FREE_API_KEY", os.environ.get("GOOGLE_FREE_TOOL_MODEL", _TOOL_PROVIDER_DEFAULTS["google-alt"])),
        ("cerebras", "https://api.cerebras.ai/v1/chat/completions",
         "CEREBRAS_API_KEY", os.environ.get("CEREBRAS_TOOL_MODEL", _TOOL_PROVIDER_DEFAULTS["cerebras"])),
        ("sambanova", "https://api.sambanova.ai/v1/chat/completions",
         "SAMBANOVA_API_KEY", os.environ.get("SAMBANOVA_TOOL_MODEL", _TOOL_PROVIDER_DEFAULTS["sambanova"])),
        ("fireworks", "https://api.fireworks.ai/inference/v1/chat/completions",
         "FIREWORKS_API_KEY", os.environ.get("FIREWORKS_TOOL_MODEL", _TOOL_PROVIDER_DEFAULTS["fireworks"])),
        ("qwen-coder", "https://openrouter.ai/api/v1/chat/completions",
         "OPENROUTER_API_KEY", os.environ.get("HAQUI_MODEL", "qwen/qwen3-coder:free")),
        ("openrouter", "https://openrouter.ai/api/v1/chat/completions",
         "OPENROUTER_API_KEY", os.environ.get("REASON_MODEL", _TOOL_PROVIDER_DEFAULTS["openrouter"])),
    ]

    # Max tools to forward — free-tier Llama models degrade with too many
    # tool definitions and start calling tools randomly instead of chatting.
    # Llama 3.3 70B works reliably with ~6 tools; above that it starts
    # calling tools for simple chat messages.
    _MAX_TOOLS_PASSTHROUGH = 15

    def _is_valid_response(data: dict, had_tools: bool) -> bool:
        """Check if response is usable or needs escalation to a better model."""
        if not data or not data.get("choices"):
            return False
        msg = data["choices"][0].get("message", {})
        content = (msg.get("content") or "").strip()
        tool_calls = msg.get("tool_calls") or []

        # Empty response
        if not content and not tool_calls:
            return False

        # Tools were in the request but model rendered them as markdown
        # instead of proper function calls
        if had_tools and not tool_calls:
            if "```bash" in content or "```python" in content:
                return False
            if "terminal --tool_code" in content or "claudemem_" in content:
                return False

        return True

    async def _tool_call_passthrough(
        body: dict,
        messages: list,
        max_tokens: int,
        temperature: float,
        tools: list,
        tool_choice: Any | None,
        cognitive_tier: str | None = None,
    ) -> JSONResponse:
        """Forward tool-calling requests, trying local LLM first for simple calls.

        Cloud providers are tried first (faster, higher quality).  If all
        cloud providers fail, the local Gemma 4 26B-A4B is used as a
        last-resort fallback — it supports tool calling via --jinja mode.
        """
        import httpx

        router = _get_router()
        budget = _router_cache.get("budget")

        # Cap tools to avoid overwhelming free-tier models.  Prioritise
        # core tools over browser tools (which Llama can't use well anyway).
        _PRIORITY_PREFIXES = ("mission", "get_recent_incidents", "terminal", "file", "web_search", "memory", "skill", "session")
        if len(tools) > _MAX_TOOLS_PASSTHROUGH:
            priority = [t for t in tools if any(
                t.get("function", {}).get("name", "").startswith(p) for p in _PRIORITY_PREFIXES
            )]
            rest = [t for t in tools if t not in priority]
            capped_tools = (priority + rest)[:_MAX_TOOLS_PASSTHROUGH]
            log.info("Tool passthrough: ALL TOOLS: %s", [t.get("function", {}).get("name") for t in tools])
            log.info("Tool passthrough: capped tools from %d to %d (%s)",
                     len(tools), len(capped_tools),
                     [t["function"]["name"] for t in capped_tools])
        else:
            capped_tools = tools

        # Inject a tool-use guardrail into the system prompt so Llama
        # models don't eagerly call tools for conversational messages.
        _TOOL_GUARDRAIL = (
            "IMPORTANT: Respond with normal text for conversational messages. "
            "Only call a tool when the user explicitly asks you to perform an "
            "action that requires one (e.g. browse a URL, read a file, run a "
            "command, search the web). If the user is just chatting, reply "
            "with text — do NOT call any tools."
        )
        # Strip model footers from message history to prevent accumulation
        patched_messages = []
        for _msg in messages:
            _m = dict(_msg)
            if _m.get('content') and isinstance(_m['content'], str) and '— via ' in _m['content']:
                _idx = _m['content'].rfind('\n\n— via ')
                if _idx >= 0:
                    _m['content'] = _m['content'][:_idx]
            patched_messages.append(_m)
        if patched_messages and patched_messages[0].get("role") == "system":
            patched_messages[0] = {
                **patched_messages[0],
                "content": patched_messages[0].get("content", "") + "\n\n" + _TOOL_GUARDRAIL,
            }
        else:
            patched_messages.insert(0, {"role": "system", "content": _TOOL_GUARDRAIL})

        # Ensure max_tokens has a sane default (some clients send null).
        if not max_tokens:
            max_tokens = 4096

        # Estimate request size (chars / 4 ≈ tokens) to skip providers
        # with low TPM limits.  Groq free-tier caps at 12k TPM; Hermes
        # sessions routinely exceed that with 32 tools + system prompt.
        _est_chars = sum(len(str(m.get("content", ""))) for m in patched_messages)
        _est_chars += len(json.dumps(capped_tools)) if capped_tools else 0
        _est_tokens = _est_chars // 4
        # Providers and their approximate free-tier TPM limits
        _PROVIDER_TPM_LIMITS = {
            "groq": 12000,       # Groq free tier hard cap: 12K TPM
            "cerebras": 50000,
            "sambanova": 50000,
            "openrouter": 50000,
            "google-alt": 1000000,
            "google-primary": 1000000,
        }

        # Reorder providers based on cognitive tier hint
        providers_to_try = list(_TOOL_PROVIDERS)
        if str(cognitive_tier).upper().strip() in ["REASON", "PLAN_NEEDED"]:
            # Move owl to front for reasoning tasks (strongest model)
            providers_to_try.sort(
                key=lambda p: 0 if p[0] == "owl" else (1 if p[0] == "laguna" else 2)
            )
            log.info("Tier %s: owl prioritized", cognitive_tier)
            log.info("Providers order: %s", [p[0] for p in providers_to_try])
        elif cognitive_tier == "CHAT":
            # Chat shouldn't have tools, but if it does, use cheapest
            providers_to_try.sort(
                key=lambda p: 0 if p[0] in ("groq", "cerebras") else 1
            )
            log.info("Tier CHAT: cheapest providers prioritized")
        # EXECUTE uses default order (already optimized for cost)

        # --- Local-first for simple tool calls ---
        # Small requests (few tools, short context) can be handled by the
        # local qwen2.5-7b-tool-planning model, avoiding cloud API usage.
        _LOCAL_FIRST_MAX_TOKENS = 0
        _LOCAL_FIRST_MAX_TOOLS = 3
        _LOCAL_FIRST_TIMEOUT = 30.0  # bail fast if local is slow

        if _est_tokens < _LOCAL_FIRST_MAX_TOKENS and len(capped_tools) <= _LOCAL_FIRST_MAX_TOOLS:
            local_url = os.environ.get("LOCAL_LLM_URL", "http://localhost:8081")
            local_ok = await _check_local_health(local_url)
            if local_ok:
                local_tools = _sanitize_tools_for_local(capped_tools) if capped_tools else []
                local_payload = {
                    "model": "local",
                    "messages": patched_messages if local_tools else list(messages),
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                }
                if local_tools:
                    local_payload["tools"] = local_tools
                if tool_choice is not None and local_tools:
                    local_payload["tool_choice"] = tool_choice

                start = time.monotonic()
                try:
                    async with httpx.AsyncClient() as client:
                        resp = await client.post(
                            f"{local_url}/v1/chat/completions",
                            json=local_payload,
                            headers={"Content-Type": "application/json"},
                            timeout=_LOCAL_FIRST_TIMEOUT,
                        )
                    if resp.status_code in (200, 201):
                        data = resp.json()
                        choices = data.get("choices", [])
                        if choices:
                            msg = choices[0].get("message", {})
                            msg_content = (msg.get("content") or "").strip()
                            msg_tool_calls = msg.get("tool_calls") or []
                            if msg_content or msg_tool_calls:
                                elapsed_ms = int((time.monotonic() - start) * 1000)
                                data["timings"] = {"provider": "local", "latency_ms": elapsed_ms}
                                data = _append_model_footer(data, "local", "local")
                                log.info("Tool passthrough: local-first succeeded (%dms, ~%d tokens)", elapsed_ms, _est_tokens)
                                return JSONResponse(data)
                        log.info("Tool passthrough: local-first returned empty, falling through to cloud")
                    else:
                        log.info("Tool passthrough: local-first returned %d, falling through to cloud", resp.status_code)
                except httpx.TimeoutException:
                    log.info("Tool passthrough: local-first timed out (%.0fs), falling through to cloud", _LOCAL_FIRST_TIMEOUT)
                except Exception as exc:
                    log.info("Tool passthrough: local-first error: %r, falling through to cloud", exc)
            else:
                log.info("Tool passthrough: local LLM unhealthy, skipping local-first")

        # --- Cloud provider cascade ---
        for pname, url, env_key, default_model in providers_to_try:
            api_key = os.environ.get(env_key, "")
            if not api_key:
                continue

            # Skip providers whose TPM limit is too small for this request
            _tpm_limit = _PROVIDER_TPM_LIMITS.get(pname, 1000000)
            if _est_tokens > _tpm_limit:
                log.info("Tool passthrough: skipping %s (est %d tokens > %d TPM limit)",
                         pname, _est_tokens, _tpm_limit)
                continue

            # Skip permanently disabled providers (e.g. 402 no credits)
            if pname in _disabled_providers:
                continue

            # Skip providers in rate-limit cooldown
            cooldown_until = _rate_cooldowns.get(pname, 0)
            if time.monotonic() < cooldown_until:
                remaining = int(cooldown_until - time.monotonic())
                log.info("Tool passthrough: skipping %s (rate-limit cooldown, %ds left)", pname, remaining)
                continue

            # Respect budget if the provider is registered in the router
            provider_obj = router._providers_by_name.get(pname)
            if provider_obj is not None:
                if not provider_obj.is_available():
                    log.info("Tool passthrough: skipping %s (unavailable)", pname)
                    continue
                bs = provider_obj.budget_status()
                if bs.estimated_remaining is not None and bs.estimated_remaining <= 0:
                    log.info("Tool passthrough: skipping %s (budget exhausted)", pname)
                    continue

            # Always use the provider's default model — the client sends
            # "agentharness-proxy" which upstream providers don't recognise.
            req_model = body.get("model", "")
            use_model = default_model if req_model in ("", "agentharness-proxy") or req_model.startswith("claude-") else req_model
            payload = {
                "model": use_model,
                "messages": patched_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if capped_tools:
                payload["tools"] = capped_tools
            if tool_choice is not None and capped_tools:
                payload["tool_choice"] = tool_choice

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            start = time.monotonic()
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        url, json=payload, headers=headers, timeout=30.0,
                    )
            except httpx.HTTPError as exc:
                log.warning("Tool passthrough: %s HTTP error: %s", pname, exc)
                continue

            elapsed_ms = int((time.monotonic() - start) * 1000)

            if resp.status_code == 429:
                hits = _cooldown_hits.get(pname, 0) + 1
                _cooldown_hits[pname] = hits
                # Escalate: 60s, 120s, 240s, 480s, 960s, max 3600s
                cooldown = min(_COOLDOWN_SECONDS * (2 ** (hits - 1)), _MAX_COOLDOWN_SECONDS)
                _rate_cooldowns[pname] = time.monotonic() + cooldown
                log.warning("Tool passthrough: %s rate limited (429 #%d), cooling down for %ds", pname, hits, cooldown)
                continue
            if resp.status_code == 402:
                # Use timed cooldown instead of permanent disable — free-tier
                # providers (OpenRouter) return 402 transiently under congestion.
                hits = _cooldown_hits.get(pname, 0) + 1
                _cooldown_hits[pname] = hits
                cooldown = min(300 * (2 ** (hits - 1)), _MAX_COOLDOWN_SECONDS)  # 5min, 10min, 20min...
                _rate_cooldowns[pname] = time.monotonic() + cooldown
                log.warning("Tool passthrough: %s returned 402, cooldown %ds (hit #%d)", pname, cooldown, hits)
                continue
            if resp.status_code not in (200, 201):
                log.warning(
                    "Tool passthrough: %s returned %d: %s",
                    pname, resp.status_code, resp.text[:200],
                )
                continue

            # Success — record usage, reset cooldown counter, return response.
            _cooldown_hits.pop(pname, None)  # reset escalation on success
            data = resp.json()

            if not _is_valid_response(data, bool(capped_tools)):
                log.warning("Provider %s returned invalid tool response, escalating to next", pname)
                continue

            usage = data.get("usage", {})
            usage = data.get("usage", {})

            # Check for empty response — some providers (SambaNova, Cerebras)
            # return HTTP 200 but empty content after tool calls. Cascade to
            # next provider instead of returning empty to the caller.
            choices = data.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                msg_content = (msg.get("content") or "").strip()
                msg_tool_calls = msg.get("tool_calls") or []
                if not msg_content and not msg_tool_calls:
                    log.warning("Tool passthrough: %s returned empty content (no text, no tool_calls) — trying next provider", pname)
                    if budget is not None:
                        budget.record_usage(pname, tokens_in=usage.get("prompt_tokens", 0),
                                            tokens_out=0, success=False)
                    continue

            if budget is not None:
                budget.record_usage(
                    pname,
                    tokens_in=usage.get("prompt_tokens", 0),
                    tokens_out=usage.get("completion_tokens", 0),
                    success=True,
                )

            # Inject provider info so the caller knows who handled it
            data["timings"] = {"provider": pname, "latency_ms": elapsed_ms}
            data = _append_model_footer(data, pname, use_model)

            # Store in response cache (only non-tool-use responses —
            # tool-use responses are action-oriented and shouldn't be cached)
            msg = (data.get("choices", [{}])[0].get("message") or {})
            if not msg.get("tool_calls") and body.get("temperature", 0.7) <= 0.3:
                _response_cache.put(body, data)

            return JSONResponse(data)

        # All cloud providers failed — try local LLM as last resort.
        # Gemma 4 26B-A4B supports tool calling via ik_llama.cpp --jinja.
        local_url = os.environ.get("LOCAL_LLM_URL", "http://localhost:8081")

        # Health check the local LLM before attempting a request.
        # If unresponsive, try auto-restart.
        local_ok = await _check_local_health(local_url)
        if not local_ok:
            log.warning("Tool passthrough: local LLM unresponsive, attempting restart")
            local_ok = await _restart_local_llm()
            if not local_ok:
                log.error("Tool passthrough: local LLM restart failed, all providers exhausted")
                return JSONResponse(
                    {"error": {"message": "All providers exhausted. Local LLM unresponsive and restart failed."}},
                    status_code=503,
                )

        # Sanitize tool descriptions — ik-llama-server's jinja template
        # parser crashes on embedded double quotes in tool descriptions.
        local_tools = _sanitize_tools_for_local(capped_tools) if capped_tools else []

        payload = {
            "model": "local",
            "messages": patched_messages if local_tools else list(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if local_tools:
            payload["tools"] = local_tools
        if tool_choice is not None and local_tools:
            payload["tool_choice"] = tool_choice

        start = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{local_url}/v1/chat/completions",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=600.0,
                )
            if resp.status_code in (200, 201):
                elapsed_ms = int((time.monotonic() - start) * 1000)
                data = resp.json()
                data["timings"] = {"provider": "local", "latency_ms": elapsed_ms}
                data = _append_model_footer(data, "local", "local")
                log.info("Tool passthrough: local LLM fallback succeeded (%dms)", elapsed_ms)
                return JSONResponse(data)
            log.warning("Tool passthrough: local returned %d: %s",
                       resp.status_code, resp.text[:200])
        except httpx.TimeoutException:
            log.warning("Tool passthrough: local LLM timed out (600s) — may be hung")
            # Mark unhealthy so next request triggers restart
            _local_health["healthy"] = False
        except Exception as exc:
            log.warning("Tool passthrough: local LLM error: %r", exc)

        return JSONResponse(
            {"error": {"message": "No provider available for tool calling (cloud + local exhausted)"}},
            status_code=503,
        )

    # -- Orchestrator Workflow (Reasoning -> Execution) ----------------------
    async def _orchestrator_workflow(body: dict) -> JSONResponse:
        """Two-agent workflow for complex tasks.
        1. Reasoning Agent (Qwen3.6 Plus via OpenRouter) creates a plan.
        2. Execution Agent (tiered routing) executes each step.
        """
        import httpx

        # --- Step 1: Reasoning Agent (generate plan) ---
        reasoning_model = os.environ.get("REASON_MODEL", "deepseek/deepseek-chat")
        reasoning_provider = "openrouter"
        reasoning_url = "https://openrouter.ai/api/v1/chat/completions"
        openrouter_api_key = os.environ.get("OPENROUTER_API_KEY", "")

        if not openrouter_api_key:
            return JSONResponse({"error": {"message": "OPENROUTER_API_KEY not set for orchestrator"}}, status_code=503)

        user_prompt = ""
        messages = body.get("messages", [])
        for m in reversed(messages):
            if m.get("role") == "user":
                user_prompt = m.get("content", "")
                break

        reasoning_prompt = f"""
You are a DevOps expert. Your task is to create a step-by-step plan to resolve the user's request.
The plan should be a JSON array of objects, where each object has a "step" number and a "command" to execute.
The commands should be single-line shell commands or brief instructions for an LLM.

User request: "{user_prompt}"

Generate the JSON plan.
"""
        reasoning_payload = {
            "model": reasoning_model,
            "messages": [{"role": "user", "content": reasoning_prompt}],
            "max_tokens": 1024,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    reasoning_url,
                    json=reasoning_payload,
                    headers={"Authorization": f"Bearer {openrouter_api_key}", "Content-Type": "application/json"},
                    timeout=300.0,
                )
            if resp.status_code != 200:
                return JSONResponse({"error": {"message": f"Reasoning agent failed: {resp.text}"}}, status_code=502)

            plan_text = resp.json()["choices"][0]["message"]["content"]
            plan = json.loads(plan_text).get("plan", [])
        except Exception as e:
            return JSONResponse({"error": {"message": f"Failed to generate or parse plan: {e}"}}, status_code=500)

        # --- Step 2: Execution Agent (execute plan) ---
        execution_results = []
        router = _get_router()
        from core.providers.base import LLMRequest, Complexity

        for task in plan:
            step = task.get("step")
            command = task.get("command")
            log.info(f"Orchestrator: Executing step {step}: {command}")

            # Here we could add more sophisticated routing based on the command content.
            # For now, we use the standard tiered routing.
            llm_request = LLMRequest(
                prompt=command,
                complexity=Complexity.MEDIUM, # Assume all steps are medium complexity for now
            )
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, router.route, llm_request)

            if response.success:
                execution_results.append(f"Step {step}: {command}\nOutput: {response.text}")
            else:
                execution_results.append(f"Step {step}: {command}\nError: {response.error}")

        # --- Step 3: Final Response ---
        # Combine the results into a single response.
        final_response_text = "Orchestrator workflow complete:\n\n" + "\n\n".join(execution_results)

        return JSONResponse({
            "id": f"chatcmpl-orch-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": f"agentharness-orchestrator",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": final_response_text}, "finish_reason": "stop"}],
        })


    # -- Anthropic API compatibility layer ----------------------------------
    @app.get("/v1/cache")
    async def cache_stats():
        """Response cache statistics."""
        return JSONResponse(_response_cache.stats())

    @app.delete("/v1/cache")
    async def cache_clear():
        """Clear the response cache."""
        _response_cache._cache.clear()
        _response_cache.hits = 0
        _response_cache.misses = 0
        return JSONResponse({"status": "cleared"})

    # Allows Claude Code to use the proxy via /v1/messages (Anthropic format).
    from core.providers.anthropic_compat import register_anthropic_routes
    register_anthropic_routes(app, chat_completions)

    return app


def main():
    """Run the proxy server."""
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="AgentHarness LLM Proxy")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--data-dir", default=os.environ.get("AH_DATA_DIR", ""))
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    os.environ.setdefault("AH_DATA_DIR", args.data_dir)
    app = create_proxy_app(data_dir=args.data_dir)
    uvicorn.run(app, host=args.host, port=args.port, log_level="debug")


if __name__ == "__main__":
    main()
