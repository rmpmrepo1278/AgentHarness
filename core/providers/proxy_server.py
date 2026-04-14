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
from typing import Any

log = logging.getLogger(__name__)

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
    _COOLDOWN_SECONDS = 60  # skip provider for 60s after a 429

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
                resp = await client.get(f"{local_url}/health", timeout=5.0)
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

        bt = BudgetTracker(data_dir=data_dir)
        providers = []

        # Local Gemma 4 on port 8081
        local = LlamaCppProvider(
            name="local",
            endpoint=os.environ.get("LOCAL_LLM_URL", "http://localhost:8081"),
        )
        providers.append(local)

        # Cloud providers (only if API key is set)
        if os.environ.get("GROQ_API_KEY"):
            providers.append(GroqProvider())
        if os.environ.get("GOOGLE_API_KEY"):
            providers.append(GoogleProvider())
        if os.environ.get("CEREBRAS_API_KEY"):
            providers.append(CerebrasProvider())
        if os.environ.get("SAMBANOVA_API_KEY"):
            providers.append(SambaNovaProvider())
        if os.environ.get("OPENROUTER_API_KEY"):
            providers.append(OpenRouterProvider())
        if os.environ.get("OLLAMA_API_KEY"):
            providers.append(OllamaCloudProvider())

        provider_names = [p.name for p in providers]
        log.info(f"LLM Proxy initialized with providers: {provider_names}")

        router = Router(
            providers=providers,
            budget=bt,
            routing={
                "low": ["local", "google", "cerebras"],
                "medium": ["local", "google", "cerebras", "openrouter"],
                "high": ["groq", "google", "ollama_cloud", "sambanova", "local"],
                "critical": ["groq", "google", "ollama_cloud", "cerebras", "sambanova", "openrouter", "local"],
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

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        """OpenAI-compatible chat completions — routed through AgentHarness."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": {"message": "Invalid JSON"}}, status_code=400)

        messages = body.get("messages", [])
        max_tokens = body.get("max_tokens", 1024)
        temperature = body.get("temperature", 0.7)
        tools = body.get("tools")
        tool_choice = body.get("tool_choice")
        stream_requested = body.get("stream", False)

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
            if role == "system":
                system_prompt = content
            elif role == "user":
                prompt_parts.append(content)

        prompt = "\n".join(prompt_parts) if prompt_parts else ""
        if not prompt:
            return JSONResponse({"error": {"message": "No user message"}}, status_code=400)

        # Determine complexity from prompt length and context
        from core.providers.base import Complexity, LLMRequest
        token_estimate = len(prompt.split())
        if token_estimate < 20:
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
        "groq": "llama-3.3-70b-versatile",
        "google": "gemini-2.5-flash",
        "cerebras": "qwen-3-235b-a22b-instruct-2507",
        "sambanova": "Meta-Llama-3.3-70B-Instruct",
        "openrouter": "meta-llama/llama-3.3-70b-instruct",
    }
    _TOOL_PROVIDERS = [
        ("groq", "https://api.groq.com/openai/v1/chat/completions",
         "GROQ_API_KEY", os.environ.get("GROQ_TOOL_MODEL", _TOOL_PROVIDER_DEFAULTS["groq"])),
        ("google", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
         "GOOGLE_API_KEY", os.environ.get("GOOGLE_TOOL_MODEL", _TOOL_PROVIDER_DEFAULTS["google"])),
        ("cerebras", "https://api.cerebras.ai/v1/chat/completions",
         "CEREBRAS_API_KEY", os.environ.get("CEREBRAS_TOOL_MODEL", _TOOL_PROVIDER_DEFAULTS["cerebras"])),
        ("sambanova", "https://api.sambanova.ai/v1/chat/completions",
         "SAMBANOVA_API_KEY", os.environ.get("SAMBANOVA_TOOL_MODEL", _TOOL_PROVIDER_DEFAULTS["sambanova"])),
        ("openrouter", "https://openrouter.ai/api/v1/chat/completions",
         "OPENROUTER_API_KEY", os.environ.get("OPENROUTER_TOOL_MODEL", _TOOL_PROVIDER_DEFAULTS["openrouter"])),
    ]

    # Max tools to forward — free-tier Llama models degrade with too many
    # tool definitions and start calling tools randomly instead of chatting.
    # Llama 3.3 70B works reliably with ~6 tools; above that it starts
    # calling tools for simple chat messages.
    _MAX_TOOLS_PASSTHROUGH = 6

    async def _tool_call_passthrough(
        body: dict,
        messages: list,
        max_tokens: int,
        temperature: float,
        tools: list,
        tool_choice: Any | None,
    ) -> JSONResponse:
        """Forward tool-calling requests to cloud providers, with local LLM fallback.

        Cloud providers are tried first (faster, higher quality).  If all
        cloud providers fail, the local Gemma 4 26B-A4B is used as a
        last-resort fallback — it supports tool calling via --jinja mode.
        """
        import httpx

        router = _get_router()
        budget = _router_cache.get("budget")

        # Cap tools to avoid overwhelming free-tier models.  Prioritise
        # core tools over browser tools (which Llama can't use well anyway).
        _PRIORITY_PREFIXES = ("terminal", "file", "web_search", "memory", "skill", "session")
        if len(tools) > _MAX_TOOLS_PASSTHROUGH:
            priority = [t for t in tools if any(
                t.get("function", {}).get("name", "").startswith(p) for p in _PRIORITY_PREFIXES
            )]
            rest = [t for t in tools if t not in priority]
            capped_tools = (priority + rest)[:_MAX_TOOLS_PASSTHROUGH]
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
        patched_messages = list(messages)
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

        for pname, url, env_key, default_model in _TOOL_PROVIDERS:
            api_key = os.environ.get(env_key, "")
            if not api_key:
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
            use_model = default_model if req_model in ("", "agentharness-proxy") else req_model
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
                        url, json=payload, headers=headers, timeout=60.0,
                    )
            except httpx.HTTPError as exc:
                log.warning("Tool passthrough: %s HTTP error: %s", pname, exc)
                continue

            elapsed_ms = int((time.monotonic() - start) * 1000)

            if resp.status_code == 429:
                _rate_cooldowns[pname] = time.monotonic() + _COOLDOWN_SECONDS
                log.warning("Tool passthrough: %s rate limited (429), cooling down for %ds", pname, _COOLDOWN_SECONDS)
                continue
            if resp.status_code == 402:
                _disabled_providers.add(pname)
                log.warning("Tool passthrough: %s returned 402 (no credits), permanently disabled for this session", pname)
                continue
            if resp.status_code not in (200, 201):
                log.warning(
                    "Tool passthrough: %s returned %d: %s",
                    pname, resp.status_code, resp.text[:200],
                )
                continue

            # Success — record usage and return the response as-is, with
            # our timing metadata injected.
            data = resp.json()
            usage = data.get("usage", {})

            if budget is not None:
                budget.record_usage(
                    pname,
                    tokens_in=usage.get("prompt_tokens", 0),
                    tokens_out=usage.get("completion_tokens", 0),
                    success=True,
                )

            # Inject provider info so the caller knows who handled it
            data["timings"] = {"provider": pname, "latency_ms": elapsed_ms}
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
                    timeout=120.0,  # local model is slower
                )
            if resp.status_code in (200, 201):
                elapsed_ms = int((time.monotonic() - start) * 1000)
                data = resp.json()
                data["timings"] = {"provider": "local", "latency_ms": elapsed_ms}
                log.info("Tool passthrough: local LLM fallback succeeded (%dms)", elapsed_ms)
                return JSONResponse(data)
            log.warning("Tool passthrough: local returned %d: %s",
                       resp.status_code, resp.text[:200])
        except httpx.TimeoutException:
            log.warning("Tool passthrough: local LLM timed out (120s) — may be hung")
            # Mark unhealthy so next request triggers restart
            _local_health["healthy"] = False
        except Exception as exc:
            log.warning("Tool passthrough: local LLM error: %r", exc)

        return JSONResponse(
            {"error": {"message": "No provider available for tool calling (cloud + local exhausted)"}},
            status_code=503,
        )

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

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    os.environ.setdefault("AH_DATA_DIR", args.data_dir)
    app = create_proxy_app(data_dir=args.data_dir)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
