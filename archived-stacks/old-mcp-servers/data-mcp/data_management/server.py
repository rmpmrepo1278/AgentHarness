#!/usr/bin/env python3
"""Combined Data Management MCP Server.

Combines: git, paperless, rss into a single server.
Each service runs on its own port:
- 8100: Git/Gitea
- 8099: Paperless
- 8104: RSS
"""
from __future__ import annotations
import os
import sys
import threading

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

# Import handlers
from data_management import (
    # Git
    list_repos, get_commits, list_issues, create_issue,
    # Paperless
    search_documents, list_documents, upload_file,
    # RSS
    fetch_feed, fetch_category, list_feeds, add_feed,
)

# Also import handlers from the module
import data_management


def create_git_server():
    """Create Git/Gitea MCP server on port 8100."""
    from mcp_base import MCPServer
    s = MCPServer(name="data-git", port=8100)
    s.register_handler("list_repos", __import__('data_management', fromlist=['list_repos']).list_repos)
    s.register_handler("get_commits", __import__('data_management', fromlist=['get_commits']).get_commits)
    s.register_handler("list_issues", __import__('data_management', fromlist=['list_issues']).list_issues)
    s.register_handler("create_issue", __import__('data_management', fromlist=['create_issue']).create_issue)
    return s


def create_paperless_server():
    """Create Paperless MCP server on port 8099."""
    from mcp_base import MCPServer
    s = MCPServer(name="data-paperless", port=8099)
    s.register_handler("search_documents", __import__('data_management', fromlist=['search_documents']).search_documents)
    s.register_handler("list_documents", __import__('data_management', fromlist=['list_documents']).list_documents)
    s.register_handler("upload_file", __import__('data_management', fromlist=['upload_file']).upload_file)
    return s


def create_rss_server():
    """Create RSS MCP server on port 8104."""
    from mcp_base import MCPServer
    s = MCPServer(name="data-rss", port=8104)
    s.register_handler("fetch_feed", __import__('data_management', fromlist=['fetch_feed']).fetch_feed)
    s.register_handler("fetch_category", __import__('data_management', fromlist=['fetch_category']).fetch_category)
    s.register_handler("list_feeds", __import__('data_management', fromlist=['list_feeds']).list_feeds)
    s.register_handler("add_feed", __import__('data_management', fromlist=['add_feed']).add_feed)
    return s


def run_servers():
    """Run all servers in separate threads."""
    import threading
    git_srv = create_git_server()
    paperless_srv = create_paperless_server()
    rss_srv = create_rss_server()

    threads = [
        threading.Thread(target=git_srv.start, daemon=True, name="git-mcp"),
        threading.Thread(target=paperless_srv.start, daemon=True, name="paperless-mcp"),
        threading.Thread(target=rss_srv.start, daemon=True, name="rss-mcp"),
    ]

    for t in threads:
        t.start()

    # Keep main thread alive
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\nShutting down data management services...")
        sys.exit(0)


if __name__ == "__main__":
    run_servers()