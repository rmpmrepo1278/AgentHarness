"""LLM Proxy Server — OpenAI-compatible API that routes through AgentHarness.

Sits on port 8080 and routes requests to the best available provider
(Groq, Cerebras, OpenRouter, local Ollama).

Chaguli and any other client just calls http://localhost:8080/v1/chat/completions
and gets routed automatically.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

# PII redaction - strips emails, phones, SSNs, etc. before cloud LLMs
from core.providers.pii_redact import (
    redact as redact_pii,
    is_enabled as pii_redact_enabled,
)
# Circuit breaker for provider resilience
from core.providers.circuit_breaker import get_all_states, reset as reset_circuit_breaker


def _tool_args(arguments) -> dict:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except (ValueError, TypeError):
            return {}
    return {}


def create_proxy_app(data_dir: str = "") -> object:
    """Create the LLM proxy FastAPI app."""
    if not HAS_FASTAPI:
        raise ImportError("FastAPI not installed. Run: pip install fastapi uvicorn")

    data_dir = data_dir or os.environ.get("AH_DATA_DIR", ".")

    app = FastAPI(title="AgentHarness LLM Proxy")

    # Lazy-init router on first request
    _router_cache = {}

    def _get_router():
        if "router" in _router_cache:
            return _router_cache["router"]

        from core.providers.budget import BudgetTracker
        from core.providers.router import Router
        from core.providers.llamacpp import LlamaCppProvider
        from core.providers.rate_limit_tracker import RateLimitTracker
        from core.providers.groq import GroqProvider
        from core.providers.cerebras import CerebrasProvider
        from core.providers.openai_compat import OpenAICompatProvider
        from core.providers.openrouter import OpenRouterProvider

        bt = BudgetTracker(data_dir=data_dir)
        rlt = RateLimitTracker(data_dir=data_dir)  # Rate limit tracker for cooldown checking
        providers = []

        # Local LLM (Ollama) — multiple models for intent-based routing.
        # Larger models need longer timeout for cold starts.
        local_endpoint = os.environ.get("LOCAL_LLM_URL", "http://localhost:11434")
        local_models = [
            ("local", "llama3.2:3b", 15),           # Fast small model
            ("local-gemma12b", "gemma4:12b", 120),   # 7.5GB — longer for cold start
            ("local-qwen8b", "qwen3:8b", 60),        # 5.2GB
            ("local-qwen32b", "qwen3:32b", 180),     # 19GB — longest cold start
        ]
        for name, model, timeout in local_models:
            providers.append(LlamaCppProvider(
                name=name,
                endpoint=local_endpoint,
                model=model,
                timeout=timeout,
            ))

        # Cloud providers (only if API key is set)
        if os.environ.get("OPENROUTER_API_KEY"):
            # Primary: Owl-Alpha (Hermes 3 405B via OpenRouter)
            providers.append(OpenRouterProvider(
                model=os.environ.get("OWL_MODEL", "openai/gpt-oss-120b:free"),
                name="owl",
                daily_limit=50000
            ))
            # OpenRouter free-tier
            providers.append(OpenRouterProvider(
                name="openrouter",
                daily_limit=10000
            ))
            # Mistral via OpenRouter (1B tokens/month free)
            providers.append(OpenRouterProvider(
                model="mistralai/mistral-7b-instruct:free",
                name="mistral",
                daily_limit=1000000
            ))
            # DeepSeek V4 Flash (free on OpenRouter)
            providers.append(OpenRouterProvider(
                model="deepseek/deepseek-v4-flash:free",
                name="deepseek-v4-flash",
                daily_limit=50000
            ))
        if os.environ.get("GROQ_API_KEY"):
            providers.append(GroqProvider(model="llama-3.3-70b-versatile", daily_limit=12000))
        if os.environ.get("CEREBRAS_API_KEY"):
            providers.append(CerebrasProvider(model="gpt-oss-120b", daily_limit=50000))
        if os.environ.get("SAMBANOVA_API_KEY"):
            from core.providers.sambanova import SambaNovaProvider
            providers.append(SambaNovaProvider(model="gpt-oss-120b", daily_limit=50000))
        if os.environ.get("GITHUB_API_KEY"):
            providers.append(OpenAICompatProvider(
                name="github-models",
                endpoint="https://models.inference.ai.azure.com",
                env_key="GITHUB_API_KEY",
                model="gpt-4o",
                daily_limit=150,
            ))
        provider_names = [p.name for p in providers]
        log.info(f"LLM Proxy initialized with providers: {provider_names}")

        # Routing — speed-first order across providers.
        # The task_router in chat_completions() overrides with forced_provider
        # for intent-based local-first routing + quality-gated escalation.
        speed_order = ["groq", "sambanova", "github-models", "cerebras", "mistral", "owl", "openrouter", "local-qwen32b", "local-gemma12b", "local-qwen8b", "local"]
        router = Router(
            providers=providers,
            budget=bt,
            rate_limit_tracker=rlt,  # Pass rate limit tracker for cooldown-aware retry
            routing={
                "low": speed_order,
                "medium": speed_order,
                "high": speed_order,
                "critical": speed_order,
            },
        )
        _router_cache["router"] = router
        _router_cache["budget"] = bt
        return router

    @app.get("/health")
    def health():
        return JSONResponse({"status": "ok", "type": "agentharness_proxy"})

    @app.get("/api/hello")
    @app.head("/api/hello")
    def api_hello():
        return JSONResponse({"ok": True, "version": "2.1.0", "model": "agentharness-proxy"})

    @app.get("/v1/models")
    def models():
        return JSONResponse({
            "object": "list",
            "data": [{"id": "agentharness-proxy", "object": "model", "owned_by": "agentharness"}],
        })

    @app.get("/v1/status")
    def status():
        router = _get_router()
        rtp = router._providers_by_name
        providers_info = {}
        cb_states = get_all_states()  # Circuit breaker states
        for name, p in rtp.items():
            fails = _failure_counts.get(name, 0)
            cb = cb_states.get(name, {})
            endpoint = getattr(p, "endpoint", "") or ""
            is_local = name == "local" or name.startswith("local") or "127.0.0.1" in endpoint or "localhost" in endpoint
            providers_info[name] = {
                "type": "local" if is_local else "cloud",
                "healthy": p.is_available() if hasattr(p, "is_available") else True,
                "model": getattr(p, "model", "unknown"),
                "has_api_key": bool(getattr(p, "api_key", "")),
                "enabled": p.enabled() if callable(getattr(p, "enabled", None)) else getattr(p, "enabled", True),
                "health_probe": {
                    "consecutive_failures": fails,
                    "healthy": fails == 0,
                },
                "circuit_breaker": {
                    "state": cb.get("state", "CLOSED"),
                    "failures": cb.get("failure_count", 0),
                    "available": cb.get("available", True),
                },
            }
        return JSONResponse({
            "timestamp": int(time.time()),
            "overall": "healthy",
            "providers": providers_info,
            "routing_order": router._routing.get("critical", []),
            "circuit_states": cb_states,
        })

    @app.get("/v1/cache")
    def cache_stats():
        # ponytail: thin read-only view over existing caches, not a new caching
        # layer. Add a real response cache only if hit-rate proves it's needed.
        from core.providers import token_juice, short_circuit
        tj = token_juice.get_stats()
        sc_len = len(getattr(short_circuit, "_cache", {}))
        hits = tj.get("cache_hits", 0)
        misses = tj.get("cache_misses", 0)
        return JSONResponse({
            "hits": hits,
            "misses": misses,
            "size": sc_len + len(getattr(token_juice, "_content_cache", {})._cache
                     if hasattr(token_juice, "_content_cache") else sc_len),
            "token_juice": tj,
            "short_circuit_size": sc_len,
        })

    @app.delete("/v1/cache")
    def cache_clear():
        from core.providers import token_juice, short_circuit
        if hasattr(token_juice, "_content_cache"):
            token_juice._content_cache._cache.clear()  # type: ignore[attr-defined]
        if hasattr(short_circuit, "_cache"):
            short_circuit._cache.clear()
        token_juice._stats["cache_hits"] = 0  # type: ignore[assignment]
        token_juice._stats["cache_misses"] = 0  # type: ignore[assignment]
        return JSONResponse({"success": True})

    # ponytail: global failure counter, per-provider locks if multi-process.
    _failure_counts: dict[str, int] = {}

    @app.post("/v1/routing")
    async def routing_control(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        action = body.get("action")
        router = _get_router()
        if action in ("disable_provider", "enable_provider"):
            name = body.get("provider", "")
            p = router._providers_by_name.get(name)
            if p is None:
                return JSONResponse({"success": False, "error": f"unknown provider {name}"}, status_code=404)
            p.enabled = (action == "enable_provider")  # type: ignore[attr-defined]
            if p.enabled:
                _failure_counts.pop(name, None)
            return JSONResponse({"success": True})
        if action == "reset_cooldowns":
            for p in router._providers_by_name.values():
                if hasattr(p, "reset_cooldowns"):
                    p.reset_cooldowns()
            return JSONResponse({"success": True})
        if action == "reset_circuit_breaker":
            provider_name = body.get("provider")
            if provider_name:
                reset_circuit_breaker(provider_name)
            else:
                reset_circuit_breaker()  # Reset all
            return JSONResponse({"success": True})
        return JSONResponse({"success": False, "error": f"unknown action {action}"}, status_code=400)

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

        # Intent classification + complexity estimation (replaces token-only logic)
        from core.providers.base import Complexity, LLMRequest
        from core.providers.task_router import (
            decide as task_router_decide, check_quality,
        )

        # PII redaction — must happen BEFORE routing to cloud
        pii_result = None
        if pii_redact_enabled():
            redacted_system = redact_pii(system_prompt) if system_prompt else None
            redacted_prompt = redact_pii(prompt)
            if redacted_prompt.total or (redacted_system and redacted_system.total):
                pii_result = {
                    "total": redacted_prompt.total + (redacted_system.total if redacted_system else 0),
                    "fields": {**redacted_prompt.redacted, **(redacted_system.redacted if redacted_system else {})},
                }
                prompt = redacted_prompt.text
                system_prompt = redacted_system.text if redacted_system else system_prompt

        decision = task_router_decide(prompt, system_prompt)

        # If client explicitly requested a model, map it to the local provider
        has_tools = bool(body.get("tools"))
        requested_model = body.get("model", "")
        explicit_model_map = {
            "llama3.2:3b": "local",
            "gemma4:12b": "local-gemma12b",
            "qwen3:8b": "local-qwen8b",
            "qwen3:32b": "local-qwen32b",
            "deepseek/deepseek-v4-flash": "deepseek-v4-flash",
        }
        explicit_provider = explicit_model_map.get(requested_model, None)

        # Determine routing strategy
        if explicit_provider:
            forced_provider = explicit_provider
            direct_to_cloud = False
        elif has_tools:
            # Tool-calling requests need a model with real function-calling
            # support. Local llama3.2:3b does NOT emit tool_calls — it
            # narrates commands as text (the "We need to run commands..."
            # leak users saw in Telegram). All cloud providers here support
            # native tool calls, so force cloud-first for tool requests.
            direct_to_cloud = True
            forced_provider = None
        else:
            direct_to_cloud = decision.direct_to_cloud
            forced_provider = None  # Let Router use normal (cloud-first) routing

        router = _get_router()

        llm_request = LLMRequest(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            complexity=decision.complexity,
            system_prompt=system_prompt,
            tools=body.get("tools"),
            tool_name=body.get("tool_name"),
        )

        start = time.monotonic()
        import asyncio
        if direct_to_cloud:
            # Skip local entirely — go straight to cloud with normal failover
            response = router.route(llm_request)
        else:
            # Local-first: force the local model for this intent
            forced_local = explicit_provider or decision.local_provider
            response = router.route(llm_request, forced_provider=forced_local)

            # Quality gate — escalate to cloud if local quality is insufficient
            if response.success and response.text:
                passed, quality_reason = check_quality(
                    response.text, prompt, decision.intent
                )
                if not passed:
                    log.warning(
                        f"Local quality gate failed ({quality_reason}) "
                        f"intent={decision.intent.value}, escalating to cloud"
                    )
                    # Try the primary cloud provider, then fall back to normal routing
                    cloud_provider = decision.cloud_providers[0] if decision.cloud_providers else None
                    if cloud_provider:
                        cloud_response = router.route(llm_request, forced_provider=cloud_provider)
                        if cloud_response.success:
                            response = cloud_response
                        else:
                            response = router.route(llm_request)  # Normal failover

        elapsed_ms = int((time.monotonic() - start) * 1000)

        log.info(
            f"Routed: intent={decision.intent.value} complex={decision.complexity.value} "
            f"model={decision.local_model} direct_cloud={decision.direct_to_cloud} "
            f"provider={response.provider} latency={elapsed_ms}ms"
        )

        if not response.success:
            return JSONResponse(
                {"error": {"message": f"All providers failed: {response.error}"}},
                status_code=503,
            )

        # Surface structured tool_calls. Cloud providers (groq, cerebras,
        # openai_compat, openrouter) encode tool_calls as JSON text inside
        # ``response.text``. Decode them back into the OpenAI ``tool_calls``
        # field so clients execute the tools instead of echoing the JSON
        # narration back to the user.
        tool_calls = None
        resp_text = response.text or ""
        if has_tools:
            try:
                parsed = json.loads(resp_text)
                if isinstance(parsed, dict) and parsed.get("tool_calls"):
                    tool_calls = parsed["tool_calls"]
                    resp_text = ""
            except Exception:
                pass

        # Streaming support (SSE)
        if body.get("stream", False):
            from starlette.responses import StreamingResponse
            import json as _json

            async def stream_response():
                if tool_calls:
                    yield "data: " + _json.dumps({"choices": [{"delta": {"role": "assistant"}, "index": 0}]}) + "\n\n"
                    for i, tc in enumerate(tool_calls):
                        tc_with_index = dict(tc)
                        tc_with_index["index"] = i
                        yield "data: " + _json.dumps({"choices": [{"delta": {"tool_calls": [tc_with_index]}, "index": 0}]}) + "\n\n"
                    yield "data: " + _json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls", "index": 0}]}) + "\n\n"
                    yield "data: [DONE]\n\n"
                    return
                yield "data: " + _json.dumps({"choices": [{"delta": {"role": "assistant"}, "index": 0}]}) + "\n\n"
                for i in range(0, len(resp_text), max(1, len(resp_text) // 20)):
                    chunk = resp_text[i:i + max(1, len(resp_text) // 20)]
                    yield "data: " + _json.dumps({"choices": [{"delta": {"content": chunk}, "index": 0}]}) + "\n\n"
                    await asyncio.sleep(0)
                yield "data: " + _json.dumps({"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]}) + "\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(stream_response(), media_type="text/event-stream")

        # Format as OpenAI response (non-streaming)
        message_payload = {"role": "assistant", "content": resp_text}
        if tool_calls:
            message_payload["content"] = None
            message_payload["tool_calls"] = tool_calls
        resp_data = {
            "id": f"chatcmpl-ah-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": f"agentharness-proxy ({response.provider})",
            "choices": [{
                "index": 0,
                "message": message_payload,
                "finish_reason": "tool_calls" if tool_calls else "stop",
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
            "routing": {
                "intent": decision.intent.value,
                "complexity": decision.complexity.value,
                "local_model": decision.local_model,
                "direct_to_cloud": decision.direct_to_cloud,
            },
        }
        return JSONResponse(resp_data)

    @app.post("/v1/messages")
    async def anthropic_messages(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": {"message": "Invalid JSON"}}, status_code=400)

        requested_model = body.get("model", "claude-sonnet-4-20250514")
        system = body.get("system", "")
        messages = body.get("messages", [])
        max_tokens = body.get("max_tokens", 1024)
        temperature = body.get("temperature", 0.7)
        stream = body.get("stream", False)
        tools = body.get("tools", [])

        if isinstance(system, list):
            texts = [b.get("text", "") for b in system if b.get("type") == "text"]
            system = " ".join(texts)

        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                texts = [b.get("text", "") for b in content if b.get("type") == "text"]
                content = "\n".join(texts)
            if role == "user":
                prompt_parts.append("User: " + str(content))
            elif role == "assistant":
                prompt_parts.append("Assistant: " + str(content))

        prompt = "\n".join(prompt_parts) if prompt_parts else ""
        if not prompt:
            return JSONResponse({"error": {"message": "No user message"}}, status_code=400)

        from core.providers.base import Complexity, LLMRequest
        token_estimate = len(prompt.split())
        if token_estimate < 20:
            complexity = Complexity.LOW
        elif token_estimate < 100:
            complexity = Complexity.MEDIUM
        else:
            complexity = Complexity.HIGH

        has_tools = len(tools) > 0
        local_first = has_tools and token_estimate < 2000 and len(tools) <= 6

        # Model routing (same map as /v1/chat/completions)
        tool_model_routing = {
            "llama3.2:3b": "local",
            "gemma4:12b": "local",
            "qwen2.5:7b": "local",
            "deepseek/deepseek-v4-flash": "deepseek-v4-flash",
        }
        standard_model_routing = {
            "llama3.2:3b": "local",
            "gemma4:12b": "local",
            "qwen2.5:7b": "local",
            "deepseek/deepseek-v4-flash": "deepseek-v4-flash",
        }
        model_routing = tool_model_routing if has_tools else standard_model_routing

        resp_text = ""
        tokens_in = 0
        tokens_out = 0
        tool_calls = None
        provider_used = "none"

        # Try local Ollama — use qwen2.5:7b (fast, already loaded) for tool calls
        if local_first:
            try:
                import httpx
                local_url = os.environ.get("LOCAL_LLM_URL", "http://localhost:11434")
                or_messages = []
                if system:
                    or_messages.append({"role": "system", "content": system})
                for msg in messages:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                        content = "\n".join(texts)
                    if role in ("user", "assistant"):
                        or_messages.append({"role": role, "content": str(content) if content else ""})

                local_payload = {
                    "model": os.environ.get("LOCAL_TOOL_MODEL", "qwen2.5:7b"),
                    "messages": or_messages,
                    "tools": tools,
                    "stream": False,
                    "think": False,
                    "temperature": 0,
                    "options": {"num_ctx": 8192, "num_predict": max_tokens},
                }
                async with httpx.AsyncClient(timeout=60) as client:
                    local_resp = await client.post(
                        f"{local_url}/api/chat",
                        json=local_payload,
                    )
                    if local_resp.status_code == 200:
                        local_data = local_resp.json()
                        msg = local_data.get("message", {})
                        resp_text = msg.get("content", "") or ""
                        tool_calls = msg.get("tool_calls")
                        local_usage = local_data.get("usage", {}) or {}
                        tokens_in = local_usage.get("prompt_tokens", 0)
                        tokens_out = local_usage.get("completion_tokens", 0)
                        provider_used = "local"
            except Exception as e:
                log.error(f"Local Ollama fallback failed: {e}")
                pass

        # Fallback to OpenRouter (cloud)
        if not resp_text and not tool_calls:
            openrouter_key = os.environ.get("OPENROUTER_API_KEY")
            if openrouter_key:
                import httpx
                or_messages = []
                if system:
                    or_messages.append({"role": "system", "content": system})
                for msg in messages:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        texts = [b.get("text", "") for b in content if b.get("type") == "text"]
                        content = "\n".join(texts)
                    if role in ("user", "assistant"):
                        or_messages.append({"role": role, "content": content})
                
                or_payload = {
                    "model": "inclusionai/ling-3.0-flash:free",
                    "messages": or_messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                }
                if tools:
                    or_payload["tools"] = tools
                
                or_headers = {
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json",
                    "X-Title": "AgentHarness",
                }
                try:
                    async with httpx.AsyncClient(timeout=120) as client:
                        or_resp = await client.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            json=or_payload,
                            headers=or_headers,
                        )
                        if or_resp.status_code == 200:
                            or_data = or_resp.json()
                            choice = or_data["choices"][0]
                            msg = choice.get("message", {})
                            resp_text = msg.get("content", "") or ""
                            tool_calls = msg.get("tool_calls")
                            or_usage = or_data.get("usage", {})
                            tokens_in = or_usage.get("prompt_tokens", 0)
                            tokens_out = or_usage.get("completion_tokens", 0)
                            provider_used = "openrouter"
                except Exception as e:
                    log.error(f"OpenRouter fallback failed: {e}")
                    pass

        # Final fallback: use the Router (same logic as /v1/chat/completions)
        if not resp_text and not tool_calls:
            try:
                router = _get_router()
                from core.providers.base import Complexity, LLMRequest
                token_estimate = len(prompt.split()) if prompt else 0
                complexity = Complexity.LOW if token_estimate < 20 else (Complexity.MEDIUM if token_estimate < 100 else Complexity.HIGH)
                llm_req = LLMRequest(
                    prompt=prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    complexity=complexity,
                    system_prompt=system,
                )
                routed_provider = model_routing.get(requested_model, None)
                router_resp = router.route(llm_req, forced_provider=routed_provider)
                resp_text = router_resp.text or ""
                tokens_in = router_resp.tokens_in or 0
                tokens_out = router_resp.tokens_out or 0
                provider_used = "router:" + (routed_provider or router_resp.provider or "auto")
                if router_resp.tool_calls:
                    tool_calls = [{"function": {"name": tc.name, "arguments": json.dumps(tc.args)}} for tc in router_resp.tool_calls]
            except Exception as e:
                log.error(f"Router fallback failed: {e}")
                pass

        msg_id = "msg_ah_" + str(int(time.time()))
        content_blocks = []
        
        if resp_text:
            content_blocks.append({"type": "text", "text": resp_text})
        elif provider_used == "none":
            # Log the failure for debugging — don't silently return empty
            log.error(f"All providers exhausted for /v1/messages request. Provider: none, Tool calls: {bool(tool_calls)}")
        
        if tool_calls:
            for tc in tool_calls:
                func = tc.get("function", {})
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", "call_" + str(int(time.time()))),
                    "name": func.get("name", ""),
                    "input": _tool_args(func.get("arguments", "{}"))
                })

        if stream:
            from starlette.responses import StreamingResponse

            def iter_anthropic():
                yield "event: message_start\ndata: " + json.dumps({
                    "type": "message_start", 
                    "message": {
                        "id": msg_id, "type": "message", "role": "assistant", 
                        "content": [], "model": requested_model, 
                        "stop_reason": None, "stop_sequence": None,
                        "usage": {"input_tokens": tokens_in, "output_tokens": 0}
                    }
                }) + "\n\n"

                if content_blocks:
                    for i, block in enumerate(content_blocks):
                        if block["type"] == "tool_use":
                            yield "event: content_block_start\ndata: " + json.dumps({
                                "type": "content_block_start", 
                                "index": i, 
                                "content_block": {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}
                            }) + "\n\n"
                            
                            yield "event: content_block_delta\ndata: " + json.dumps({
                                "type": "content_block_delta", 
                                "index": i, 
                                "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
                            }) + "\n\n"
                            
                            yield "event: content_block_stop\ndata: " + json.dumps({"type": "content_block_stop", "index": i}) + "\n\n"
                        else:
                            yield "event: content_block_start\ndata: " + json.dumps({
                                "type": "content_block_start", 
                                "index": i, 
                                "content_block": {"type": "text", "text": ""}
                            }) + "\n\n"
                            
                            text = block["text"]
                            for j in range(0, len(text), 20):
                                chunk = text[j:j+20]
                                yield "event: content_block_delta\ndata: " + json.dumps({
                                    "type": "content_block_delta", 
                                    "index": i, 
                                    "delta": {"type": "text_delta", "text": chunk}
                                }) + "\n\n"
                            
                            yield "event: content_block_stop\ndata: " + json.dumps({"type": "content_block_stop", "index": i}) + "\n\n"

                stop_reason = "tool_use" if tool_calls else "end_turn"
                yield "event: message_delta\ndata: " + json.dumps({
                    "type": "message_delta", 
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None}, 
                    "usage": {"output_tokens": tokens_out}
                }) + "\n\n"
                yield "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n"

            return StreamingResponse(iter_anthropic(), media_type="text/event-stream")

        resp_data = {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "content": content_blocks,
            "model": requested_model,
            "stop_reason": "tool_use" if tool_calls else "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
        }
        return JSONResponse(resp_data)


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
