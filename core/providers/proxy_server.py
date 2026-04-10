"""LLM Proxy Server — OpenAI-compatible API that routes through AgentHarness.

Sits on port 8080 and routes requests to the best available provider
(local Gemma 4, Groq, Google, Cerebras, SambaNova, OpenRouter).

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
        from core.providers.groq import GroqProvider
        from core.providers.google import GoogleProvider
        from core.providers.cerebras import CerebrasProvider
        from core.providers.sambanova import SambaNovaProvider
        from core.providers.openrouter import OpenRouterProvider

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

        provider_names = [p.name for p in providers]
        log.info(f"LLM Proxy initialized with providers: {provider_names}")

        router = Router(
            providers=providers,
            budget=bt,
            routing={
                "low": ["local"],
                "medium": ["local", "google", "cerebras", "openrouter"],
                "high": ["groq", "google", "sambanova", "local"],
                "critical": ["groq", "google", "cerebras", "sambanova", "openrouter", "local"],
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

        # If tools are present, use passthrough mode — forward the full
        # OpenAI-compatible request directly to cloud providers that support
        # tool calling.  The local LLM does not support tools, so it is
        # excluded from the candidate list.
        if tools:
            return await _tool_call_passthrough(
                body, messages, max_tokens, temperature, tools, tool_choice,
            )

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
        response = router.route(llm_request)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        if not response.success:
            return JSONResponse(
                {"error": {"message": f"All providers failed: {response.error}"}},
                status_code=503,
            )

        # Format as OpenAI response
        return JSONResponse({
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
        })

    # -- Tool calling passthrough ------------------------------------------------
    # Cloud providers that support OpenAI-compatible tool calling, in priority
    # order.  Each entry is (provider_name, base_url, env_key_for_api_key, default_model).
    _TOOL_PROVIDERS = [
        ("groq", "https://api.groq.com/openai/v1/chat/completions",
         "GROQ_API_KEY", "llama-3.3-70b-versatile"),
        ("google", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
         "GOOGLE_API_KEY", "gemini-2.0-flash"),
        ("cerebras", "https://api.cerebras.ai/v1/chat/completions",
         "CEREBRAS_API_KEY", "llama-3.3-70b"),
        ("sambanova", "https://api.sambanova.ai/v1/chat/completions",
         "SAMBANOVA_API_KEY", "Meta-Llama-3.3-70B-Instruct"),
        ("openrouter", "https://openrouter.ai/api/v1/chat/completions",
         "OPENROUTER_API_KEY", "meta-llama/llama-3.3-70b-instruct"),
    ]

    async def _tool_call_passthrough(
        body: dict,
        messages: list,
        max_tokens: int,
        temperature: float,
        tools: list,
        tool_choice: Any | None,
    ) -> JSONResponse:
        """Forward tool-calling requests directly to cloud providers.

        The local LLM cannot handle tool schemas, so we iterate through
        cloud providers that expose an OpenAI-compatible /chat/completions
        endpoint, forwarding the full request body.  Budget tracking is
        still applied.
        """
        import httpx

        router = _get_router()
        budget = _router_cache.get("budget")

        for pname, url, env_key, default_model in _TOOL_PROVIDERS:
            api_key = os.environ.get(env_key, "")
            if not api_key:
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

            # Build the outgoing request — use the model from the body if
            # provided, otherwise use the provider's default.
            payload = {
                "model": body.get("model") or default_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "tools": tools,
            }
            if tool_choice is not None:
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
                log.warning("Tool passthrough: %s rate limited (429)", pname)
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

        # All providers failed
        return JSONResponse(
            {"error": {"message": "No cloud provider available for tool calling"}},
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
