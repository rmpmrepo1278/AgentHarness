#!/usr/bin/env python3
"""File Manager MCP - File operations across mounted volumes."""
from __future__ import annotations
import os
import sys
import shutil
import glob
import logging
import json
from pathlib import Path

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
    os.environ.get("OPENCLAW_DATA_DIR", "/home/rohit/openclaw/data"),
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
    for f in sorted(matches)[:100]:
        try:
            stat = os.stat(f)
            files.append({"path": f, "name": os.path.basename(f), "is_dir": os.path.isdir(f),
                         "size": _format_size(stat.st_size) if not os.path.isdir(f) else "",
                         "size_bytes": stat.st_size if not os.path.isdir(f) else 0})
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
        dest_path = os.path.join(destination, os.path.basename(source))
        shutil.copy2(source, dest_path)
        return {"copied": 1, "files": [dest_path]}

    if not os.path.isdir(source):
        return {"error": f"Source not found: {source}"}

    copied = []
    for src_file in glob.glob(os.path.join(source, pattern)):
        if os.path.isfile(src_file):
            dest = os.path.join(destination, os.path.basename(src_file))
            shutil.copy2(src_file, dest)
            copied.append(dest)

    return {"copied": len(copied), "files": copied}


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

    moved = []
    if os.path.isfile(source):
        dest_path = os.path.join(destination, os.path.basename(source))
        shutil.move(source, dest_path)
        moved.append(dest_path)
    elif os.path.isdir(source):
        for src_file in glob.glob(os.path.join(source, pattern)):
            if os.path.isfile(src_file):
                dest = os.path.join(destination, os.path.basename(src_file))
                shutil.move(src_file, dest)
                moved.append(dest)
    else:
        return {"error": f"Source not found: {source}"}

    return {"moved": len(moved), "files": moved}


def delete_files(args: dict) -> dict:
    """Delete files matching pattern."""
    path = args.get("path", "")
    pattern = args.get("pattern", "*")

    if not path:
        return {"error": "path is required"}
    if not _is_safe_path(path):
        return {"error": "Access denied: path is outside allowed directories"}

    deleted = []
    for f in glob.glob(os.path.join(path, pattern)):
        if os.path.isfile(f):
            os.unlink(f)
            deleted.append(f)

    return {"deleted": len(deleted), "files": deleted}


def file_info(args: dict) -> dict:
    """Get detailed information about a file."""
    path = args.get("path", "")
    if not path:
        return {"error": "path is required"}
    if not _is_safe_path(path):
        return {"error": "Access denied"}
    if not os.path.exists(path):
        return {"error": f"File not found: {path}"}

    stat = os.stat(path)
    return {
        "path": path,
        "name": os.path.basename(path),
        "is_dir": os.path.isdir(path),
        "size": stat.st_size,
        "size_human": _format_size(stat.st_size),
        "mode": oct(stat.st_mode),
        "uid": stat.st_uid,
        "gid": stat.st_gid,
        "atime": stat.st_atime,
        "mtime": stat.st_mtime,
        "ctime": stat.st_ctime,
    }


def _format_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


TOOL_SCHEMAS = [
    {"name": "list_files", "description": "List files in a directory.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "pattern": {"type": "string", "default": "*"}, "recursive": {"type": "boolean", "default": False}}, "required": ["path"]}},
    {"name": "copy_files", "description": "Copy files from source to destination.", "inputSchema": {"type": "object", "properties": {"source": {"type": "string"}, "destination": {"type": "string"}, "pattern": {"type": "string", "default": "*"}}, "required": ["source", "destination"]}},
    {"name": "move_files", "description": "Move files from source to destination.",        "inputSchema": {"type": "object", "properties": {"source": {"type": "string"}, "destination": {"type": "string"}, "pattern": {"type": "string", "default": "*"}}, "required": ["source", "destination"]}},
    {"name": "delete_files", "description": "Delete files matching pattern.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "pattern": {"type": "string", "default": "*"}}, "required": ["path"]}},
    {"name": "file_info", "description": "Get detailed information about a file.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
]


def main():
    from mcp_base import MCPServer
    import os
    import logging
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    log = logging.getLogger("file-mcp")
    s = MCPServer(name="infrastructure-files", port=8097)
    s.register_handler("list_files", list_files)
    s.register_handler("copy_files", copy_files)
    s.register_handler("move_files", move_files)
    s.register_handler("delete_files", delete_files)
    s.register_handler("file_info", file_info)
    log.info("File MCP starting on :8097")
    s.start()


if __name__ == "__main__":
    main()