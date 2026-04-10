"""Media MCP server. Control Sonarr, Radarr, Lidarr, Jellyfin, qBittorrent."""
from __future__ import annotations
import os, sys, logging, requests
sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("media-mcp")

SONARR_URL = os.environ.get("SONARR_URL", "http://127.0.0.1:8989")
RADARR_URL = os.environ.get("RADARR_URL", "http://127.0.0.1:7878")
LIDARR_URL = os.environ.get("LIDARR_URL", "http://127.0.0.1:8686")
JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "http://127.0.0.1:8096")
QBIT_URL = os.environ.get("QBIT_URL", "http://127.0.0.1:8085")
SONARR_KEY = os.environ.get("SONARR_API_KEY", "")
RADARR_KEY = os.environ.get("RADARR_API_KEY", "")
LIDARR_KEY = os.environ.get("LIDARR_API_KEY", "")
JELLYFIN_KEY = os.environ.get("JELLYFIN_API_KEY", "")

def _arr_get(url, key, endpoint, params=None):
    try:
        resp = requests.get(f"{url}/api/v3/{endpoint}", params={**(params or {}), "apikey": key}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e: return {"error": str(e)}

def list_downloads(args):
    """List active downloads across all *arr apps and qBittorrent."""
    results = []
    # Sonarr queue
    data = _arr_get(SONARR_URL, SONARR_KEY, "queue")
    if isinstance(data, dict) and "records" in data:
        for r in data["records"][:10]:
            results.append({"source": "sonarr", "title": r.get("title", ""), "status": r.get("status", ""), "progress": f"{r.get('sizeleft', 0) / max(r.get('size', 1), 1) * 100:.0f}%"})
    # Radarr queue
    data = _arr_get(RADARR_URL, RADARR_KEY, "queue")
    if isinstance(data, dict) and "records" in data:
        for r in data["records"][:10]:
            results.append({"source": "radarr", "title": r.get("title", ""), "status": r.get("status", ""), "progress": f"{r.get('sizeleft', 0) / max(r.get('size', 1), 1) * 100:.0f}%"})
    # qBittorrent
    try:
        resp = requests.get(f"{QBIT_URL}/api/v2/torrents/info", params={"filter": "downloading"}, timeout=10)
        for t in resp.json()[:10]:
            results.append({"source": "qbittorrent", "title": t.get("name", ""), "status": "downloading", "progress": f"{t.get('progress', 0)*100:.0f}%"})
    except Exception: pass
    return {"downloads": results, "count": len(results)}

def search_movie(args):
    """Search for a movie to add to Radarr."""
    query = args.get("query", "")
    if not query: return {"error": "query required"}
    try:
        resp = requests.get(f"{RADARR_URL}/api/v3/movie/lookup", params={"term": query, "apikey": RADARR_KEY}, timeout=15)
        resp.raise_for_status()
        return {"results": [{"title": m.get("title", ""), "year": m.get("year", ""), "tmdbId": m.get("tmdbId", ""), "overview": m.get("overview", "")[:100]} for m in resp.json()[:5]]}
    except Exception as e: return {"error": str(e)}

def search_show(args):
    """Search for a TV show to add to Sonarr."""
    query = args.get("query", "")
    if not query: return {"error": "query required"}
    try:
        resp = requests.get(f"{SONARR_URL}/api/v3/series/lookup", params={"term": query, "apikey": SONARR_KEY}, timeout=15)
        resp.raise_for_status()
        return {"results": [{"title": s.get("title", ""), "year": s.get("year", ""), "tvdbId": s.get("tvdbId", ""), "overview": s.get("overview", "")[:100]} for s in resp.json()[:5]]}
    except Exception as e: return {"error": str(e)}

def jellyfin_libraries(args):
    """List Jellyfin media libraries."""
    try:
        resp = requests.get(f"{JELLYFIN_URL}/Library/VirtualFolders", headers={"X-Emby-Token": JELLYFIN_KEY}, timeout=10)
        resp.raise_for_status()
        return {"libraries": [{"name": l.get("Name", ""), "type": l.get("CollectionType", ""), "paths": l.get("Locations", [])} for l in resp.json()]}
    except Exception as e: return {"error": str(e)}

def jellyfin_recent(args):
    """List recently added media in Jellyfin."""
    limit = args.get("limit", 10)
    try:
        resp = requests.get(f"{JELLYFIN_URL}/Items/Latest", params={"Limit": limit}, headers={"X-Emby-Token": JELLYFIN_KEY}, timeout=10)
        resp.raise_for_status()
        return {"items": [{"name": i.get("Name", ""), "type": i.get("Type", ""), "added": i.get("DateCreated", "")} for i in resp.json()[:limit]]}
    except Exception as e: return {"error": str(e)}

TOOL_SCHEMAS = [
    {"name": "list_downloads", "description": "List active downloads across Sonarr, Radarr, and qBittorrent.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "search_movie", "description": "Search for a movie to download via Radarr.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Movie title to search"}}, "required": ["query"]}},
    {"name": "search_show", "description": "Search for a TV show to download via Sonarr.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Show title to search"}}, "required": ["query"]}},
    {"name": "jellyfin_libraries", "description": "List Jellyfin media libraries.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "jellyfin_recent", "description": "List recently added media in Jellyfin.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "Number of items (default: 10)"}}}},
]

def main():
    port = int(os.environ.get("MCP_PORT", "8101"))
    s = MCPServer(name="media", port=port, tools=TOOL_SCHEMAS)
    for n, fn in [("list_downloads", list_downloads), ("search_movie", search_movie), ("search_show", search_show), ("jellyfin_libraries", jellyfin_libraries), ("jellyfin_recent", jellyfin_recent)]:
        s.register_handler(n, fn)
    log.info(f"Media MCP starting on :{port}")
    s.start()

if __name__ == "__main__": main()
