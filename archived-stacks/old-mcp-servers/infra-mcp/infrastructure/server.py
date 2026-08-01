#!/usr/bin/env python3
"""Combined Infrastructure Services MCP Server.

Combines: backup, doctor, file operations into a single server.
Each service runs on its own port:
- 8102: Backup
- 8105: Doctor
- 8097: File Operations
"""
from __future__ import annotations
import os
import sys
import logging
import threading

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Import the service modules
from infrastructure.backup import TOOL_SCHEMAS as BACKUP_TOOLS
from infrastructure.doctor import TOOL_SCHEMAS as DOCTOR_TOOLS
from infrastructure.file_ops import TOOL_SCHEMAS as FILE_TOOLS
from infrastructure.backup import (
    run_backup, backup_status, list_backups, verify_backup
)
from infrastructure.doctor import (
    doctor_status, doctor_runbooks, doctor_heal
)
from infrastructure.file_ops import (
    list_files, copy_files, move_files, read_file, write_file,
    delete_file, file_hash, safe_remove, ensure_backup, sync_directories
)


def create_backup_server():
    """Create the backup MCP server."""
    from mcp_base import MCPServer
    import os
    s = MCPServer(name="infrastructure-backup", port=8102)
    s.register_handler("run_backup", __import__('infrastructure.backup', fromlist=['run_backup']).run_backup)
    s.register_handler("backup_status", __import__('infrastructure.backup', fromlist=['backup_status']).backup_status)
    s.register_handler("list_backups", __import__('infrastructure.backup', fromlist=['list_backups']).list_backups)
    s.register_handler("verify_backup", __import__('infrastructure.backup', fromlist=['verify_backup']).verify_backup)
    return s


def create_doctor_server():
    """Create the doctor MCP server."""
    from mcp_base import MCPServer
    import os
    from infrastructure.doctor import doctor_status, doctor_runbooks, doctor_heal
    s = MCPServer(name="infrastructure-doctor", port=8105)
    s.register_handler("doctor_status", doctor_status)
    s.register_handler("doctor_runbooks", __import__('infrastructure.doctor', fromlist=['doctor_runbooks']).doctor_runbooks)
    s.register_handler("doctor_heal", __import__('infrastructure.doctor', fromlist=['doctor_heal']).doctor_heal)
    return s


def create_file_server():
    """Create the file operations MCP server."""
    from mcp_base import MCPServer
    from infrastructure.file_ops import (
        list_files, copy_files, move_files, read_file, write_file,
        delete_file, file_hash, safe_remove, ensure_backup, sync_directories
    )
    s = MCPServer(name="infrastructure-files", port=8097)
    s.register_handler("list_files", list_files)
    s.register_handler("copy_files", copy_files)
    s.register_handler("move_files", move_files)
    s.register_handler("read_file", __import__('infrastructure.file_ops', fromlist=['read_file']).read_file)
    s.register_handler("write_file", __import__('infrastructure.file_ops', fromlist=['write_file']).write_file)
    s.register_handler("delete_file", __import__('infrastructure.file_ops', fromlist=['delete_file']).delete_file)
    s.register_handler("file_hash", __import__('infrastructure.file_ops', fromlist=['file_hash']).file_hash)
    s.register_handler("safe_remove", __import__('infrastructure.file_ops', fromlist=['safe_remove']).safe_remove)
    s.register_handler("ensure_backup", __import__('infrastructure.file_ops', fromlist=['ensure_backup']).ensure_backup)
    s.register_handler("sync_directories", __import__('infrastructure.file_ops', fromlist=['sync_directories']).sync_directories)
    return s


def run_servers():
    """Run all servers in separate threads."""
    backup_srv = create_backup_server()
    doctor_srv = create_doctor_server()
    file_srv = create_file_server()

    threads = [
        threading.Thread(target=backup_srv.start, daemon=True, name="backup-mcp"),
        threading.Thread(target=__import__('infrastructure.doctor', fromlist=['main']).main, daemon=True, name="doctor-mcp"),
        threading.Thread(target=__import__('infrastructure.file_ops', fromlist=['main']).main, daemon=True, name="file-mcp"),
    ]

    for t in threads:
        t.start()

    # Keep main thread alive
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\nShutting down infrastructure services...")
        sys.exit(0)


if __name__ == "__main__":
    run_servers()