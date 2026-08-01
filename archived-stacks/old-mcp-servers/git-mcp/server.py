"""Git/Gitea MCP server. Manage repos, commits, issues on local Gitea instance."""
from __future__ import annotations
import os, sys, logging, requests
sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("git-mcp")

GITEA_URL = os.environ.get("GITEA_URL", "http://127.0.0.1:3001")
GITEA_TOKEN = os.environ.get("GITEA_TOKEN", "")

def _h():
    h = {"Content-Type": "application/json"}
    if GITEA_TOKEN: h["Authorization"] = f"token {GITEA_TOKEN}"
    return h

def list_repos(args):
    try:
        resp = requests.get(f"{GITEA_URL}/api/v1/repos/search", params={"limit": args.get("limit", 20)}, headers=_h(), timeout=10)
        resp.raise_for_status()
        return {"repos": [{"name": r["full_name"], "description": r.get("description", ""), "stars": r.get("stars_count", 0), "updated": r.get("updated_at", "")} for r in resp.json().get("data", [])], "count": len(resp.json().get("data", []))}
    except Exception as e: return {"error": str(e)}

def get_commits(args):
    repo = args.get("repo", "")
    limit = args.get("limit", 10)
    if not repo: return {"error": "repo required (e.g., rohit/agentharness)"}
    try:
        resp = requests.get(f"{GITEA_URL}/api/v1/repos/{repo}/commits", params={"limit": limit}, headers=_h(), timeout=10)
        resp.raise_for_status()
        return {"commits": [{"sha": c["sha"][:8], "message": c["commit"]["message"].split("\n")[0], "author": c["commit"]["author"]["name"], "date": c["commit"]["author"]["date"]} for c in resp.json()], "repo": repo}
    except Exception as e: return {"error": str(e)}

def list_issues(args):
    repo = args.get("repo", "")
    if not repo: return {"error": "repo required"}
    try:
        resp = requests.get(f"{GITEA_URL}/api/v1/repos/{repo}/issues", params={"state": args.get("state", "open"), "limit": 20}, headers=_h(), timeout=10)
        resp.raise_for_status()
        return {"issues": [{"id": i["number"], "title": i["title"], "state": i["state"], "created": i.get("created_at", "")} for i in resp.json()]}
    except Exception as e: return {"error": str(e)}

def create_issue(args):
    repo = args.get("repo", "")
    title = args.get("title", "")
    body = args.get("body", "")
    if not repo or not title: return {"error": "repo and title required"}
    try:
        resp = requests.post(f"{GITEA_URL}/api/v1/repos/{repo}/issues", json={"title": title, "body": body}, headers=_h(), timeout=10)
        resp.raise_for_status()
        i = resp.json()
        return {"status": "created", "issue_number": i["number"], "url": i.get("html_url", "")}
    except Exception as e: return {"error": str(e)}

TOOL_SCHEMAS = [
    {"name": "list_repos", "description": "List Git repositories on Gitea.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "Max repos (default: 20)"}}}},
    {"name": "get_commits", "description": "Get recent commits for a repo.", "inputSchema": {"type": "object", "properties": {"repo": {"type": "string", "description": "Repo name (e.g., rohit/agentharness)"}, "limit": {"type": "integer", "description": "Number of commits (default: 10)"}}, "required": ["repo"]}},
    {"name": "list_issues", "description": "List open issues for a repo.", "inputSchema": {"type": "object", "properties": {"repo": {"type": "string", "description": "Repo name"}, "state": {"type": "string", "description": "open, closed, or all (default: open)"}}, "required": ["repo"]}},
    {"name": "create_issue", "description": "Create a new issue on a Gitea repo.", "inputSchema": {"type": "object", "properties": {"repo": {"type": "string", "description": "Repo name"}, "title": {"type": "string", "description": "Issue title"}, "body": {"type": "string", "description": "Issue body/description"}}, "required": ["repo", "title"]}},
]

def main():
    port = int(os.environ.get("MCP_PORT", "8100"))
    s = MCPServer(name="git", port=port, tools=TOOL_SCHEMAS)
    for n, fn in [("list_repos", list_repos), ("get_commits", get_commits), ("list_issues", list_issues), ("create_issue", create_issue)]:
        s.register_handler(n, fn)
    log.info(f"Git MCP starting on :{port}")
    s.start()

if __name__ == "__main__": main()
