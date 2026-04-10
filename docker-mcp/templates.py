"""Resolve and render compose templates with hot-reload support."""
from __future__ import annotations
import os
import re
import logging
import threading

log = logging.getLogger("templates")

_REPO_DIR = os.environ.get("TEMPLATES_REPO_DIR", "/templates/repo")
_LOCAL_DIR = os.environ.get("TEMPLATES_LOCAL_DIR", "/templates/local")

_cache: dict[str, str] = {}
_lock = threading.Lock()


def _scan_templates() -> dict[str, str]:
    """Scan both directories. Local overrides take precedence."""
    templates = {}
    if os.path.isdir(_REPO_DIR):
        for f in os.listdir(_REPO_DIR):
            if f.endswith((".yml", ".yaml")):
                name = f.rsplit(".", 1)[0]
                templates[name] = os.path.join(_REPO_DIR, f)
    if os.path.isdir(_LOCAL_DIR):
        for f in os.listdir(_LOCAL_DIR):
            if f.endswith((".yml", ".yaml")):
                name = f.rsplit(".", 1)[0]
                templates[name] = os.path.join(_LOCAL_DIR, f)
    return templates


def refresh():
    global _cache
    with _lock:
        _cache = _scan_templates()
    log.info(f"Template cache: {len(_cache)} templates ({', '.join(_cache.keys()) or 'none'})")


def list_templates() -> list[str]:
    with _lock:
        if not _cache:
            refresh()
        return list(_cache.keys())


def get_template(name: str) -> str | None:
    with _lock:
        if not _cache:
            refresh()
        path = _cache.get(name)
    if path and os.path.exists(path):
        with open(path) as f:
            return f.read()
    return None


def render_template(name: str, variables: dict) -> str | None:
    """Render a template with ${VAR:-default} substitution."""
    raw = get_template(name)
    if raw is None:
        return None

    def replace_var(match):
        var_name = match.group(1)
        default = match.group(3)
        return str(variables.get(var_name, default or ""))

    return re.sub(r'\$\{(\w+)(:-([^}]*))?\}', replace_var, raw)


def start_watcher():
    """Start background thread to watch for template changes."""
    def poll_loop():
        import time
        while True:
            time.sleep(30)
            refresh()

    t = threading.Thread(target=poll_loop, daemon=True, name="template-watcher")
    t.start()


refresh()
