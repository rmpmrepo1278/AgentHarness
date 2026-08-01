"""Crawl4AI MCP server. Web crawling and content extraction via crawl4ai."""
from __future__ import annotations
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))

from mcp_base import MCPServer

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("crawl4ai-mcp")


async def _crawl_url(url: str, **kwargs) -> dict:
    from crawl4ai import AsyncWebCrawler
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=url, **kwargs)
        _media = result.media or {}
        _links = result.links or {}
        return {
            "url": url,
            "success": result.success,
            "markdown": (result.markdown or "")[:50000],
            "extracted_content": (result.extracted_content or "")[:50000],
            "media": {
                "images": (_media.get("images") or [])[:50],
                "videos": (_media.get("videos") or [])[:50],
                "audios": (_media.get("audios") or [])[:50],
            },
            "links": {
                "internal": (_links.get("internal") or [])[:100],
                "external": (_links.get("external") or [])[:100],
            },
        }


async def _crawl_with_strategy(url: str, css_selector: str, **kwargs) -> dict:
    from crawl4ai import AsyncWebCrawler
    from bs4 import BeautifulSoup
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=url)
        extracted = []
        soup = BeautifulSoup(result.html or "", "html.parser")
        for el in soup.select(css_selector):
            extracted.append({"text": el.get_text(strip=True)[:5000], "html": str(el)[:1000]})
        return {
            "url": url,
            "success": result.success,
            "extracted": extracted[:100],
            "count": len(extracted),
            "markdown": (result.markdown or "")[:30000],
        }


async def _search_web(query: str, max_results: int = 10) -> dict:
    from duckduckgo_search import DDGS
    try:
        results = list(DDGS().text(query, max_results=max_results))
        return {
            "query": query,
            "results": results[:max_results],
            "count": len(results),
        }
    except Exception as e:
        log.exception("search failed for %s", query)
        return {"error": str(e), "query": query, "results": [], "count": 0}


async def _deep_crawl(start_url: str, max_pages: int = 20, same_domain: bool = True) -> dict:
    from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
    from crawl4ai.deep_crawling.bfs_strategy import BFSDeepCrawlStrategy
    strategy = BFSDeepCrawlStrategy(
        max_depth=3,
        include_external=not same_domain,
        max_pages=max_pages,
    )
    config = CrawlerRunConfig(deep_crawl_strategy=strategy)
    async with AsyncWebCrawler() as crawler:
        results = await crawler.arun(start_url, config=config)
        pages = []
        for r in results:
            if r.success:
                _links = r.links or {}
                pages.append({
                    "url": r.url or start_url,
                    "markdown": (r.markdown or "")[:20000],
                    "links": {
                        "internal": (_links.get("internal") or [])[:50],
                        "external": (_links.get("external") or [])[:50],
                    },
                })
        return {
            "start_url": start_url,
            "pages_crawled": len(pages),
            "max_pages": max_pages,
            "pages": pages,
        }


def crawl_web(args: dict) -> dict:
    url = args.get("url", "")
    if not url:
        return {"error": "url is required"}
    try:
        return asyncio.run(_crawl_url(url))
    except Exception as e:
        log.exception("crawl_web failed for %s", url)
        return {"error": str(e), "url": url}


def crawl_and_extract(args: dict) -> dict:
    url = args.get("url", "")
    css_selector = args.get("css_selector", "")
    if not url:
        return {"error": "url is required"}
    if not css_selector:
        return {"error": "css_selector is required"}
    try:
        return asyncio.run(_crawl_with_strategy(url, css_selector))
    except Exception as e:
        log.exception("crawl_and_extract failed for %s", url)
        return {"error": str(e), "url": url}


def crawl_search(args: dict) -> dict:
    query = args.get("query", "")
    max_results = args.get("max_results", 10)
    if not query:
        return {"error": "query is required"}
    try:
        return asyncio.run(_search_web(query, max_results))
    except Exception as e:
        log.exception("crawl_search failed")
        return {"error": str(e), "query": query}


def deep_crawl(args: dict) -> dict:
    url = args.get("url", "")
    max_pages = args.get("max_pages", 20)
    same_domain = args.get("same_domain", True)
    if not url:
        return {"error": "url is required"}
    try:
        return asyncio.run(_deep_crawl(url, max_pages, same_domain))
    except Exception as e:
        log.exception("deep_crawl failed for %s", url)
        return {"error": str(e), "url": url}


TOOL_SCHEMAS = [
    {
        "name": "crawl_web",
        "description": "Crawl a single URL and return the page content as markdown. Use for reading documentation, articles, or any web page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to crawl"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "crawl_and_extract",
        "description": "Crawl a URL and extract structured data using a CSS selector. Returns extracted elements as JSON.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to crawl"},
                "css_selector": {"type": "string", "description": "CSS selector to target elements for extraction (e.g. article h2, .product-card)"},
            },
            "required": ["url", "css_selector"],
        },
    },
    {
        "name": "crawl_search",
        "description": "Search the web using DuckDuckGo search. Returns search results with title, URL, and snippet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string"},
                "max_results": {"type": "integer", "description": "Maximum number of search results to return (default: 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "deep_crawl",
        "description": "Deep crawl a website starting from a URL using BFS (breadth-first search) strategy. Crawls linked pages within the same domain.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Starting URL for the deep crawl"},
                "max_pages": {"type": "integer", "description": "Maximum pages to crawl (default: 20)"},
                "same_domain": {"type": "boolean", "description": "Only crawl pages on the same domain (default: true)"},
            },
            "required": ["url"],
        },
    },
]


def main():
    port = int(os.environ.get("MCP_PORT", "8125"))
    server = MCPServer(name="crawl4ai", port=port, tools=TOOL_SCHEMAS)
    server.register_handler("crawl_web", crawl_web)
    server.register_handler("crawl_and_extract", crawl_and_extract)
    server.register_handler("crawl_search", crawl_search)
    server.register_handler("deep_crawl", deep_crawl)
    log.info("Crawl4AI MCP starting on :%d with %d tools", port, len(TOOL_SCHEMAS))
    server.start()


if __name__ == "__main__":
    main()
