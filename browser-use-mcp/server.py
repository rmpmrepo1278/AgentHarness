"""Browser-Use MCP server.

AI-powered browser automation for personal tasks:
- Price comparison across sites
- Form filling and web scraping
- Stealth browsing for sites that block bots
- Screenshot capture
- Content extraction

Uses browser-use with headless Chromium via Playwright.
"""
from __future__ import annotations
import os
import sys
import json
import time
import logging
import asyncio
from urllib.parse import urlparse

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("browser-use-mcp")

# Semaphore to limit concurrent browser operations
_browser_lock = asyncio.Lock()


async def _run_browser_task(coro):
    """Run a browser task with locking to prevent concurrent browser use."""
    async with _browser_lock:
        return await coro


def browse_url(args):
    """Navigate to a URL and extract the page content.

    Args:
        url: The URL to navigate to
        extract_mode: "text" (default), "markdown", or "html"
        wait_for: CSS selector to wait for before extracting
        max_chars: Max characters to return (default: 8000)
    """
    url = args.get("url", "")
    extract_mode = args.get("extract_mode", "markdown")
    wait_for = args.get("wait_for", "")
    max_chars = args.get("max_chars", 8000)

    if not url:
        return {"error": "url required"}

    try:
        from langchain_openai import ChatOpenAI
        from browser_use import Agent, Browser, BrowserConfig

        llm = ChatOpenAI(
            base_url=os.environ.get("LLM_PROXY_URL", "http://127.0.0.1:8080/v1"),
            api_key=os.environ.get("LLM_PROXY_API_KEY", "not-needed"),
            model=os.environ.get("BROWSER_USE_MODEL", "groq/llama-3.3-70b-versatile"),
            temperature=0,
        )

        browser = Browser(
            config=BrowserConfig(
                headless=True,
                disable_security=True,
            )
        )

        task = f"Navigate to {url} and extract the page content"
        if wait_for:
            task += f". Wait for '{wait_for}' to appear first"

        agent = Agent(
            task=task,
            llm=llm,
            browser=browser,
            use_vision=False,
        )

        result = asyncio.run(_run_browser_task(agent.run(max_steps=10)))
        content = str(result.final_result()) if hasattr(result, "final_result") else str(result)

        if len(content) > max_chars:
            content = content[:max_chars] + "\n... [truncated]"

        return {
            "url": url,
            "content": content,
            "mode": extract_mode,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    except ImportError as e:
        return {"error": f"browser-use not available: {e}"}
    except Exception as e:
        return {"error": f"Browse failed: {str(e)}"}


def browse_interactive(args):
    """Perform an interactive browser task (click, type, navigate, extract).

    Args:
        task: Natural language description of the task (e.g., "Search for RTX 5090 prices on Amazon and Newegg, compare the top 3 results")
        max_steps: Maximum browser actions (default: 15)
    """
    task = args.get("task", "")
    max_steps = args.get("max_steps", 15)

    if not task:
        return {"error": "task required"}

    try:
        from langchain_openai import ChatOpenAI
        from browser_use import Agent, Browser, BrowserConfig

        llm = ChatOpenAI(
            base_url=os.environ.get("LLM_PROXY_URL", "http://127.0.0.1:8080/v1"),
            api_key=os.environ.get("LLM_PROXY_API_KEY", "not-needed"),
            model=os.environ.get("BROWSER_USE_MODEL", "groq/llama-3.3-70b-versatile"),
            temperature=0,
        )

        browser = Browser(
            config=BrowserConfig(
                headless=True,
                disable_security=True,
            )
        )

        agent = Agent(
            task=task,
            llm=llm,
            browser=browser,
            use_vision=False,
        )

        result = asyncio.run(_run_browser_task(agent.run(max_steps=max_steps)))
        content = str(result.final_result()) if hasattr(result, "final_result") else str(result)

        return {
            "task": task,
            "result": content[:10000],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    except ImportError as e:
        return {"error": f"browser-use not available: {e}"}
    except Exception as e:
        return {"error": f"Interactive browse failed: {str(e)}"}


def take_screenshot(args):
    """Navigate to a URL and take a screenshot.

    Args:
        url: The URL to screenshot
        full_page: If true, capture full page (default: false)
        wait_for: CSS selector to wait for before screenshot
    """
    url = args.get("url", "")
    full_page = args.get("full_page", False)
    wait_for = args.get("wait_for", "")

    if not url:
        return {"error": "url required"}

    try:
        from playwright.async_api import async_playwright

        async def _screenshot():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1280, "height": 900})
                await page.goto(url, wait_until="networkidle", timeout=30000)
                if wait_for:
                    await page.wait_for_selector(wait_for, timeout=10000)
                screenshot = await page.screenshot(full_page=full_page)
                await browser.close()
                return screenshot

        screenshot_data = asyncio.run(_screenshot())
        import base64
        b64 = base64.b64encode(screenshot_data).decode()

        return {
            "url": url,
            "screenshot": b64[:100] + f"... [{len(b64)} chars base64]",
            "size_bytes": len(screenshot_data),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    except Exception as e:
        return {"error": f"Screenshot failed: {str(e)}"}


def extract_structured(args):
    """Navigate to a URL and extract structured data.

    Args:
        url: The URL to extract from
        schema: JSON schema describing what to extract (e.g., {"product_name": "string", "price": "number", "rating": "number"})
        wait_for: CSS selector to wait for before extracting
    """
    url = args.get("url", "")
    schema = args.get("schema", {})
    wait_for = args.get("wait_for", "")

    if not url:
        return {"error": "url required"}
    if not schema:
        return {"error": "schema required"}

    try:
        from langchain_openai import ChatOpenAI
        from browser_use import Agent, Browser, BrowserConfig

        schema_str = json.dumps(schema, indent=2)
        task = f"Navigate to {url} and extract data matching this schema:\n{schema_str}\n\nReturn ONLY valid JSON matching the schema."

        llm = ChatOpenAI(
            base_url=os.environ.get("LLM_PROXY_URL", "http://127.0.0.1:8080/v1"),
            api_key=os.environ.get("LLM_PROXY_API_KEY", "not-needed"),
            model=os.environ.get("BROWSER_USE_MODEL", "groq/llama-3.3-70b-versatile"),
            temperature=0,
        )

        browser = Browser(
            config=BrowserConfig(
                headless=True,
                disable_security=True,
            )
        )

        agent = Agent(
            task=task,
            llm=llm,
            browser=browser,
            use_vision=False,
        )

        result = asyncio.run(_run_browser_task(agent.run(max_steps=10)))
        content = str(result.final_result()) if hasattr(result, "final_result") else str(result)

        # Try to parse as JSON
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from the response
            import re
            match = re.search(r"\{[\s\S]*\}", content)
            if match:
                data = json.loads(match.group())
            else:
                data = {"raw": content[:5000]}

        return {
            "url": url,
            "data": data,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    except Exception as e:
        return {"error": f"Extraction failed: {str(e)}"}


TOOL_SCHEMAS = [
    {
        "name": "browse_url",
        "description": "Navigate to a URL and extract page content. Good for reading articles, scraping info, or getting page text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to"},
                "extract_mode": {"type": "string", "description": "Extraction mode: text, markdown, or html (default: markdown)"},
                "wait_for": {"type": "string", "description": "CSS selector to wait for before extracting"},
                "max_chars": {"type": "integer", "description": "Max characters to return (default: 8000)"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browse_interactive",
        "description": "Perform an interactive browser task using natural language. Good for price comparison, form filling, multi-step web tasks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Natural language task description (e.g., 'Compare prices for RTX 5090 on Amazon, Newegg, and Best Buy')"},
                "max_steps": {"type": "integer", "description": "Max browser actions (default: 15)"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "take_screenshot",
        "description": "Navigate to a URL and capture a screenshot. Returns base64-encoded PNG.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to screenshot"},
                "full_page": {"type": "boolean", "description": "Capture full page (default: false)"},
                "wait_for": {"type": "string", "description": "CSS selector to wait for before screenshot"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "extract_structured",
        "description": "Navigate to a URL and extract structured data matching a JSON schema. Good for scraping product info, prices, reviews, etc.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to extract from"},
                "schema": {"type": "object", "description": "JSON schema describing what to extract"},
                "wait_for": {"type": "string", "description": "CSS selector to wait for before extracting"},
            },
            "required": ["url", "schema"],
        },
    },
]


def main():
    port = int(os.environ.get("MCP_PORT", "8107"))
    s = MCPServer(name="browser-use", port=port, tools=TOOL_SCHEMAS)
    for name, fn in [
        ("browse_url", browse_url),
        ("browse_interactive", browse_interactive),
        ("take_screenshot", take_screenshot),
        ("extract_structured", extract_structured),
    ]:
        s.register_handler(name, fn)
    log.info(f"Browser-Use MCP starting on :{port}")
    s.start()


if __name__ == "__main__":
    main()
