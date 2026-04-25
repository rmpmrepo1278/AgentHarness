"""Anthropic API compatibility layer for the AgentHarness LLM proxy.

Translates Anthropic /v1/messages requests into OpenAI /v1/chat/completions
format, routes through the existing proxy infrastructure, then translates
the response back to Anthropic format.

This allows Claude Code (which speaks Anthropic API) to use the tiered
local → free cloud → paid cloud routing.
"""
from __future__ import annotations

import json
import os
import logging
import time
import uuid
from typing import Any

log = logging.getLogger(__name__)

try:
    from fastapi import Request
    from fastapi.responses import JSONResponse, StreamingResponse
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Anthropic → OpenAI request translation
# ---------------------------------------------------------------------------

def _anthropic_content_to_openai(content: Any) -> str:
    """Convert Anthropic content (string or content-block array) to a string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "image":
                    parts.append("[image]")
                elif block.get("type") == "tool_result":
                    # tool_result content can itself be string or blocks
                    inner = block.get("content", "")
                    parts.append(_anthropic_content_to_openai(inner))
        return "\n".join(parts)
    return str(content) if content else ""


def _anthropic_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool definitions to OpenAI function-calling format.

    Anthropic: {name, description, input_schema: {type, properties, ...}}
    OpenAI:    {type: "function", function: {name, description, parameters: {...}}}

    Tools are sorted by name for Gemini implicit caching stability — the
    prefix hash stays identical across requests even if Claude Code sends
    tools in a different order.
    """
    openai_tools = []
    # Sort by name so tool block is identical across requests (cache stability)
    sorted_tools = sorted(tools, key=lambda t: t.get("name", ""))
    for tool in sorted_tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return openai_tools


def anthropic_request_to_openai(body: dict) -> dict:
    """Translate an Anthropic /v1/messages request to OpenAI chat format."""
    openai_messages = []

    # System prompt → system message
    # Strip cache_control markers (Anthropic-specific, not used by Gemini).
    # Preserve stable text ordering for Gemini implicit caching (≥ 4096 tokens).
    system = body.get("system", "")
    if isinstance(system, list):
        # Anthropic sends system as array of content blocks with cache_control
        parts = []
        for block in system:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        system = "\n".join(parts)


    if system:
        openai_messages.append({"role": "system", "content": system})

    # Convert messages
    for msg in body.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "user":
            # User messages can contain text + tool_result blocks
            if isinstance(content, list):
                text_parts = []
                tool_results = []
                for block in content:
                    if isinstance(block, str):
                        text_parts.append(block)
                    elif isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_result":
                            # OpenAI expects tool results as separate messages
                            tool_results.append(block)
                        elif block.get("type") == "image":
                            text_parts.append("[image]")

                # First add any text content as a user message
                text = "\n".join(text_parts).strip()

                # Tool results become separate "tool" role messages
                for tr in tool_results:
                    tr_content = tr.get("content", "")
                    if isinstance(tr_content, list):
                        tr_content = _anthropic_content_to_openai(tr_content)
                    elif not isinstance(tr_content, str):
                        tr_content = str(tr_content) if tr_content else ""
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": tr.get("tool_use_id", ""),
                        "content": tr_content,
                    })

                # Add user text if present (after tool results, to maintain order)
                if text:
                    openai_messages.append({"role": "user", "content": text})
            else:
                openai_messages.append({"role": "user", "content": str(content)})

        elif role == "assistant":
            # Assistant messages can contain text + tool_use blocks
            if isinstance(content, list):
                text_parts = []
                tool_calls = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            tool_calls.append({
                                "id": block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                                "type": "function",
                                "function": {
                                    "name": block.get("name", ""),
                                    "arguments": json.dumps(block.get("input", {})),
                                },
                            })

                assistant_msg: dict[str, Any] = {"role": "assistant"}
                text = "\n".join(text_parts).strip()
                if text:
                    assistant_msg["content"] = text
                else:
                    assistant_msg["content"] = None
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                openai_messages.append(assistant_msg)
            else:
                openai_messages.append({"role": "assistant", "content": str(content)})

    # Build OpenAI request
    openai_body: dict[str, Any] = {
        "model": body.get("model", "agentharness-proxy"),
        "messages": openai_messages,
        "max_tokens": body.get("max_tokens", 4096),
        "temperature": body.get("temperature", 0.7),
    }

    # Tools
    tools = body.get("tools")
    if tools:
        openai_body["tools"] = _anthropic_tools_to_openai(tools)

    # Tool choice
    tool_choice = body.get("tool_choice")
    if tool_choice and tools:
        if isinstance(tool_choice, dict):
            tc_type = tool_choice.get("type", "auto")
            if tc_type == "auto":
                openai_body["tool_choice"] = "auto"
            elif tc_type == "any":
                openai_body["tool_choice"] = "required"
            elif tc_type == "tool":
                openai_body["tool_choice"] = {
                    "type": "function",
                    "function": {"name": tool_choice.get("name", "")},
                }
        elif isinstance(tool_choice, str):
            openai_body["tool_choice"] = tool_choice

    # Top-p
    if "top_p" in body:
        openai_body["top_p"] = body["top_p"]

    # Stop sequences
    if "stop_sequences" in body:
        openai_body["stop"] = body["stop_sequences"]

    return openai_body


# ---------------------------------------------------------------------------
# OpenAI → Anthropic response translation
# ---------------------------------------------------------------------------

def openai_response_to_anthropic(data: dict, model: str = "agentharness-proxy") -> dict:
    """Translate an OpenAI chat completion response to Anthropic format."""
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"

    choices = data.get("choices", [])
    if not choices:
        return _anthropic_error("No response from provider")

    choice = choices[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason", "stop")

    # Build content blocks
    content_blocks = []
    text = message.get("content")
    if text:
        # Strip the model footer the proxy appends
        if isinstance(text, str):
            footer_idx = text.rfind("\n\n\u2014 via ")
            if footer_idx >= 0:
                text = text[:footer_idx]
        content_blocks.append({"type": "text", "text": text})

    tool_calls = message.get("tool_calls")
    if tool_calls:
        for tc in tool_calls:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            content_blocks.append({
                "type": "tool_use",
                "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:20]}"),
                "name": fn.get("name", ""),
                "input": args,
            })

    # If no content at all, return None to signal the caller should error
    if not content_blocks:
        return None

    # Map finish reason
    if tool_calls:
        stop_reason = "tool_use"
    elif finish_reason == "stop":
        stop_reason = "end_turn"
    elif finish_reason == "length":
        stop_reason = "max_tokens"
    else:
        stop_reason = "end_turn"

    # Usage
    usage = data.get("usage", {})
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)

    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


def openai_response_to_anthropic_sse(data: dict, model: str = "agentharness-proxy") -> StreamingResponse:
    """Convert an OpenAI response into Anthropic SSE streaming format.

    Claude Code expects these event types:
      message_start → content_block_start → content_block_delta(s) → content_block_stop
      → message_delta → message_stop
    """
    anthropic_resp = openai_response_to_anthropic(data, model)

    def _generate():
        msg_id = anthropic_resp["id"]
        usage = anthropic_resp["usage"]
        content = anthropic_resp["content"]
        stop_reason = anthropic_resp["stop_reason"]

        # message_start
        yield "event: message_start\n"
        yield "data: " + json.dumps({
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": usage["input_tokens"],
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        }) + "\n\n"

        # Content blocks
        for idx, block in enumerate(content):
            if block["type"] == "text":
                # content_block_start
                yield "event: content_block_start\n"
                yield "data: " + json.dumps({
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {"type": "text", "text": ""},
                }) + "\n\n"

                # Stream text in chunks for more realistic streaming
                text = block["text"]
                chunk_size = 20  # characters per chunk
                for i in range(0, len(text), chunk_size):
                    chunk = text[i:i + chunk_size]
                    yield "event: content_block_delta\n"
                    yield "data: " + json.dumps({
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {"type": "text_delta", "text": chunk},
                    }) + "\n\n"

                # content_block_stop
                yield "event: content_block_stop\n"
                yield "data: " + json.dumps({
                    "type": "content_block_stop",
                    "index": idx,
                }) + "\n\n"

            elif block["type"] == "tool_use":
                # content_block_start with tool_use
                yield "event: content_block_start\n"
                yield "data: " + json.dumps({
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {
                        "type": "tool_use",
                        "id": block["id"],
                        "name": block["name"],
                        "input": {},
                    },
                }) + "\n\n"

                # Stream the input JSON as a delta
                input_json = json.dumps(block["input"])
                yield "event: content_block_delta\n"
                yield "data: " + json.dumps({
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": input_json,
                    },
                }) + "\n\n"

                # content_block_stop
                yield "event: content_block_stop\n"
                yield "data: " + json.dumps({
                    "type": "content_block_stop",
                    "index": idx,
                }) + "\n\n"

        # message_delta with stop reason and final usage
        yield "event: message_delta\n"
        yield "data: " + json.dumps({
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_reason,
                "stop_sequence": None,
            },
            "usage": {
                "output_tokens": usage["output_tokens"],
            },
        }) + "\n\n"

        # message_stop
        yield "event: message_stop\n"
        yield "data: " + json.dumps({"type": "message_stop"}) + "\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _anthropic_error(message: str, status: int = 500) -> dict:
    """Create an Anthropic-format error response."""
    return {
        "type": "error",
        "error": {
            "type": "api_error",
            "message": message,
        },
    }


# ---------------------------------------------------------------------------
# FastAPI route registration
# ---------------------------------------------------------------------------

def register_anthropic_routes(app: Any, chat_completions_handler: Any) -> None:
    """Register Anthropic-compatible /v1/messages endpoint on the FastAPI app.

    Args:
        app: FastAPI application instance
        chat_completions_handler: The existing async chat_completions handler
            that processes OpenAI-format requests and returns JSONResponse.
    """

    @app.post("/v1/messages")
    async def anthropic_messages(request: Request):
        """Anthropic-compatible /v1/messages endpoint.

        Translates to OpenAI format and calls Google Gemini 2.5 Pro directly,
        bypassing the tiered proxy routing.  This gives Claude Code consistent
        quality from a single strong model.
        """
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                _anthropic_error("Invalid JSON"),
                status_code=400,
            )

        stream_requested = body.get("stream", False)

        log.info(
            "Anthropic /v1/messages -> Gemini direct: stream=%s messages=%d tools=%d",
            stream_requested,
            len(body.get("messages", [])),
            len(body.get("tools", [])),
        )
        log.debug("Anthropic request body: %s", json.dumps(body, indent=2))

        # Translate Anthropic -> OpenAI
        try:
            openai_body = anthropic_request_to_openai(body)
        except Exception as exc:
            log.error("Anthropic request translation failed: %s", exc)
            return JSONResponse(
                _anthropic_error(f"Request translation error: {exc}"),
                status_code=400,
            )
        log.debug("Translated OpenAI request body: %s", json.dumps(openai_body, indent=2))

        # Force model to Gemini 2.5 Pro, disable streaming (we handle SSE ourselves)
        _gemini_model = os.environ.get("GEMINI_CLAUDE_CODE_MODEL", "gemini-2.5-pro")
        openai_body["model"] = _gemini_model
        openai_body["stream"] = False

        # Gemini 2.5 Pro uses internal thinking tokens that count against
        # max_tokens.  Ensure a minimum so thinking doesn't consume the
        # entire budget and return empty responses.
        if openai_body.get("max_tokens", 0) < 16384:
            openai_body["max_tokens"] = 16384

        # Call Google Gemini API directly
        _google_api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not _google_api_key:
            return JSONResponse(
                _anthropic_error("GOOGLE_API_KEY not set"),
                status_code=503,
            )

        _gemini_url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    _gemini_url,
                    json=openai_body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {_google_api_key}",
                    },
                    timeout=180.0,
                )
        except Exception as exc:
            log.error("Gemini direct call failed: %s", exc)
            return JSONResponse(
                _anthropic_error(f"Gemini API error: {exc}"),
                status_code=503,
            )

        if resp.status_code != 200:
            error_text = resp.text[:500]
            log.warning("Gemini returned %d: %s", resp.status_code, error_text)
            return JSONResponse(
                _anthropic_error(f"Gemini error ({resp.status_code}): {error_text}"),
                status_code=resp.status_code,
            )

        try:
            openai_data = resp.json()
        except Exception:
            return JSONResponse(
                _anthropic_error("Invalid response from Gemini"),
                status_code=502,
            )

        # Log token usage including any implicit cache savings
        usage = openai_data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        log.info(
            "Gemini response: in=%d out=%d total=%d (implicit caching active if total < in+out)",
            prompt_tokens,
            completion_tokens,
            usage.get("total_tokens", 0),
        )

        # Detect empty/degenerate responses from Gemini (often caused by
        # context overflow or thinking-token exhaustion) and return an
        # Anthropic "overloaded" error so Claude Code retries or compacts.
        anthropic_data = openai_response_to_anthropic(openai_data, _gemini_model)
        if anthropic_data is None:
            log.warning(
                "Gemini returned empty content (in=%d out=%d) — returning overloaded error",
                prompt_tokens, completion_tokens,
            )
            return JSONResponse(
                {
                    "type": "error",
                    "error": {
                        "type": "overloaded",
                        "message": "Model returned empty response — context may be too large. "
                                   f"Prompt tokens: {prompt_tokens}.",
                    },
                },
                status_code=529,
            )

        # Guard: if prompt tokens exceed 900K, warn that context is near limit
        if prompt_tokens > 900_000:
            log.warning("Context nearing Gemini limit: %d prompt tokens", prompt_tokens)

        if stream_requested:
            return openai_response_to_anthropic_sse(openai_data, _gemini_model)
        else:
            return JSONResponse(anthropic_data)

    # Also add a model listing in Anthropic format
    @app.get("/v1/messages/models")
    async def anthropic_models():
        return JSONResponse({
            "models": [
                {
                    "id": "agentharness-proxy",
                    "display_name": "AgentHarness Proxy (tiered routing)",
                    "created_at": "2026-04-01T00:00:00Z",
                },
            ],
        })

    log.info("Anthropic /v1/messages endpoint registered")
