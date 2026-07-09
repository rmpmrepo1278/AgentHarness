"""LLM Proxy Server — OpenAI-compatible API that routes through AgentHarness.

Sits on port 8080 and routes requests to the best available provider
(freellmapi, Groq, Cerebras, OpenRouter, local Ollama).

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
        from core.providers.freellmapi import FreeLLMAPIProvider
        from core.providers.openai_compat import OpenAICompatProvider
        from core.providers.openrouter import OpenRouterProvider

        bt = BudgetTracker(data_dir=data_dir)
        rlt = RateLimitTracker(data_dir=data_dir)  # Rate limit tracker for cooldown checking
        providers = []

        # Local LLM (Ollama) — disaster recovery fallback
        local = LlamaCppProvider(
            name="local",
            endpoint=os.environ.get("LOCAL_LLM_URL", "http://localhost:11434"),
        )
        providers.append(local)

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
        if os.environ.get("FREELLMAPI_KEY"):
            providers.append(FreeLLMAPIProvider(
                name="freellmapi",
                model=os.environ.get("FREELLMAPI_MODEL", "auto"),
                daily_limit=int(os.environ.get("FREELLMAPI_DAILY_LIMIT", "100000")),
            ))

        provider_names = [p.name for p in providers]
        log.info(f"LLM Proxy initialized with providers: {provider_names}")

        # Routing — distribute load across all working providers
        # freellmapi first: auto-routes across 16+ providers internally
        # then round-robin: groq → cerebras → mistral → owl → openrouter → local
        speed_order = ["freellmapi", "groq", "sambanova", "github-models", "cerebras", "mistral", "owl", "openrouter", "local"]
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
            providers_info[name] = {
                "type": "local" if name == "local" else "cloud",
                "healthy": p.is_available() if hasattr(p, "is_available") else True,
                "model": getattr(p, "model", "unknown"),
                "has_api_key": bool(getattr(p, "api_key", "")),
                "enabled": getattr(p, "enabled", True),
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

        # PII redaction - intercept before reaching cloud providers
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

        llm_request = LLMRequest(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            complexity=complexity,
            system_prompt=system_prompt,
        )

        start = time.monotonic()
        import asyncio
        loop = asyncio.get_event_loop()
        response = router.route(llm_request)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        if not response.success:
            return JSONResponse(
                {"error": {"message": f"All providers failed: {response.error}"}},
                status_code=503,
            )

        # Streaming support (SSE)
        if body.get("stream", False):
            from starlette.responses import StreamingResponse
            import json as _json
            resp_text = response.text or ""

            async def stream_response():
                yield "data: " + _json.dumps({"choices": [{"delta": {"role": "assistant"}, "index": 0}]}) + "\n\n"
                for i in range(0, len(resp_text), max(1, len(resp_text) // 20)):
                    chunk = resp_text[i:i + max(1, len(resp_text) // 20)]
                    yield "data: " + _json.dumps({"choices": [{"delta": {"content": chunk}, "index": 0}]}) + "\n\n"
                    await asyncio.sleep(0)
                yield "data: " + _json.dumps({"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]}) + "\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(stream_response(), media_type="text/event-stream")

        # Format as OpenAI response (non-streaming)
        resp_data = {
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
        if pii_result:
            resp_data["pii_redacted"] = pii_result
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
