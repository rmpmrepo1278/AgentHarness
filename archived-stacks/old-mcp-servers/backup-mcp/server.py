"""Backup MCP server. Trigger, verify, and manage backups to USB drive."""
from __future__ import annotations
import os, sys, glob, json, time, logging, subprocess, shutil
sys.path.insert(0, os.environ.get("MCP_BASE_DIR", "/mcp-base"))
from mcp_base import MCPServer

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("backup-mcp")

USB_MOUNT = os.environ.get("USB_MOUNT", "/mnt/usb")
BACKUP_DIR = os.path.join(USB_MOUNT, "backups")
BACKUP_SCRIPT = os.environ.get("BACKUP_SCRIPT", "/scripts/backup_volumes.sh")
REPORTS_DIR = os.environ.get("REPORTS_DIR", "/data/reports")

def run_backup(args):
    """Trigger a full backup now."""
    if not os.path.exists(BACKUP_SCRIPT):
        return {"error": f"Backup script not found: {BACKUP_SCRIPT}"}
    try:
        result = subprocess.run(["bash", BACKUP_SCRIPT], capture_output=True, text=True, timeout=600)
        return {"status": "completed" if result.returncode == 0 else "failed", "output": result.stdout[-500:], "errors": result.stderr[-200:] if result.returncode != 0 else ""}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "message": "Backup timed out after 10 minutes"}
    except Exception as e:
        return {"error": str(e)}

def backup_status(args):
    """Check when the last backup ran and its status."""
    if not os.path.isdir(BACKUP_DIR):
        return {"error": "No backup directory found", "path": BACKUP_DIR}
    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "20*")))
    if not backups:
        return {"status": "no_backups", "path": BACKUP_DIR}
    latest = backups[-1]
    files = glob.glob(os.path.join(latest, "*.tar.gz"))
    total_size = sum(os.path.getsize(f) for f in files)
    return {
        "latest_backup": os.path.basename(latest),
        "files": len(files),
        "total_size": f"{total_size / (1024**3):.2f} GB",
        "backups_available": len(backups),
        "oldest": os.path.basename(backups[0]),
    }

def list_backups(args):
    """List all available backup snapshots."""
    if not os.path.isdir(BACKUP_DIR):
        return {"error": "No backup directory found"}
    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "20*")), reverse=True)
    result = []
    for b in backups[:args.get("limit", 10)]:
        files = glob.glob(os.path.join(b, "*.tar.gz"))
        size = sum(os.path.getsize(f) for f in files)
        result.append({"date": os.path.basename(b), "files": len(files), "size": f"{size / (1024**3):.2f} GB"})
    return {"backups": result, "count": len(result)}

def verify_backup(args):
    """Verify integrity of the latest backup."""
    date = args.get("date", "")
    if not date:
        backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "20*")))
        if not backups: return {"error": "No backups found"}
        date = os.path.basename(backups[-1])
    backup_path = os.path.join(BACKUP_DIR, date)
    if not os.path.isdir(backup_path):
        return {"error": f"Backup not found: {date}"}
    files = glob.glob(os.path.join(backup_path, "*.tar.gz"))
    ok = []
    corrupt = []
    for f in files:
        try:
            result = subprocess.run(["tar", "tzf", f], capture_output=True, timeout=30)
            if result.returncode == 0:
                ok.append(os.path.basename(f))
            else:
                corrupt.append(os.path.basename(f))
        except Exception:
            corrupt.append(os.path.basename(f))
    return {"date": date, "verified": len(ok), "corrupt": len(corrupt), "corrupt_files": corrupt[:10]}

def disk_space(args):
    """Check USB backup drive space."""
    try:
        usage = shutil.disk_usage(USB_MOUNT)
        return {"mount": USB_MOUNT, "total": f"{usage.total / (1024**3):.1f} GB", "used": f"{usage.used / (1024**3):.1f} GB", "free": f"{usage.free / (1024**3):.1f} GB", "percent_used": round(usage.used / usage.total * 100, 1)}
    except Exception as e:
        return {"error": str(e)}

TOOL_SCHEMAS = [
    {"name": "run_backup", "description": "Run a full backup of all Docker service data to USB drive now.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "backup_status", "description": "Check when the last backup ran and how many files it contains.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_backups", "description": "List all available backup snapshots with dates and sizes.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "Max results (default: 10)"}}}},
    {"name": "verify_backup", "description": "Verify integrity of a backup by testing all tar.gz files.", "inputSchema": {"type": "object", "properties": {"date": {"type": "string", "description": "Backup date (YYYY-MM-DD). Default: latest"}}}},
    {"name": "disk_space", "description": "Check free space on the USB backup drive.", "inputSchema": {"type": "object", "properties": {}}},
]

def main():
    port = int(os.environ.get("MCP_PORT", "8102"))
    s = MCPServer(name="backup", port=port, tools=TOOL_SCHEMAS)
    for n, fn in [("run_backup", run_backup), ("backup_status", backup_status), ("list_backups", list_backups), ("verify_backup", verify_backup), ("disk_space", disk_space)]:
        s.register_handler(n, fn)
    log.info(f"Backup MCP starting on :{port}")
    s.start()

if __name__ == "__main__": main()
