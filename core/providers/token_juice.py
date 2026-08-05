"""
TokenJuice — LLM request preprocessing layer.

Converts HTML→markdown, strips noise, shortens URLs before sending to providers.
Goal: reduce token consumption by 40-80% on web-heavy requests.

Features:
- Content-hash LRU cache (avoids re-processing same content)
- Table/math/SVG preservation (keeps structured data as HTML fragments)
- Timeout + pass-through fallback (never blocks the request)
- Configurable via env vars:
    TJ_CACHE_SIZE (default: 256)
    TJ_TIMEOUT_SECS (default: 5)
    TJ_PRESERVE_TABLES (default: true)
    TJ_PRESERVE_MATH (default: true)
    TJ_PRESERVE_SVG (default: true)
    TJ_ENABLED (default: true)

Inspired by OpenHuman's TokenJuice concept.

Usage in proxy_server.py:
    from core.providers.token_juice import juice_request, get_juice_stats
    body = juice_request(body)  # before routing
"""

from __future__ import annotations

import asyncio
import hashlib
import html as html_module
import logging
import os
import re
import time
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from typing import Any

log = logging.getLogger(__name__)

# ── Configuration (env vars with defaults) ─────────────────────────────────

CACHE_SIZE = int(os.environ.get("TJ_CACHE_SIZE", "256"))
CACHE_TTL_SECS = int(os.environ.get("TJ_CACHE_TTL_SECS", "300"))  # 5 min default
TIMEOUT_SECS = float(os.environ.get("TJ_TIMEOUT_SECS", "5"))
PRESERVE_TABLES = os.environ.get("TJ_PRESERVE_TABLES", "true").lower() == "true"
PRESERVE_MATH = os.environ.get("TJ_PRESERVE_MATH", "true").lower() == "true"
PRESERVE_SVG = os.environ.get("TJ_PRESERVE_SVG", "true").lower() == "true"
POOL_WORKERS = int(os.environ.get("TJ_POOL_WORKERS", os.cpu_count() or 2))
ENABLED = os.environ.get("TJ_ENABLED", "true").lower() == "true"

# ── Observability Counters ─────────────────────────────────────────────────

_stats = {
    "total_requests": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "tokens_saved": 0,
    "timeouts": 0,
    "errors": 0,
}


def get_stats() -> dict:
    """Return TokenJuice observability counters."""
    return dict(_stats)


def _inc_stat(key: str, val: int = 1):
    _stats[key] = _stats.get(key, 0) + val


# ── Content-Hash LRU Cache ─────────────────────────────────────────────────

class _ContentCache:
    """LRU cache keyed on content hash with TTL support."""

    def __init__(self, maxsize: int = CACHE_SIZE, ttl_secs: int = CACHE_TTL_SECS):
        self._maxsize = maxsize
        self._ttl = ttl_secs
        self._cache: OrderedDict[str, tuple[str, float]] = OrderedDict()  # key -> (value, timestamp)

    def get(self, content: str) -> str | None:
        key = hashlib.sha256(content.encode()).hexdigest()[:16]
        if key in self._cache:
            value, ts = self._cache[key]
            # Check TTL
            if time.monotonic() - ts < self._ttl:
                self._cache.move_to_end(key)
                _inc_stat("cache_hits")
                return value
            else:
                # Expired — remove
                del self._cache[key]
        _inc_stat("cache_misses")
        return None

    def put(self, content: str, result: str):
        key = hashlib.sha256(content.encode()).hexdigest()[:16]
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._maxsize:
                self._cache.popitem(last=False)
        self._cache[key] = (result, time.monotonic())


_content_cache = _ContentCache(ttl_secs=CACHE_TTL_SECS)

# ── URL Shortening ─────────────────────────────────────────────────────────

TRUNCATABLE_PARAMS = [
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "source", "srsltid",
]

TRACKING_PATTERNS = [
    r'https?://(www\.)?google\.com/url\?.*',
    r'https?://l\.facebook\.com/l\.php\?.*',
    r'https?://t\.co/.*',
    r'https?://bit\.ly/.*',
    r'https?://tinyurl\.com/.*',
]


def shorten_url(url: str) -> str:
    """Strip tracking params from a URL, collapse known shorteners."""
    for pattern in TRACKING_PATTERNS:
        if re.match(pattern, url):
            return "[tracking-url-removed]"
    if "?" in url:
        base, _, query = url.partition("?")
        params = [p for p in query.split("&")
                 if (p.split("=")[0] if "=" in p else p) not in TRUNCATABLE_PARAMS]
        if params:
            return f"{base}?{'&'.join(params)}"
        return base
    return url


def shorten_urls_in_text(text: str) -> str:
    """Find and shorten all URLs in a text block."""
    url_pattern = re.compile(r'https?://[^\s<>"\')\]]+')
    return url_pattern.sub(lambda m: shorten_url(m.group(0)), text)


# ── Structured Content Preservation ────────────────────────────────────────

# Regex to extract and preserve structured elements before general HTML stripping
TABLE_PATTERN = re.compile(
    r'<table[^>]*>.*?</table>', re.DOTALL | re.IGNORECASE
)
MATH_PATTERN = re.compile(
    r'<math[^>]*>.*?</math>', re.DOTALL | re.IGNORECASE
)
SVG_PATTERN = re.compile(
    r'<svg[^>]*>.*?</svg>', re.DOTALL | re.IGNORECASE
)
# Also capture markdown-style math: $$...$$ and inline $...$
BLOCK_MATH_PATTERN = re.compile(r'\$\$(.*?)\$\$', re.DOTALL)
INLINE_MATH_PATTERN = re.compile(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)')


def _preserve_blocks(text: str) -> tuple[str, dict[str, str]]:
    """
    Extract structured content (tables, math, SVG) before HTML→markdown.
    Returns modified text + dict of placeholders to restore.
    """
    preserved: dict[str, str] = {}
    counter = 0

    def _save_block(match, label):
        nonlocal counter
        key = f"__PRESERVED_{label}_{counter}__"
        counter += 1
        preserved[key] = match.group(0)
        return key

    if PRESERVE_TABLES:
        text = TABLE_PATTERN.sub(lambda m: _save_block(m, "TABLE"), text)
    if PRESERVE_MATH:
        text = MATH_PATTERN.sub(lambda m: _save_block(m, "MATH"), text)
        # Also preserve block math in $$...$$
        text = BLOCK_MATH_PATTERN.sub(lambda m: _save_block(m, "BLOCKMATH"), text)
    if PRESERVE_SVG:
        text = SVG_PATTERN.sub(lambda m: _save_block(m, "SVG"), text)

    return text, preserved


def _restore_blocks(text: str, preserved: dict[str, str]) -> str:
    """Restore preserved blocks back as HTML fragments (LLMs understand them)."""
    for key, content in preserved.items():
        text = text.replace(key, content)
    return text


# ── HTML → Markdown ────────────────────────────────────────────────────────

HTML_TO_MD = {
    r'<h1[^>]*>(.*?)</h1>': r'# \1',
    r'<h2[^>]*>(.*?)</h2>': r'## \1',
    r'<h3[^>]*>(.*?)</h3>': r'### \1',
    r'<h4[^>]*>(.*?)</h4>': r'#### \1',
    r'<h5[^>]*>(.*?)</h5>': r'##### \1',
    r'<h6[^>]*>(.*?)</h6>': r'###### \1',
    r'<strong[^>]*>(.*?)</strong>': r'**\1**',
    r'<b[^>]*>(.*?)</b>': r'**\1**',
    r'<em[^>]*>(.*?)</em>': r'*\1*',
    r'<i[^>]*>(.*?)</i>': r'*\1*',
    r'<code[^>]*>(.*?)</code>': r'`\1`',
    r'<pre[^>]*>(.*?)</pre>': r'\n```\n\1\n```\n',
    r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>': r'[\2](\1)',
    r'<li[^>]*>(.*?)</li>': r'- \1',
    r'<br\s*/?>': '\n',
    r'<p[^>]*>(.*?)</p>': r'\n\1\n',
    r'<hr\s*/?>': '\n---\n',
    r'<blockquote[^>]*>(.*?)</blockquote>': lambda m: '> ' + m.group(1).replace('\n', '\n> '),
}

REMOVE_TAGS = [
    'script', 'style', 'nav', 'footer', 'header', 'aside',
    'iframe', 'noscript', 'form', 'input', 'button',
    'select', 'textarea', 'label',
]

REMOVE_PATTERNS = [
    re.compile(rf'<{tag}[^>]*>.*?</{tag}>', re.DOTALL | re.IGNORECASE)
    for tag in REMOVE_TAGS
]


def html_to_markdown(text: str) -> str:
    """
    Convert HTML to clean markdown. Preserves tables/math/SVG as HTML fragments.
    """
    result = text

    # Step 1: Extract and preserve structured content
    result, preserved = _preserve_blocks(result)

    # Step 2: Remove unwanted tags with content
    for pattern in REMOVE_PATTERNS:
        result = pattern.sub('', result)

    # Step 3: Replace structural tags
    for pattern, replacement in HTML_TO_MD.items():
        if callable(replacement):
            result = re.sub(pattern, replacement, result, flags=re.DOTALL | re.IGNORECASE)
        else:
            result = re.sub(pattern, replacement, result, flags=re.DOTALL | re.IGNORECASE)

    # Step 4: Decode HTML entities
    result = html_module.unescape(result)

    # Step 5: Collapse whitespace
    result = re.sub(r'\n{3,}', '\n\n', result)
    result = re.sub(r' {2,}', ' ', result)

    # Step 6: Remove remaining HTML tags (catch-all)
    result = re.sub(r'<[^>]+>', '', result)

    # Step 7: Restore preserved blocks AFTER tag removal
    # This ensures tables/math/SVG survive as HTML fragments
    result = _restore_blocks(result, preserved)

    return result.strip()


# ── Noise Stripping ─────────────────────────────────────────────────────────

NOISE_PATTERNS = [
    r'(Share|Tweet|Pin|Like|Follow|Subscribe)\s*(on\s+)?(Twitter|Facebook|LinkedIn|Pinterest|Instagram)',
    r'(Accept|Reject|Manage)\s*(all)?\s*cookies',
    r'(Privacy\s*Policy|Terms\s*of\s*Service|Cookie\s*Policy)',
    r'(Home|About|Contact|Search|Login|Sign\s*(up|in))[\s|]*',
    r'Read\s+more\s*[.…→]*',
    r'Continue\s+reading\s*[.…→]*',
    r'(Sponsored|Advertisement|Promoted)',
]


def strip_noise(text: str) -> str:
    """Strip common web page noise that wastes tokens."""
    result = text
    for pattern in NOISE_PATTERNS:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


# ── Timeout Guard (asyncio-safe) ────────────────────────────────────────────

# Process pool for CPU-bound preprocessing (runs outside the event loop)
_process_pool: ProcessPoolExecutor | None = None


def _get_process_pool() -> ProcessPoolExecutor:
    """Get or create the process pool for CPU-bound preprocessing."""
    global _process_pool
    if _process_pool is None:
        _process_pool = ProcessPoolExecutor(max_workers=POOL_WORKERS)
    return _process_pool


def _process_content_sync(content: str) -> str:
    """CPU-bound preprocessing (runs in separate process, no event loop)."""
    result = html_to_markdown(content)
    result = shorten_urls_in_text(result)
    result = strip_noise(result)
    return result


async def _process_with_timeout(content: str, timeout: float) -> str:
    """
    Run TokenJuice preprocessing with a hard timeout, safely in asyncio.
    Uses ProcessPoolExecutor to run CPU-bound work outside the event loop,
    then asyncio.wait_for() for the timeout.
    """
    try:
        loop = asyncio.get_running_loop()
        pool = _get_process_pool()
        result = await asyncio.wait_for(
            loop.run_in_executor(pool, _process_content_sync, content),
            timeout=timeout,
        )
        return result
    except asyncio.TimeoutError:
        raise _TimeoutError(f"TokenJuice processing timed out after {timeout}s")
    except Exception:
        raise


class _TimeoutError(Exception):
    pass


# ── Observability Counter ──────────────────────────────────────────────────


# ── Main Entry Point ───────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Rough token estimate: chars / 4 for English text."""
    return len(text) // 4


async def juice_body(body: dict[str, Any]) -> dict[str, Any]:
    """
    Process a chat completions request body to reduce token usage.

    Applies to message content:
    1. Content-hash LRU cache lookup (skip if already processed)
    2. Preserve tables/math/SVG as HTML fragments
    3. HTML → Markdown conversion (in ProcessPoolExecutor, asyncio-safe)
    4. URL shortening (strip tracking params)
    5. Noise stripping (nav, ads, cookie banners)
    6. Whitespace normalization
    7. Hard timeout with pass-through fallback

    Returns the modified body (mutates in place).
    """
    if not ENABLED:
        return body

    _inc_stat("total_requests")
    start = time.monotonic()

    messages = body.get("messages", [])
    total_saved = 0

    for msg in messages:
        content = msg.get("content", "")
        if not content or not isinstance(content, str):
            continue

        original_tokens = estimate_tokens(content)

        # Check cache first
        cached = _content_cache.get(content)
        if cached is not None:
            msg["content"] = cached
            saved = max(0, original_tokens - estimate_tokens(cached))
            total_saved += saved
            continue

        # Process with timeout guard (asyncio-safe via ProcessPoolExecutor)
        try:
            processed = await _process_with_timeout(content, TIMEOUT_SECS)
        except _TimeoutError:
            log.warning("TokenJuice timeout on %d-char content, passing through original", len(content))
            _inc_stat("timeouts")
            processed = content  # pass-through fallback
        except Exception as exc:
            log.warning("TokenJuice error: %s, passing through original", exc)
            _inc_stat("errors")
            processed = content  # pass-through fallback

        new_tokens = estimate_tokens(processed)
        saved = max(0, original_tokens - new_tokens)
        total_saved += saved

        if saved > 0:
            msg["content"] = processed
            # Cache the result
            _content_cache.put(content, processed)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    _inc_stat("tokens_saved", total_saved)
    body["_token_juice"] = {
        "tokens_saved": total_saved,
        "applied": total_saved > 0,
        "elapsed_ms": elapsed_ms,
        "cache_hit_rate": _stats["cache_hits"] / max(1, _stats["cache_hits"] + _stats["cache_misses"]),
    }

    return body


def get_juice_stats(body: dict[str, Any]) -> dict:
    """Get token savings stats from a juiced request."""
    return body.get("_token_juice", {"tokens_saved": 0, "applied": False, "elapsed_ms": 0})
