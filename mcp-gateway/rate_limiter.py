"""Per-tool and global rate limiting for the MCP gateway."""
import time
import threading
from collections import defaultdict

_DEFAULT_LIMITS = {
    "list_containers": 10,
    "container_status": 10,
    "container_logs": 10,
    "deploy_stack": 2,
    "remove_container": 3,
    "restart_container": 3,
}
_GLOBAL_LIMIT = 30
_WINDOW_SECONDS = 60

_lock = threading.Lock()
_calls: dict[str, list[float]] = defaultdict(list)
_global_calls: list[float] = []


def _prune(timestamps: list[float]) -> list[float]:
    cutoff = time.time() - _WINDOW_SECONDS
    return [t for t in timestamps if t > cutoff]


def check(tool_name: str) -> tuple[bool, int]:
    """Check if a tool call is allowed.
    Returns (allowed: bool, retry_after_seconds: int)."""
    now = time.time()
    with _lock:
        _calls[tool_name] = _prune(_calls[tool_name])
        global _global_calls
        _global_calls = _prune(_global_calls)

        limit = _DEFAULT_LIMITS.get(tool_name, 10)
        if len(_calls[tool_name]) >= limit:
            oldest = _calls[tool_name][0]
            retry_after = int(_WINDOW_SECONDS - (now - oldest)) + 1
            return False, max(retry_after, 1)

        if len(_global_calls) >= _GLOBAL_LIMIT:
            oldest = _global_calls[0]
            retry_after = int(_WINDOW_SECONDS - (now - oldest)) + 1
            return False, max(retry_after, 1)

        _calls[tool_name].append(now)
        _global_calls.append(now)
        return True, 0


def set_limit(tool_name: str, max_per_minute: int):
    """Override the default limit for a tool."""
    _DEFAULT_LIMITS[tool_name] = max_per_minute
