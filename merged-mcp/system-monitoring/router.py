"""Route tool calls to the correct MCP server.
Converts MCP tool schemas to OpenAI function-calling format."""
from __future__ import annotations
import logging
import time
import requests

import registry
import rate_limiter
import gateway_log

log = logging.getLogger("router")


def _mcp_to_openai_tool(mcp_tool: dict) -> dict:
    """Convert an MCP tool schema to OpenAI function-calling format."""
    return {
        "type": "function",
        "function": {
            "name": mcp_tool.get("name", ""),
            "description": mcp_tool.get("description", ""),
            "parameters": mcp_tool.get("inputSchema", mcp_tool.get("parameters", {
                "type": "object",
                "properties": {},
            })),
        },
    }


def get_catalog() -> list[dict]:
    """Get all available tools in OpenAI function-calling format."""
    tools = []
    for mcp_name, tool_schema in registry.get_healthy_tools():
        openai_tool = _mcp_to_openai_tool(tool_schema)
        openai_tool["_mcp_source"] = mcp_name
        tools.append(openai_tool)
    return tools


def _find_owner(tool_name: str) -> tuple[str, dict] | tuple[None, None]:
    """Find which MCP owns a tool."""
    for name, mcp in registry.get_all().items():
        if mcp["status"] in ("healthy", "degraded"):
            for tool in mcp.get("tools", []):
                if tool.get("name") == tool_name:
                    return name, mcp
    return None, None


def call_tool(tool_name: str, arguments: dict) -> dict:
    """Route a tool call to the correct MCP server."""
    allowed, retry_after = rate_limiter.check(tool_name)
    if not allowed:
        gateway_log.emit("rate_limited", tool=tool_name, retry_after=retry_after)
        return {"error": "rate_limited", "retry_after_seconds": retry_after}

    mcp_name, mcp = _find_owner(tool_name)
    if mcp is None:
        return {"error": f"No MCP server provides tool '{tool_name}'"}

    if mcp["status"] == "degraded":
        log.warning(f"Routing {tool_name} to degraded MCP {mcp_name}")

    start = time.time()
    try:
        resp = requests.post(
            mcp["address"],
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
                "id": 1,
            },
            timeout=120,
        )
        resp.raise_for_status()
        body = resp.json()
        duration_ms = int((time.time() - start) * 1000)

        if "error" in body:
            gateway_log.emit("tool_error", tool=tool_name, mcp=mcp_name,
                             error=str(body["error"]), duration_ms=duration_ms)
            return {"error": body["error"], "mcp": mcp_name, "duration_ms": duration_ms}

        result = body.get("result", {})
        gateway_log.emit("tool_call", tool=tool_name, mcp=mcp_name,
                         duration_ms=duration_ms, success=True)
        return {"result": result, "mcp": mcp_name, "duration_ms": duration_ms}

    except requests.RequestException as e:
        duration_ms = int((time.time() - start) * 1000)
        gateway_log.emit("tool_error", tool=tool_name, mcp=mcp_name,
                         error=str(e), duration_ms=duration_ms)
        return {"error": f"MCP {mcp_name} unreachable: {e}", "mcp": mcp_name}
