"""Paperless-ngx MCP server. Upload, search, and manage documents."""
from __future__ import annotations
import os
import sys
import logging
import requests
import base64

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("paperless-mcp")

PAPERLESS_URL = os.environ.get("PAPERLESS_URL", "http://127.0.0.1:8000")
PAPERLESS_TOKEN = os.environ.get("PAPERLESS_TOKEN", "")
CONSUME_DIR = os.environ.get("CONSUME_DIR", "/home/rohit/openclaw/data/paperless/consume")


def _headers():
    h = {"Accept": "application/json"}
    if PAPERLESS_TOKEN:
        h["Authorization"] = f"Token {PAPERLESS_TOKEN}"
    return h


def search_documents(args: dict) -> dict:
    """Search for documents in Paperless."""
    query = args.get("query", "")
    limit = args.get("limit", 10)
    if not query:
        return {"error": "query is required"}
    try:
        resp = requests.get(
            f"{PAPERLESS_URL}/api/documents/",
            params={"query": query, "page_size": limit},
            headers=_headers(),
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


def list_documents(args: dict) -> dict:
    """List recent documents in Paperless."""
    limit = args.get("limit", 10)
    try:
        resp = requests.get(
            f"{PAPERLESS_URL}/api/documents/",
            params={"page_size": limit, "ordering": "-created"},
            headers=_headers(),
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


def upload_file(args: dict) -> dict:
    """Upload a file to Paperless by copying it to the consume directory."""
    file_path = args.get("path", "")
    title = args.get("title", "")

    if not file_path:
        return {"error": "file path is required"}
    if not os.path.isfile(file_path):
        return {"error": f"File not found: {file_path}"}

    try:
        import shutil
        os.makedirs(CONSUME_DIR, exist_ok=True)
        dest = os.path.join(CONSUME_DIR, os.path.basename(file_path))
        shutil.copy2(file_path, dest)
        return {
            "status": "uploaded",
            "file": os.path.basename(file_path),
            "message": f"Copied to consume folder. Paperless will auto-import it shortly.",
        }
    except Exception as e:
        return {"error": str(e)}


def bulk_import(args: dict) -> dict:
    """Import multiple files from a directory into Paperless."""
    source_dir = args.get("source", "")
    pattern = args.get("pattern", "*")

    if not source_dir:
        return {"error": "source directory is required"}
    if not os.path.isdir(source_dir):
        return {"error": f"Directory not found: {source_dir}"}

    import shutil
    import glob

    os.makedirs(CONSUME_DIR, exist_ok=True)
    matches = glob.glob(os.path.join(source_dir, pattern))
    files = [f for f in matches if os.path.isfile(f)]

    copied = []
    errors = []
    for f in files:
        try:
            dest = os.path.join(CONSUME_DIR, os.path.basename(f))
            shutil.copy2(f, dest)
            copied.append(os.path.basename(f))
        except Exception as e:
            errors.append(f"{os.path.basename(f)}: {e}")

    return {
        "imported": len(copied),
        "errors": len(errors),
        "files": copied[:20],
        "error_details": errors[:5] if errors else [],
        "message": f"Copied {len(copied)} files to Paperless consume folder. They will be auto-imported.",
    }


def list_tags(args: dict) -> dict:
    """List all tags in Paperless."""
    try:
        resp = requests.get(f"{PAPERLESS_URL}/api/tags/", headers=_headers(), timeout=10)
        resp.raise_for_status()
        tags = [{"id": t["id"], "name": t["name"], "document_count": t.get("document_count", 0)}
                for t in resp.json().get("results", [])]
        return {"tags": tags, "count": len(tags)}
    except Exception as e:
        return {"error": str(e)}


def tag_document(args: dict) -> dict:
    """Add tags to a document."""
    doc_id = args.get("id")
    tag_ids = args.get("tags", [])
    if not doc_id:
        return {"error": "document id is required"}
    try:
        # Get current tags
        resp = requests.get(f"{PAPERLESS_URL}/api/documents/{doc_id}/", headers=_headers(), timeout=10)
        resp.raise_for_status()
        current_tags = resp.json().get("tags", [])
        new_tags = list(set(current_tags + tag_ids))

        # Update
        resp = requests.patch(
            f"{PAPERLESS_URL}/api/documents/{doc_id}/",
            json={"tags": new_tags},
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        return {"status": "tagged", "document_id": doc_id, "tags": new_tags}
    except Exception as e:
        return {"error": str(e)}


TOOL_SCHEMAS = [
    {
        "name": "search_documents",
        "description": "Search for documents in Paperless-ngx by keyword.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default: 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_documents",
        "description": "List recent documents in Paperless-ngx.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "Number of documents (default: 10)"}},
        },
    },
    {
        "name": "upload_file",
        "description": "Upload a single file to Paperless-ngx for document management.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full file path to upload"},
                "title": {"type": "string", "description": "Optional document title"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "bulk_import",
        "description": "Import multiple files from a directory into Paperless-ngx. Use to import ebooks, PDFs, etc.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source directory path"},
                "pattern": {"type": "string", "description": "File pattern (e.g., *.pdf, *.epub). Default: *"},
            },
            "required": ["source"],
        },
    },
    {
        "name": "list_tags",
        "description": "List all document tags in Paperless-ngx.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tag_document",
        "description": "Add tags to a document in Paperless-ngx.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Document ID"},
                "tags": {"type": "array", "items": {"type": "integer"}, "description": "Tag IDs to add"},
            },
            "required": ["id", "tags"],
        },
    },
]


def main():
    port = int(os.environ.get("MCP_PORT", "8099"))
    server = MCPServer(name="paperless", port=port, tools=TOOL_SCHEMAS)

    server.register_handler("search_documents", search_documents)
    server.register_handler("list_documents", list_documents)
    server.register_handler("upload_file", upload_file)
    server.register_handler("bulk_import", bulk_import)
    server.register_handler("list_tags", list_tags)
    server.register_handler("tag_document", tag_document)

    log.info(f"Paperless MCP starting on :{port} with {len(TOOL_SCHEMAS)} tools")
    server.start()


if __name__ == "__main__":
    main()
