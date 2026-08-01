"""DesktopCommander MCP — file ops, process mgmt, search.

Provides terminal/file/process tools for the homelab using Python's
os/subprocess directly (no Node.js dependency).

Tools:
  read_file       — Read file contents with pagination
  write_file      — Write file contents
  edit_block      — Surgical text replacements
  list_directory  — List directory contents with depth control
  search_files    — Search files by name or content pattern
  start_process   — Execute terminal commands with output streaming
  list_processes  — List running processes
  kill_process    — Terminate a process by PID
  get_file_info   — Get file metadata
"""
from __future__ import annotations
import os
import sys
import json
import subprocess
import logging
import stat
import time
import signal
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("desktop-commander")

MAX_OUTPUT = 8000
MAX_FILE_SIZE = 1024 * 1024
PROCESS_TIMEOUT = 300


def _path_resolve(path_str: str) -> Path:
    return Path(path_str).expanduser().resolve()


def _ensure_safe_path(path: Path) -> Path:
    return _path_resolve(str(path))


def _run(cmd: list[str], timeout: int = 30, env: dict | None = None,
         cwd: str | None = None) -> dict:
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            env=env or os.environ, cwd=cwd,
        )
        stdout = r.stdout.strip()
        stderr = r.stderr.strip()
        if len(stdout) > MAX_OUTPUT:
            stdout = stdout[:MAX_OUTPUT] + "\n... (truncated)"
        if len(stderr) > MAX_OUTPUT:
            stderr = stderr[:MAX_OUTPUT] + "\n... (truncated)"
        return {
            "status": "ok" if r.returncode == 0 else "failed",
            "exit_code": r.returncode,
            "output": stdout,
            "errors": stderr,
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": f"Command timed out after {timeout}s"}
    except FileNotFoundError as e:
        return {"status": "not_found", "error": str(e)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def read_file(args: dict) -> dict:
    path = _ensure_safe_path(args.get("path", ""))
    offset = args.get("offset", 0)
    limit = args.get("limit", 200)

    if not path.exists():
        return {"error": f"File not found: {path}"}
    if not path.is_file():
        return {"error": f"Not a file: {path}"}

    file_size = path.stat().st_size
    if file_size > MAX_FILE_SIZE:
        return {"error": f"File too large ({file_size} bytes). Max: {MAX_FILE_SIZE}"}

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return {"error": f"Cannot read file: {e}"}

    total_lines = len(lines)
    start = max(0, offset)
    end = min(total_lines, offset + limit) if limit else total_lines
    content = "".join(lines[start:end])

    return {
        "path": str(path),
        "size": file_size,
        "total_lines": total_lines,
        "offset": start,
        "limit": end - start,
        "content": content,
        "truncated": end < total_lines,
    }


def write_file(args: dict) -> dict:
    path = _ensure_safe_path(args.get("path", ""))
    content = args.get("content", "")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"status": "ok", "path": str(path), "bytes": len(content)}
    except Exception as e:
        return {"error": f"Cannot write file: {e}"}


def edit_block(args: dict) -> dict:
    path = _ensure_safe_path(args.get("path", ""))
    old_text = args.get("oldText", "")
    new_text = args.get("newText", "")
    replace_all = args.get("replaceAll", False)

    if not path.exists():
        return {"error": f"File not found: {path}"}

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return {"error": f"Cannot read file: {e}"}

    if old_text not in content:
        return {"error": "oldText not found in file"}

    count = content.count(old_text)
    if count > 1 and not replace_all:
        return {"error": f"Found {count} matches. Use replaceAll=true or provide more context."}

    if replace_all:
        new_content = content.replace(old_text, new_text)
    else:
        new_content = content.replace(old_text, new_text, 1)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return {
            "status": "ok",
            "path": str(path),
            "replacements": count if replace_all else 1,
        }
    except Exception as e:
        return {"error": f"Cannot write file: {e}"}


def list_directory(args: dict) -> dict:
    path = _ensure_safe_path(args.get("path", "."))
    depth = min(args.get("depth", 1), 5)

    if not path.exists():
        return {"error": f"Path not found: {path}"}
    if not path.is_dir():
        return {"error": f"Not a directory: {path}"}

    def _walk(p: Path, current_depth: int) -> list:
        if current_depth > depth:
            return []
        entries = []
        try:
            for child in sorted(p.iterdir()):
                is_dir = child.is_dir()
                entry = {
                    "name": child.name,
                    "path": str(child),
                    "type": "directory" if is_dir else "file",
                    "size": child.stat().st_size if not is_dir else 0,
                    "modified": datetime.fromtimestamp(child.stat().st_mtime).isoformat(),
                }
                entries.append(entry)
                if is_dir and current_depth < depth:
                    entries.extend(_walk(child, current_depth + 1))
        except PermissionError:
            entries.append({"name": "... (permission denied)", "path": str(p / "..."), "type": "error"})
        return entries

    entries = _walk(path, 1)
    return {
        "path": str(path),
        "entries": entries,
        "count": len(entries),
        "depth": depth,
    }


def search_files(args: dict) -> dict:
    root = _ensure_safe_path(args.get("root", "."))
    pattern = args.get("pattern", "")
    content_pattern = args.get("contentPattern", "")
    max_results = min(args.get("maxResults", 100), 500)

    if not pattern and not content_pattern:
        return {"error": "Provide pattern (name glob) or contentPattern (regex)"}

    if not root.exists() or not root.is_dir():
        return {"error": f"Invalid root directory: {root}"}

    results = []
    try:
        for p in root.rglob(pattern) if pattern else root.rglob("*"):
            if not p.is_file():
                continue
            if len(results) >= max_results:
                break
            try:
                if content_pattern:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f, 1):
                            if content_pattern in line:
                                results.append({
                                    "path": str(p),
                                    "line": i,
                                    "match": line.rstrip()[:200],
                                })
                                if len(results) >= max_results:
                                    break
                else:
                    results.append({"path": str(p), "size": p.stat().st_size})
            except (PermissionError, OSError):
                continue
    except PermissionError:
        pass

    return {
        "root": str(root),
        "pattern": pattern or content_pattern,
        "results": results,
        "count": len(results),
        "truncated": len(results) >= max_results,
    }


_started_processes: dict[str, dict] = {}
_process_counter = 0


def start_process(args: dict) -> dict:
    global _process_counter
    command = args.get("command", "")
    cwd = args.get("cwd")
    env_overrides = args.get("env", {})
    timeout = args.get("timeout", PROCESS_TIMEOUT)

    if not command:
        return {"error": "command is required"}

    _process_counter += 1
    proc_id = f"proc_{_process_counter}"

    env = os.environ.copy()
    env.update(env_overrides)

    cwd_path = _ensure_safe_path(cwd) if cwd else None

    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd_path if cwd_path and cwd_path.exists() else None,
            preexec_fn=os.setsid,
        )
        _started_processes[proc_id] = {
            "pid": proc.pid,
            "proc": proc,
            "command": command,
            "started_at": datetime.now().isoformat(),
        }

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            status = "completed" if proc.returncode == 0 else "failed"
            return {
                "processId": proc_id,
                "pid": proc.pid,
                "status": status,
                "exitCode": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT],
                "stderr": stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT],
            }
        except subprocess.TimeoutExpired:
            return {
                "processId": proc_id,
                "pid": proc.pid,
                "status": "running",
                "stdout": "(process still running -- output streaming not yet available)",
            }
    except Exception as e:
        return {"error": str(e)}


def list_processes(args: dict) -> dict:
    processes = []
    for pid, entry in list(_started_processes.items()):
        proc = entry["proc"]
        poll = proc.poll()
        processes.append({
            "processId": pid,
            "pid": entry["pid"],
            "command": entry["command"],
            "status": "running" if poll is None else "exited",
            "exitCode": poll if poll is not None else None,
            "startedAt": entry["started_at"],
        })

    try:
        r = _run(["ps", "aux", "--no-headers"], timeout=5)
        if r["status"] == "ok":
            for line in r["output"].split("\n")[:50]:
                parts = line.split(None, 10)
                if len(parts) >= 11:
                    processes.append({
                        "pid": int(parts[1]),
                        "user": parts[0],
                        "cpu": parts[2],
                        "mem": parts[3],
                        "command": parts[10][:100],
                        "status": "system",
                    })
    except Exception:
        pass

    return {"processes": processes, "count": len(processes)}


def kill_process(args: dict) -> dict:
    pid_or_id = args.get("pid", "")
    force = args.get("force", False)

    if not pid_or_id:
        return {"error": "pid (numeric or processId) is required"}

    if pid_or_id in _started_processes:
        real_pid = _started_processes[pid_or_id]["pid"]
        proc = _started_processes[pid_or_id]["proc"]
        try:
            if force:
                os.killpg(os.getpgid(real_pid), signal.SIGKILL)
            else:
                proc.terminate()
            return {"status": "terminated", "pid": real_pid, "processId": pid_or_id}
        except ProcessLookupError:
            return {"status": "not_found", "pid": real_pid}
        except Exception as e:
            return {"error": str(e)}

    try:
        pid = int(pid_or_id)
        sig = signal.SIGKILL if force else signal.SIGTERM
        os.kill(pid, sig)
        return {"status": "terminated", "pid": pid}
    except ProcessLookupError:
        return {"error": f"PID {pid_or_id} not found"}
    except ValueError:
        return {"error": f"Invalid pid: {pid_or_id}"}
    except Exception as e:
        return {"error": str(e)}


def get_file_info(args: dict) -> dict:
    path = _ensure_safe_path(args.get("path", ""))
    follow_symlinks = args.get("followSymlinks", True)

    if not path.exists():
        return {"error": f"Path not found: {path}"}

    try:
        if follow_symlinks:
            s = path.stat()
        else:
            s = path.lstat()

        info = {
            "path": str(path),
            "exists": True,
            "type": "directory" if path.is_dir() else "file",
            "size": s.st_size,
            "permissions": oct(s.st_mode)[-3:],
            "permissions_str": stat.filemode(s.st_mode),
            "modified": datetime.fromtimestamp(s.st_mtime).isoformat(),
            "accessed": datetime.fromtimestamp(s.st_atime).isoformat(),
            "created": datetime.fromtimestamp(s.st_ctime).isoformat(),
            "owner": s.st_uid,
            "group": s.st_gid,
            "is_symlink": path.is_symlink(),
        }

        if path.is_symlink():
            info["symlink_target"] = str(os.readlink(path))

        return info
    except Exception as e:
        return {"error": str(e)}


TOOL_SCHEMAS = [
    {
        "name": "read_file",
        "description": "Read file contents with pagination. Returns lines from offset to offset+limit.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to file"},
                "offset": {"type": "integer", "description": "Starting line (0-indexed, default: 0)"},
                "limit": {"type": "integer", "description": "Max lines to return (default: 200)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates parent directories if needed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to file"},
                "content": {"type": "string", "description": "File content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_block",
        "description": "Surgical text replacement in a file. Replaces oldText with newText. Use replaceAll=true to replace all occurrences.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to file"},
                "oldText": {"type": "string", "description": "Exact text to replace"},
                "newText": {"type": "string", "description": "Replacement text"},
                "replaceAll": {"type": "boolean", "description": "Replace all occurrences (default: false)"},
            },
            "required": ["path", "oldText", "newText"],
        },
    },
    {
        "name": "list_directory",
        "description": "List directory contents with configurable depth (max 5).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (default: current dir)"},
                "depth": {"type": "integer", "description": "Recursion depth (1-5, default: 1)"},
            },
        },
    },
    {
        "name": "search_files",
        "description": "Search files by name glob or content substring. Max 500 results.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Root directory to search (default: .)"},
                "pattern": {"type": "string", "description": "File name glob pattern, e.g. *.py"},
                "contentPattern": {"type": "string", "description": "Substring to search in file contents"},
                "maxResults": {"type": "integer", "description": "Max results (default: 100, max: 500)"},
            },
        },
    },
    {
        "name": "start_process",
        "description": "Execute a terminal command and wait for output. Supports env vars and working directory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "cwd": {"type": "string", "description": "Working directory (optional)"},
                "env": {"type": "object", "description": "Environment variable overrides (optional)"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default: 300)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "list_processes",
        "description": "List running processes (tracked + system ps snapshot).",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "kill_process",
        "description": "Terminate a process by numeric PID or tracked processId.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pid": {"type": "string", "description": "Numeric PID or tracked processId"},
                "force": {"type": "boolean", "description": "Use SIGKILL instead of SIGTERM (default: false)"},
            },
            "required": ["pid"],
        },
    },
    {
        "name": "get_file_info",
        "description": "Get file/directory metadata: size, permissions, timestamps, type.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to file or directory"},
                "followSymlinks": {"type": "boolean", "description": "Follow symlinks (default: true)"},
            },
            "required": ["path"],
        },
    },
]


def main():
    port = int(os.environ.get("MCP_PORT", "8126"))
    s = MCPServer(name="desktop-commander", port=port, tools=TOOL_SCHEMAS)

    handler_map = [
        ("read_file", read_file),
        ("write_file", write_file),
        ("edit_block", edit_block),
        ("list_directory", list_directory),
        ("search_files", search_files),
        ("start_process", start_process),
        ("list_processes", list_processes),
        ("kill_process", kill_process),
        ("get_file_info", get_file_info),
    ]
    for name, fn in handler_map:
        s.register_handler(name, fn)

    log.info("DesktopCommander MCP starting on :%d with %d tools", port, len(TOOL_SCHEMAS))
    s.start()


if __name__ == "__main__":
    main()