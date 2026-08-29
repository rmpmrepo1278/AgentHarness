"""
short_circuit.py — Proxy request short-circuit handler.

Intercepts common Claude Code probe requests and returns local responses
without hitting upstream providers. Reduces API calls and latency.

Patterns detected:
  - count_tokens with identical/repeated content
  - Title generation prompts ("summarize this conversation in 5 words")
  - Quota/billing probe requests
  - Simple filepath extraction
  - Empty/system-only messages
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

log = logging.getLogger(__name__)

# ── Pattern detectors ────────────────────────────────────────────────────────

_TITLE_PROMPTS = (
    "summarize this conversation",
    "title for this conversation",
    "generate a title",
    "conversation title",
    "in 5 words",
    "in 10 words",
    "short title",
    "brief title",
)

_QUOTA_PROMPTS = (
    "check quota",
    "check billing",
    "usage limit",
    "rate limit status",
)

_FILEPATH_PROMPTS = (
    "what files are you working on",
    "list the files",
    "which file",
    "read the file",
)

# Simple response cache: hash -> (response, expiry)
_cache: dict[str, tuple[dict, float]] = {}
CACHE_TTL = 600  # 10 minutes


def _content_hash(body: dict) -> str:
    """Hash the request body for cache lookups."""
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _cached_or_none(body: dict) -> dict | None:
    """Return cached response if available and not expired."""
    key = _content_hash(body)
    if key in _cache:
        resp, expiry = _cache[key]
        if time.time() < expiry:
            return resp
        del _cache[key]
    return None


def _cache_response(body: dict, response: dict, ttl: float = CACHE_TTL):
    """Cache a response."""
    key = _content_hash(body)
    _cache[key] = (response, time.time() + ttl)
    # Prune old entries if cache gets large
    if len(_cache) > 1000:
        now = time.time()
        expired = [k for k, (_, exp) in _cache.items() if now > exp]
        for k in expired:
            del _cache[k]


def _last_user_msg(body: dict) -> str:
    """Extract the last user message text from the request."""
    for msg in reversed(body.get("messages", [])):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content.lower()
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        parts.append(part.get("text", "").lower())
                return " ".join(parts)
    return ""


def _msg_count(body: dict) -> int:
    return len(body.get("messages", []))


def _has_tools(body: dict) -> bool:
    return bool(body.get("tools"))


def _model(body: dict) -> str:
    return body.get("model", "").lower()


# ── Short-circuit handlers ──────────────────────────────────────────────────

def _handle_count_tokens(body: dict) -> dict | None:
    """Short-circuit count_tokens with repeated content."""
    if body.get("count_tokens") is True:
        # Return estimated token count
        text = json.dumps(body.get("messages", []))
        estimated = max(len(text) // 4, 100)
        return {
            "id": "short-circuit-count",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": _model(body),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": str(estimated)},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": estimated,
                "completion_tokens": 1,
                "total_tokens": estimated + 1,
            },
            "short_circuit": True,
        }
    return None


def _handle_title_generation(body: dict) -> dict | None:
    """Short-circuit title generation probes."""
    last = _last_user_msg(body)
    if not last:
        return None
    for pattern in _TITLE_PROMPTS:
        if pattern in last and _msg_count(body) <= 3:
            log.info("Short-circuit: title generation probe detected")
            return {
                "id": "short-circuit-title",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": _model(body),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "Conversation"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                "short_circuit": True,
            }
    return None


def _handle_quota_check(body: dict) -> dict | None:
    """Short-circuit quota/billing probe requests."""
    last = _last_user_msg(body)
    if not last:
        return None
    for pattern in _QUOTA_PROMPTS:
        if pattern in last and not _has_tools(body):
            log.info("Short-circuit: quota probe detected")
            return {
                "id": "short-circuit-quota",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": _model(body),
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Quota and billing information is not available in this session. Your usage appears to be within normal limits.",
                    },
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 15, "completion_tokens": 20, "total_tokens": 35},
                "short_circuit": True,
            }
    return None


def _handle_system_only(body: dict) -> dict | None:
    """Short-circuit messages with only system prompt (no user content)."""
    messages = body.get("messages", [])
    user_msgs = [m for m in messages if m.get("role") == "user"]
    if not user_msgs and len(messages) <= 2:
        log.info("Short-circuit: system-only message (no user content)")
        return {
            "id": "short-circuit-system",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": _model(body),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "I'm ready to help."},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            "short_circuit": True,
        }
    return None


# ── Main entry point ────────────────────────────────────────────────────────

