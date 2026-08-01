"""RSS MCP server. Fetch, parse, and summarize news feeds."""
from __future__ import annotations
import os, sys, json, time, logging, xml.etree.ElementTree as ET
import requests as http_requests
sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("rss-mcp")

FEEDS_FILE = os.environ.get("FEEDS_FILE", "/data/rss_feeds.json")

DEFAULT_FEEDS = {
    "ai_news": [
        {"name": "Hacker News", "url": "https://hnrss.org/frontpage"},
        {"name": "The Verge AI", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"},
        {"name": "Ars Technica AI", "url": "https://feeds.arstechnica.com/arstechnica/technology-lab"},
    ],
    "homelab": [
        {"name": "r/selfhosted", "url": "https://www.reddit.com/r/selfhosted/.rss"},
        {"name": "r/homelab", "url": "https://www.reddit.com/r/homelab/.rss"},
    ],
    "llm": [
        {"name": "r/LocalLLaMA", "url": "https://www.reddit.com/r/LocalLLaMA/.rss"},
        {"name": "Hugging Face Blog", "url": "https://huggingface.co/blog/feed.xml"},
    ],
}

def _load_feeds():
    if os.path.exists(FEEDS_FILE):
        with open(FEEDS_FILE) as f: return json.load(f)
    return DEFAULT_FEEDS

def _save_feeds(feeds):
    os.makedirs(os.path.dirname(FEEDS_FILE), exist_ok=True)
    with open(FEEDS_FILE, "w") as f: json.dump(feeds, f, indent=2)

def _parse_feed(url, limit=5):
    try:
        resp = http_requests.get(url, timeout=15, headers={"User-Agent": "Chaguli-RSS/1.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = []
        # RSS 2.0
        for item in root.findall(".//item")[:limit]:
            items.append({
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "date": (item.findtext("pubDate") or "").strip(),
                "description": (item.findtext("description") or "")[:200].strip(),
            })
        # Atom
        if not items:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.findall(".//atom:entry", ns)[:limit]:
                link_el = entry.find("atom:link", ns)
                items.append({
                    "title": (entry.findtext("atom:title", "", ns) or "").strip(),
                    "link": link_el.get("href", "") if link_el is not None else "",
                    "date": (entry.findtext("atom:updated", "", ns) or "").strip(),
                    "description": (entry.findtext("atom:summary", "", ns) or "")[:200].strip(),
                })
        return items
    except Exception as e:
        return [{"error": str(e)}]

def fetch_feed(args):
    """Fetch articles from a specific RSS feed URL."""
    url = args.get("url", "")
    limit = args.get("limit", 5)
    if not url: return {"error": "url required"}
    items = _parse_feed(url, limit)
    return {"articles": items, "count": len(items), "url": url}

def fetch_category(args):
    """Fetch articles from all feeds in a category (ai_news, homelab, llm)."""
    category = args.get("category", "")
    limit = args.get("limit", 5)
    feeds = _load_feeds()
    if not category: return {"error": "category required", "available": list(feeds.keys())}
    if category not in feeds: return {"error": f"Unknown category: {category}", "available": list(feeds.keys())}
    all_articles = []
    for feed in feeds[category]:
        articles = _parse_feed(feed["url"], limit)
        for a in articles:
            if "error" not in a:
                a["source"] = feed["name"]
                all_articles.append(a)
    return {"category": category, "articles": all_articles[:limit * 3], "count": len(all_articles)}

def list_feeds(args):
    """List all configured RSS feed categories and their feeds."""
    feeds = _load_feeds()
    result = {}
    for cat, feed_list in feeds.items():
        result[cat] = [f["name"] for f in feed_list]
    return {"categories": result}

def add_feed(args):
    """Add a new RSS feed to a category."""
    category = args.get("category", "")
    name = args.get("name", "")
    url = args.get("url", "")
    if not all([category, name, url]): return {"error": "category, name, and url required"}
    feeds = _load_feeds()
    if category not in feeds: feeds[category] = []
    feeds[category].append({"name": name, "url": url})
    _save_feeds(feeds)
    return {"status": "added", "category": category, "name": name}

def daily_digest(args):
    """Generate a daily news digest from all feed categories."""
    limit = args.get("limit", 3)
    feeds = _load_feeds()
    digest = {}
    for cat, feed_list in feeds.items():
        articles = []
        for feed in feed_list:
            items = _parse_feed(feed["url"], limit)
            for a in items:
                if "error" not in a:
                    a["source"] = feed["name"]
                    articles.append(a)
        digest[cat] = articles[:limit * 2]
    return {"digest": digest, "generated_at": time.strftime("%Y-%m-%d %H:%M")}

TOOL_SCHEMAS = [
    {"name": "fetch_feed", "description": "Fetch articles from a specific RSS feed URL.", "inputSchema": {"type": "object", "properties": {"url": {"type": "string", "description": "RSS feed URL"}, "limit": {"type": "integer", "description": "Max articles (default: 5)"}}, "required": ["url"]}},
    {"name": "fetch_category", "description": "Fetch news from a category: ai_news, homelab, or llm.", "inputSchema": {"type": "object", "properties": {"category": {"type": "string", "description": "Feed category (ai_news, homelab, llm)"}, "limit": {"type": "integer", "description": "Articles per feed (default: 5)"}}, "required": ["category"]}},
    {"name": "daily_digest", "description": "Generate a daily news digest from all RSS feed categories. Use for morning briefings.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "Articles per feed (default: 3)"}}}},
    {"name": "list_feeds", "description": "List all configured RSS feed categories.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "add_feed", "description": "Add a new RSS feed to a category.", "inputSchema": {"type": "object", "properties": {"category": {"type": "string", "description": "Category name"}, "name": {"type": "string", "description": "Feed display name"}, "url": {"type": "string", "description": "RSS feed URL"}}, "required": ["category", "name", "url"]}},
]

def main():
    port = int(os.environ.get("MCP_PORT", "8104"))
    s = MCPServer(name="rss", port=port, tools=TOOL_SCHEMAS)
    for n, fn in [("fetch_feed", fetch_feed), ("fetch_category", fetch_category), ("daily_digest", daily_digest), ("list_feeds", list_feeds), ("add_feed", add_feed)]:
        s.register_handler(n, fn)
    log.info(f"RSS MCP starting on :{port}")
    s.start()

if __name__ == "__main__": main()
