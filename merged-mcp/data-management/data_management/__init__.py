"""Data Management MCP - Git, Paperless, RSS combined."""
from __future__ import annotations

import json
import logging
import os
import sys
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

import feedparser
import requests

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("data-management")

# Git/Gitea configuration
GITEA_URL = os.environ.get("GITEA_URL", "http://127.0.0.1:3001")
GITEA_TOKEN = os.environ.get("GITEA_TOKEN", "")

# Paperless configuration
PAPERLESS_URL = os.environ.get("PAPERLESS_URL", "http://127.0.0.1:8000")
PAPERLESS_TOKEN = os.environ.get("PAPERLESS_TOKEN", "")
CONSUME_DIR = os.environ.get("CONSUME_DIR", "/home/rohit/openclaw/data/paperless/consume")

# RSS configuration
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
        with open(FEEDS_FILE) as f:
            return json.load(f)
    return DEFAULT_FEEDS

def _save_feeds(feeds):
    os.makedirs(os.path.dirname(FEEDS_FILE), exist_ok=True)
    with open(FEEDS_FILE, "w") as f:
        json.dump(feeds, f, indent=2)

def _parse_feed(url, limit=5):
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Chaguli-RSS/1.0"})
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


def _git_h():
    h = {"Content-Type": "application/json"}
    if GITEA_TOKEN:
        h["Authorization"] = f"token {GITEA_TOKEN}"
    return h

def list_repos(args):
    try:
        resp = requests.get(f"{GITEA_URL}/api/v1/repos/search", params={"limit": args.get("limit", 20)}, headers=_git_h(), timeout=10)
        resp.raise_for_status()
        return {"repos": [{"name": r["full_name"], "description": r.get("description", ""), "stars": r.get("stars_count", 0), "updated": r.get("updated_at", "")} for r in resp.json().get("data", [])], "count": len(resp.json().get("data", []))}
    except Exception as e: return {"error": str(e)}

def get_commits(args):
    repo = args.get("repo", "")
    limit = args.get("limit", 10)
    if not repo: return {"error": "repo required (e.g., rohit/agentharness)"}
    try:
        resp = requests.get(f"{GITEA_URL}/api/v1/repos/{repo}/commits", params={"limit": limit}, headers=_git_h(), timeout=10)
        resp.raise_for_status()
        return {"commits": [{"sha": c["sha"][:8], "message": c["commit"]["message"].split("\n")[0], "author": c["commit"]["author"]["name"], "date": c["commit"]["author"]["date"]} for c in resp.json()], "repo": repo}
    except Exception as e: return {"error": str(e)}

def list_issues(args):
    repo = args.get("repo", "")
    if not repo: return {"error": "repo required"}
    try:
        resp = requests.get(f"{GITEA_URL}/api/v1/repos/{repo}/issues", params={"state": args.get("state", "open"), "limit": 20}, headers=_git_h(), timeout=10)
        resp.raise_for_status()
        return {"issues": [{"id": i["number"], "title": i["title"], "state": i["state"], "created": i.get("created_at", "")} for i in resp.json()]}
    except Exception as e: return {"error": str(e)}

def create_issue(args):
    repo = args.get("repo", "")
    title = args.get("title", "")
    body = args.get("body", "")
    if not repo or not title: return {"error": "repo and title required"}
    try:
        resp = requests.post(f"{GITEA_URL}/api/v1/repos/{repo}/issues", json={"title": title, "body": body}, headers=_git_h(), timeout=10)
        resp.raise_for_status()
        i = resp.json()
        return {"status": "created", "issue_number": i["number"], "url": i.get("html_url", "")}
    except Exception as e: return {"error": str(e)}


def _paperless_h():
    h = {"Accept": "application/json"}
    if PAPERLESS_TOKEN:
        h["Authorization"] = f"Token {PAPERLESS_TOKEN}"
    return h

def search_documents(args):
    query = args.get("query", "")
    limit = args.get("limit", 10)
    if not query: return {"error": "query is required"}
    try:
        resp = requests.get(
            f"{PAPERLESS_URL}/api/documents/",
            params={"query": query, "page_size": limit},
            headers=_paperless_h(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        docs = [{
            "id": d["id"],
            "title": d.get("title", ""),
            "correspondent": d.get("correspondent_name", ""),
            "created": d.get("created", ""),
            "tags": [t for t in d.get("tags", [])],
            "document_type": d.get("document_type_name", ""),
        } for d in data.get("results", [])]
        return {"documents": docs, "count": data.get("count", 0), "query": query}
    except Exception as e:
        return {"error": str(e)}

def list_documents(args):
    limit = args.get("limit", 10)
    try:
        resp = requests.get(
            f"{PAPERLESS_URL}/api/documents/",
            params={"page_size": limit, "ordering": "-created"},
            headers=_paperless_h(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        docs = [{
            "id": d["id"],
            "title": d.get("title", ""),
            "created": d.get("created", ""),
            "tags": d.get("tags", []),
        } for d in data.get("results", [])]
        return {"documents": docs, "count": data.get("count", 0)}
    except Exception as e:
        return {"error": str(e)}

def upload_file(args):
    file_path = args.get("path", "")
    title = args.get("title", "")
    if not file_path: return {"error": "file path is required"}
    if not os.path.isfile(file_path): return {"error": f"File not found: {file_path}"}
    try:
        os.makedirs(CONSUME_DIR, exist_ok=True)
        dest = os.path.join(CONSUME_DIR, os.path.basename(file_path))
        import shutil
        shutil.copy2(file_path, dest)
        return {"status": "uploaded", "file": os.path.basename(file_path), "message": "Copied to consume folder. Paperless will auto-import it shortly."}
    except Exception as e:
        return {"error": str(e)}


def _load_feeds():
    if os.path.exists(FEEDS_FILE):
        with open(FEEDS_FILE) as f:
            return json.load(f)
    return DEFAULT_FEEDS

def _save_feeds(feeds):
    os.makedirs(os.path.dirname(FEEDS_FILE), exist_ok=True)
    with open(FEEDS_FILE, "w") as f:
        json.dump(feeds, f, indent=2)

def fetch_feed(args):
    url = args.get("url", "")
    limit = args.get("limit", 5)
    if not url: return {"error": "url required"}
    items = _parse_feed(url, limit)
    return {"articles": items, "count": len(items), "url": url}

def fetch_category(args):
    category = args.get("category", "")
    limit = args.get("limit", 5)
    if not category: return {"error": "category required", "available": list(_load_feeds().keys())}
    feeds = _load_feeds()
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
    feeds = _load_feeds()
    result = {}
    for cat, feed_list in feeds.items():
        result[cat] = [f["name"] for f in feed_list]
    return {"categories": result}

def add_feed(args):
    category = args.get("category", "")
    name = args.get("name", "")
    url = args.get("url", "")
    if not category or not name or not url: return {"error": "category, name, and url required"}
    feeds = _load_feeds()
    if category not in feeds: feeds[category] = []
    feeds[category].append({"name": name, "url": url})
    _save_feeds(feeds)
    return {"status": "added", "feed": {"name": name, "url": url, "category": category}}


TOOL_SCHEMAS = [
    # Git
    {"name": "list_repos", "description": "List Git repositories on Gitea.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "Max repos (default: 20)"}}}},
    {"name": "get_commits", "description": "Get recent commits for a repo.", "inputSchema": {"type": "object", "properties": {"repo": {"type": "string", "description": "Repo name (e.g., rohit/agentharness)"}, "limit": {"type": "integer", "description": "Number of commits (default: 10)"}}, "required": ["repo"]}},
    {"name": "list_issues", "description": "List open issues for a repo.", "inputSchema": {"type": "object", "properties": {"repo": {"type": "string", "description": "Repo name"}, "state": {"type": "string", "description": "open, closed, or all (default: open)"}}, "required": ["repo"]}},
    {"name": "create_issue", "description": "Create a new issue on a Gitea repo.",    "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}}, "required": ["repo", "title"]}},
    # Paperless
    {"name": "search_documents", "description": "Search for documents in Paperless.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}, "limit": {"type": "integer", "description": "Max results (default: 10)"}}, "required": ["query"]}},
    {"name": "list_documents", "description": "List recent documents in Paperless.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "Max results (default: 10)"}}}},
    {"name": "upload_file", "description": "Upload a file to Paperless consume directory.",        "inputSchema": {"type": "object", "properties": {"path": {"type": "string", "description": "File path to upload"}, "title": {"type": "string", "description": "Optional title"}}, "required": ["path"]}},
    # RSS
    {"name": "fetch_feed", "description": "Fetch articles from a specific RSS feed URL.",
        "inputSchema": {"type": "object", "properties": {"url": {"type": "string", "description": "RSS feed URL"}, "limit": {"type": "integer", "description": "Max articles (default: 5)"}}, "required": ["url"]}},
    {"name": "fetch_category", "description": "Fetch articles from all feeds in a category.",
        "inputSchema": {"type": "object", "properties": {"category": {"type": "string", "description": "Category name (e.g., ai_news, homelab, llm)"}, "limit": {"type": "integer", "description": "Max articles per feed (default: 5)"}}, "required": ["category"]}},
    {"name": "list_feeds", "description": "List all configured RSS feed categories and feeds.",        "inputSchema": {"type": "object", "properties": {}}},
    {"name": "add_feed", "description": "Add a new RSS feed to a category.",
        "inputSchema": {"type": "object", "properties": {"category": {"type": "string"}, "name": {"type": "string"}, "url": {"type": "string"}}, "required": ["category", "name", "url"]}},
]


def main():
    port = int(os.environ.get("MCP_PORT", "8100"))
    s = MCPServer(name="data-management", port=8100, tools=TOOL_SCHEMAS)
    # Git
    s.register_handler("list_repos", list_repos)
    s.register_handler("get_commits", get_commits)
    s.register_handler("list_issues", list_issues)
    s.register_handler("create_issue", create_issue)
    # Paperless
    s.register_handler("search_documents", search_documents)
    s.register_handler("list_documents", list_documents)
    s.register_handler("upload_file", upload_file)
    # RSS
    s.register_handler("fetch_feed", fetch_feed)
    s.register_handler("fetch_category", fetch_category)
    s.register_handler("list_feeds", list_feeds)
    s.register_handler("add_feed", add_feed)
    log.info(f"Data Management MCP starting on :{port}")
    s.start()


if __name__ == "__main__":
    main()
