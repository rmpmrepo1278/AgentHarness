"""Pre-deploy resource checks: memory and disk."""
import os
import shutil
import logging

log = logging.getLogger("resource_guard")

_MIN_MEMORY_MB = int(os.environ.get("MIN_MEMORY_MB", "400"))
_WARN_MEMORY_MB = int(os.environ.get("WARN_MEMORY_MB", "800"))
_MIN_DISK_GB = float(os.environ.get("MIN_DISK_GB", "2"))
_WARN_DISK_GB = float(os.environ.get("WARN_DISK_GB", "5"))


def get_free_memory_mb() -> int:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 9999


def get_free_disk_gb(path: str = "/") -> float:
    try:
        usage = shutil.disk_usage(path)
        return usage.free / (1024 ** 3)
    except OSError:
        return 999.0


def check_resources() -> dict:
    """Check if resources are sufficient for a deploy."""
    mem_mb = get_free_memory_mb()
    disk_gb = get_free_disk_gb()
    warnings = []
    errors = []

    if mem_mb < _MIN_MEMORY_MB:
        errors.append(f"Only {mem_mb}MB memory free (minimum: {_MIN_MEMORY_MB}MB). "
                      "Remove a container or add swap first.")
    elif mem_mb < _WARN_MEMORY_MB:
        warnings.append(f"Low memory: {mem_mb}MB free (recommend: {_WARN_MEMORY_MB}MB+)")

    if disk_gb < _MIN_DISK_GB:
        errors.append(f"Only {disk_gb:.1f}GB disk free (minimum: {_MIN_DISK_GB}GB). "
                      "Clean up or expand storage.")
    elif disk_gb < _WARN_DISK_GB:
        warnings.append(f"Low disk: {disk_gb:.1f}GB free (recommend: {_WARN_DISK_GB}GB+)")

    return {
        "ok": len(errors) == 0,
        "warnings": warnings,
        "errors": errors,
        "memory_mb": mem_mb,
        "disk_gb": round(disk_gb, 1),
    }
