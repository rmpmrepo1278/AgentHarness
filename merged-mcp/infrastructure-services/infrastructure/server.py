#!/usr/bin/env python3
"""Combined Infrastructure Services MCP Server.

Combines: backup, doctor, file operations into a single container.
Each service runs on its own port in a separate process:
- 8102: Backup
- 8105: Doctor
- 8097: File Operations
"""
from __future__ import annotations

import multiprocessing
import os
import signal
import sys

# Import the service modules' TOOL_SCHEMAS
sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))

from mcp_base import MCPServer

from infrastructure.backup import TOOL_SCHEMAS as BACKUP_TOOLS

# Import handlers
from infrastructure.backup import backup_status, list_backups, run_backup, verify_backup
from infrastructure.doctor import TOOL_SCHEMAS as DOCTOR_TOOLS
from infrastructure.doctor import doctor_heal, doctor_runbooks, doctor_status
from infrastructure.file_ops import TOOL_SCHEMAS as FILE_TOOLS
from infrastructure.file_ops import (
    copy_files,
    delete_file,
    ensure_backup,
    file_hash,
    list_files,
    move_files,
    read_file,
    safe_remove,
    sync_directories,
    write_file,
)


def create_backup_server():
    """Create the backup MCP server."""
    s = MCPServer(name="infrastructure-backup", port=8102, tools=BACKUP_TOOLS)
    s.register_handler("run_backup", run_backup)
    s.register_handler("backup_status", backup_status)
    s.register_handler("list_backups", list_backups)
    s.register_handler("verify_backup", verify_backup)
    return s


def create_doctor_server():
    """Create the doctor MCP server."""
    s = MCPServer(name="infrastructure-doctor", port=8105, tools=DOCTOR_TOOLS)
    s.register_handler("doctor_status", doctor_status)
    s.register_handler("doctor_runbooks", doctor_runbooks)
    s.register_handler("doctor_heal", doctor_heal)
    return s


def create_file_server():
    """Create the file operations MCP server."""
    s = MCPServer(name="infrastructure-files", port=8097, tools=FILE_TOOLS)
    s.register_handler("list_files", list_files)
    s.register_handler("copy_files", copy_files)
    s.register_handler("move_files", move_files)
    s.register_handler("read_file", read_file)
    s.register_handler("write_file", write_file)
    s.register_handler("delete_file", delete_file)
    s.register_handler("file_hash", file_hash)
    s.register_handler("safe_remove", safe_remove)
    s.register_handler("ensure_backup", ensure_backup)
    s.register_handler("sync_directories", sync_directories)
    return s


def run_server(server_factory, name):
    """Run a server in a subprocess."""
    server = server_factory()
    print(f"[{name}] Starting on port {server.port}")
    server.start()


def main():
    """Run all servers in separate processes."""
    processes = []

    # Create server factories
    servers = [
        (create_backup_server, "backup-mcp"),
        (create_doctor_server, "doctor-mcp"),
        (create_file_server, "file-mcp"),
    ]

    # Start each in a separate process
    for factory, name in servers:
        p = multiprocessing.Process(target=run_server, args=(factory, name), name=name)
        p.start()
        processes.append(p)

    def shutdown(signum, frame):
        print("\nShutting down infrastructure services...")
        for p in processes:
            if p.is_alive():
                p.terminate()
                p.join(timeout=5)
                if p.is_alive():
                    p.kill()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Wait for all processes
    for p in processes:
        p.join()


if __name__ == "__main__":
    multiprocessing.set_start_method("spawn")
    main()
