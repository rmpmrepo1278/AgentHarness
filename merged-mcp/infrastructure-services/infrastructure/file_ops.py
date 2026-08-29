#!/usr/bin/env python3
"""File Manager MCP - File operations across mounted volumes."""
from __future__ import annotations

import glob
import logging
import os
import shutil
import sys

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


def read_file(args: dict) -> dict:
    """Read contents of a file."""
    path = args.get("path", "")
    encoding = args.get("encoding", "utf-8")
    max_size = args.get("max_size", 1048576)  # 1MB default

    if not path:
        return {"error": "path is required"}
    if not _is_safe_path(path):
        return {"error": "Access denied"}
    if not os.path.isfile(path):
        return {"error": f"File not found: {path}"}

    stat = os.stat(path)
    if stat.st_size > max_size:
        return {"error": f"File too large: {stat.st_size} bytes (max {max_size})"}

    try:
        with open(path, encoding=encoding) as f:
            content = f.read()
        return {"path": path, "content": content, "size": stat.st_size}
    except Exception as e:
        return {"error": f"Failed to read file: {e}"}


def write_file(args: dict) -> dict:
    """Write content to a file."""
    path = args.get("path", "")
    content = args.get("content", "")
    encoding = args.get("encoding", "utf-8")
    create_dirs = args.get("create_dirs", True)

    if not path:
        return {"error": "path is required"}
    if not _is_safe_path(path):
        return {"error": "Access denied"}

    try:
        if create_dirs:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding=encoding) as f:
            f.write(content)
        return {"path": path, "bytes_written": len(content.encode(encoding))}
    except Exception as e:
        return {"error": f"Failed to write file: {e}"}


def delete_file(args: dict) -> dict:
    """Delete a single file."""
    path = args.get("path", "")

    if not path:
        return {"error": "path is required"}
    if not _is_safe_path(path):
        return {"error": "Access denied"}
    if not os.path.isfile(path):
        return {"error": f"File not found: {path}"}

    try:
        os.unlink(path)
        return {"deleted": True, "path": path}
    except Exception as e:
        return {"error": f"Failed to delete file: {e}"}


def file_hash(args: dict) -> dict:
    """Calculate hash of a file."""
    import hashlib
    path = args.get("path", "")
    algorithm = args.get("algorithm", "sha256")

    if not path:
        return {"error": "path is required"}
    if not _is_safe_path(path):
        return {"error": "Access denied"}
    if not os.path.isfile(path):
        return {"error": f"File not found: {path}"}

    try:
        hasher = hashlib.new(algorithm)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return {"path": path, "algorithm": algorithm, "hash": hasher.hexdigest()}
    except Exception as e:
        return {"error": f"Failed to calculate hash: {e}"}


def safe_remove(args: dict) -> dict:
    """Safely remove file or directory with backup."""
    import shutil
    import time
    path = args.get("path", "")
    backup_dir = args.get("backup_dir", "/tmp/mcp_backups")

    if not path:
        return {"error": "path is required"}
    if not _is_safe_path(path):
        return {"error": "Access denied"}

    try:
        os.makedirs(backup_dir, exist_ok=True)
        if os.path.exists(path):
            backup_name = os.path.basename(path) + f".{int(time.time())}.bak"
            backup_path = os.path.join(backup_dir, backup_name)
            if os.path.isdir(path):
                shutil.copytree(path, backup_path)
            else:
                shutil.copy2(path, backup_path)
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.unlink(path)
            return {"removed": True, "path": path, "backup": backup_path}
        return {"removed": False, "path": path, "reason": "not found"}
    except Exception as e:
        return {"error": f"Failed to safely remove: {e}"}


def ensure_backup(args: dict) -> dict:
    """Ensure a backup of a file exists before modifying."""
    import shutil
    import time
    path = args.get("path", "")
    backup_dir = args.get("backup_dir", "/tmp/mcp_backups")

    if not path:
        return {"error": "path is required"}
    if not _is_safe_path(path):
        return {"error": "Access denied"}
    if not os.path.isfile(path):
        return {"error": f"File not found: {path}"}

    try:
        os.makedirs(backup_dir, exist_ok=True)
        backup_name = os.path.basename(path) + f".{int(time.time())}.bak"
        backup_path = os.path.join(backup_dir, backup_name)
        shutil.copy2(path, backup_path)
        return {"backed_up": True, "path": path, "backup": backup_path}
    except Exception as e:
        return {"error": f"Failed to create backup: {e}"}


def sync_directories(args: dict) -> dict:
    """Sync two directories (one-way)."""
    import shutil
    source = args.get("source", "")
    destination = args.get("destination", "")
    delete_extra = args.get("delete_extra", False)

    if not source or not destination:
        return {"error": "source and destination are required"}
    if not _is_safe_path(source) or not _is_safe_path(destination):
        return {"error": "Access denied"}

    try:
        if not os.path.isdir(source):
            return {"error": f"Source is not a directory: {source}"}
        os.makedirs(destination, exist_ok=True)

        copied = 0
        deleted = 0
        for root, dirs, files in os.walk(source):
            rel = os.path.relpath(root, source)
            if rel == ".":
                dest_root = destination
            else:
                dest_root = os.path.join(destination, rel)
            os.makedirs(dest_root, exist_ok=True)

            for f in files:
                src_file = os.path.join(root, f)
                dst_file = os.path.join(dest_root, f)
                shutil.copy2(src_file, dst_file)
                copied += 1

        if delete_extra:
            for root, dirs, files in os.walk(destination):
                rel = os.path.relpath(root, destination)
                if rel == ".":
                    src_root = source
                else:
                    src_root = os.path.join(source, rel)
                if not os.path.isdir(src_root):
                    for f in files:
                        dst_file = os.path.join(root, f)
                        os.unlink(dst_file)
                        deleted += 1
                    for d in dirs:
                        dst_dir = os.path.join(root, d)
                        shutil.rmtree(dst_dir)
                        deleted += 1

        return {"synced": True, "copied": copied, "deleted": deleted}
    except Exception as e:
        return {"error": f"Failed to sync directories: {e}"}


# Add read_file, write_file, delete_file, file_hash, safe_remove, ensure_backup, sync_directories to TOOL_SCHEMAS
TOOL_SCHEMAS = [
    {"name": "list_files", "description": "List files in a directory.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "pattern": {"type": "string", "default": "*"}, "recursive": {"type": "boolean", "default": False}}, "required": ["path"]}},
    {"name": "copy_files", "description": "Copy files from source to destination.", "inputSchema": {"type": "object", "properties": {"source": {"type": "string"}, "destination": {"type": "string"}, "pattern": {"type": "string", "default": "*"}}, "required": ["source", "destination"]}},
    {"name": "move_files", "description": "Move files from source to destination.", "inputSchema": {"type": "object", "properties": {"source": {"type": "string"}, "destination": {"type": "string"}, "pattern": {"type": "string", "default": "*"}}, "required": ["source", "destination"]}},
    {"name": "delete_files", "description": "Delete files matching pattern.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "pattern": {"type": "string", "default": "*"}}, "required": ["path"]}},
    {"name": "file_info", "description": "Get detailed information about a file.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "read_file", "description": "Read contents of a file.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "encoding": {"type": "string", "default": "utf-8"}, "max_size": {"type": "integer", "default": 1048576}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "encoding": {"type": "string", "default": "utf-8"}, "create_dirs": {"type": "boolean", "default": True}}, "required": ["path", "content"]}},
    {"name": "delete_file", "description": "Delete a single file.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "file_hash", "description": "Calculate hash of a file.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "algorithm": {"type": "string", "default": "sha256"}}, "required": ["path"]}},
    {"name": "safe_remove", "description": "Safely remove file/directory with backup.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "backup_dir": {"type": "string", "default": "/tmp/mcp_backups"}}, "required": ["path"]}},
    {"name": "ensure_backup", "description": "Ensure backup of file before modifying.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "backup_dir": {"type": "string", "default": "/tmp/mcp_backups"}}, "required": ["path"]}},
    {"name": "sync_directories", "description": "Sync two directories (one-way).", "inputSchema": {"type": "object", "properties": {"source": {"type": "string"}, "destination": {"type": "string"}, "delete_extra": {"type": "boolean", "default": False}}, "required": ["source", "destination"]}},
]


def main():
    import logging
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    log = logging.getLogger("file-mcp")
    s = MCPServer(name="infrastructure-files", port=8097)
    s.register_handler("list_files", list_files)
    s.register_handler("copy_files", copy_files)
    s.register_handler("move_files", move_files)
    s.register_handler("delete_files", delete_files)
    s.register_handler("file_info", file_info)
    s.register_handler("read_file", read_file)
    s.register_handler("write_file", write_file)
    s.register_handler("delete_file", delete_file)
    s.register_handler("file_hash", file_hash)
    s.register_handler("safe_remove", safe_remove)
    s.register_handler("ensure_backup", ensure_backup)
    s.register_handler("sync_directories", sync_directories)
    log.info("File MCP starting on :8097")
    s.start()


if __name__ == "__main__":
    main()
