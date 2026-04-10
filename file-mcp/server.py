"""File Manager MCP server. Provides file operations across mounted volumes."""
from __future__ import annotations
import os
import sys
import shutil
import glob
import logging

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))

from mcp_base import MCPServer

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("file-mcp")

# Safety: only allow operations within these paths
ALLOWED_ROOTS = [
    "/mnt/usb",
    "/data",
    "/home/rohit/openclaw/data",
    "/opt",
]


def _is_safe_path(path: str) -> bool:
    """Check if path is within allowed roots."""
    abs_path = os.path.abspath(path)
    return any(abs_path.startswith(root) for root in ALLOWED_ROOTS)


def _format_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


# ── Tools ──────────────────────────────────────────────────────────────────

def list_files(args: dict) -> dict:
    """List files in a directory."""
    path = args.get("path", "")
    pattern = args.get("pattern", "*")
    recursive = args.get("recursive", False)

    if not path:
        return {"error": "path is required"}
    if not _is_safe_path(path):
        return {"error": f"Access denied: {path} is outside allowed directories"}
    if not os.path.isdir(path):
        return {"error": f"Not a directory: {path}"}

    if recursive:
        matches = glob.glob(os.path.join(path, "**", pattern), recursive=True)
    else:
        matches = glob.glob(os.path.join(path, pattern))

    files = []
    for f in sorted(matches)[:100]:  # Limit to 100 results
        try:
            stat = os.stat(f)
            files.append({
                "path": f,
                "name": os.path.basename(f),
                "is_dir": os.path.isdir(f),
                "size": _format_size(stat.st_size) if not os.path.isdir(f) else "",
                "size_bytes": stat.st_size if not os.path.isdir(f) else 0,
            })
        except OSError:
            continue

    return {"path": path, "files": files, "count": len(files), "pattern": pattern}


def copy_files(args: dict) -> dict:
    """Copy files from source to destination."""
    source = args.get("source", "")
    destination = args.get("destination", "")
    pattern = args.get("pattern", "*")

    if not source or not destination:
        return {"error": "source and destination are required"}
    if not _is_safe_path(source) or not _is_safe_path(destination):
        return {"error": "Access denied: path is outside allowed directories"}

    os.makedirs(destination, exist_ok=True)

    if os.path.isfile(source):
        # Single file copy
        dest_path = os.path.join(destination, os.path.basename(source))
        shutil.copy2(source, dest_path)
        return {"copied": 1, "files": [dest_path]}

    if not os.path.isdir(source):
        return {"error": f"Source not found: {source}"}

    # Directory copy with pattern
    matches = glob.glob(os.path.join(source, pattern))
    copied = []
    errors = []

    for f in matches:
        if os.path.isfile(f):
            try:
                dest_path = os.path.join(destination, os.path.basename(f))
                shutil.copy2(f, dest_path)
                copied.append(os.path.basename(f))
            except Exception as e:
                errors.append(f"{os.path.basename(f)}: {e}")

    return {
        "copied": len(copied),
        "errors": len(errors),
        "files": copied[:20],  # Show first 20
        "error_details": errors[:5] if errors else [],
        "source": source,
        "destination": destination,
    }


def move_files(args: dict) -> dict:
    """Move files from source to destination."""
    source = args.get("source", "")
    destination = args.get("destination", "")
    pattern = args.get("pattern", "*")

    if not source or not destination:
        return {"error": "source and destination are required"}
    if not _is_safe_path(source) or not _is_safe_path(destination):
        return {"error": "Access denied: path is outside allowed directories"}

    os.makedirs(destination, exist_ok=True)

    if os.path.isfile(source):
        dest_path = os.path.join(destination, os.path.basename(source))
        shutil.move(source, dest_path)
        return {"moved": 1, "files": [dest_path]}

    if not os.path.isdir(source):
        return {"error": f"Source not found: {source}"}

    matches = glob.glob(os.path.join(source, pattern))
    moved = []
    errors = []

    for f in matches:
        if os.path.isfile(f):
            try:
                dest_path = os.path.join(destination, os.path.basename(f))
                shutil.move(f, dest_path)
                moved.append(os.path.basename(f))
            except Exception as e:
                errors.append(f"{os.path.basename(f)}: {e}")

    return {
        "moved": len(moved),
        "errors": len(errors),
        "files": moved[:20],
        "error_details": errors[:5] if errors else [],
    }


def delete_files(args: dict) -> dict:
    """Delete files (with confirmation requirement)."""
    path = args.get("path", "")
    pattern = args.get("pattern", "")
    confirmed = args.get("confirmed", False)

    if not path:
        return {"error": "path is required"}
    if not _is_safe_path(path):
        return {"error": "Access denied: path is outside allowed directories"}

    # Single file
    if os.path.isfile(path):
        if not confirmed:
            size = _format_size(os.path.getsize(path))
            return {
                "status": "confirmation_required",
                "message": f"Delete {os.path.basename(path)} ({size})?",
                "path": path,
            }
        os.remove(path)
        return {"deleted": 1, "files": [path]}

    # Pattern in directory
    if not pattern:
        return {"error": "pattern is required when deleting from a directory"}

    matches = [f for f in glob.glob(os.path.join(path, pattern)) if os.path.isfile(f)]

    if not confirmed:
        total_size = sum(os.path.getsize(f) for f in matches)
        return {
            "status": "confirmation_required",
            "message": f"Delete {len(matches)} files ({_format_size(total_size)}) matching '{pattern}' in {path}?",
            "count": len(matches),
            "files": [os.path.basename(f) for f in matches[:10]],
        }

    deleted = []
    for f in matches:
        try:
            os.remove(f)
            deleted.append(os.path.basename(f))
        except Exception:
            pass

    return {"deleted": len(deleted), "files": deleted[:20]}


def disk_usage(args: dict) -> dict:
    """Show disk usage for a path."""
    path = args.get("path", "/")

    if not _is_safe_path(path) and path != "/":
        return {"error": "Access denied: path is outside allowed directories"}

    try:
        usage = shutil.disk_usage(path)
        return {
            "path": path,
            "total": _format_size(usage.total),
            "used": _format_size(usage.used),
            "free": _format_size(usage.free),
            "percent_used": round(usage.used / usage.total * 100, 1),
        }
    except Exception as e:
        return {"error": str(e)}


def find_files(args: dict) -> dict:
    """Search for files by name pattern across allowed directories."""
    pattern = args.get("pattern", "")
    search_path = args.get("path", "")

    if not pattern:
        return {"error": "pattern is required (e.g., '*.pdf', 'resume*')"}

    paths_to_search = [search_path] if search_path and _is_safe_path(search_path) else ALLOWED_ROOTS

    results = []
    for root in paths_to_search:
        if not os.path.isdir(root):
            continue
        for match in glob.glob(os.path.join(root, "**", pattern), recursive=True):
            if os.path.isfile(match):
                try:
                    stat = os.stat(match)
                    results.append({
                        "path": match,
                        "name": os.path.basename(match),
                        "size": _format_size(stat.st_size),
                    })
                except OSError:
                    continue
            if len(results) >= 50:
                break
        if len(results) >= 50:
            break

    return {"pattern": pattern, "results": results, "count": len(results)}


# ── Tool Schemas ───────────────────────────────────────────────────────────

TOOL_SCHEMAS = [
    {
        "name": "list_files",
        "description": "List files and directories at a given path. Use to explore the filesystem.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list (e.g., /mnt/usb/ebooks)"},
                "pattern": {"type": "string", "description": "Glob pattern to filter (e.g., *.pdf). Default: *"},
                "recursive": {"type": "boolean", "description": "Search subdirectories too. Default: false"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "copy_files",
        "description": "Copy files from source to destination. Use to import files into Paperless, move media, etc.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source file or directory path"},
                "destination": {"type": "string", "description": "Destination directory path"},
                "pattern": {"type": "string", "description": "Glob pattern when copying from directory (e.g., *.pdf). Default: *"},
            },
            "required": ["source", "destination"],
        },
    },
    {
        "name": "move_files",
        "description": "Move files from source to destination.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source file or directory path"},
                "destination": {"type": "string", "description": "Destination directory path"},
                "pattern": {"type": "string", "description": "Glob pattern when moving from directory. Default: *"},
            },
            "required": ["source", "destination"],
        },
    },
    {
        "name": "delete_files",
        "description": "Delete files. Requires confirmation before deleting.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path or directory containing files to delete"},
                "pattern": {"type": "string", "description": "Glob pattern when deleting from directory (e.g., *.tmp)"},
                "confirmed": {"type": "boolean", "description": "Set true after user confirms deletion"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_files",
        "description": "Search for files by name pattern across the homelab filesystem.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern to search for (e.g., *.pdf, resume*, *.epub)"},
                "path": {"type": "string", "description": "Optional: limit search to this directory"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "disk_usage",
        "description": "Show disk space usage for a path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to check (default: /)"},
            },
        },
    },
]


def main():
    port = int(os.environ.get("MCP_PORT", "8097"))

    server = MCPServer(name="files", port=port, tools=TOOL_SCHEMAS)

    server.register_handler("list_files", list_files)
    server.register_handler("copy_files", copy_files)
    server.register_handler("move_files", move_files)
    server.register_handler("delete_files", delete_files)
    server.register_handler("find_files", find_files)
    server.register_handler("disk_usage", disk_usage)

    log.info(f"File MCP starting on :{port} with {len(TOOL_SCHEMAS)} tools")
    log.info(f"Allowed paths: {', '.join(ALLOWED_ROOTS)}")
    server.start()


if __name__ == "__main__":
    main()
