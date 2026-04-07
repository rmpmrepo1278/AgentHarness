"""Input sanitization — blocks shell injection in tool parameters."""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Patterns that indicate shell injection attempts.
_INJECTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r";"),                    "prohibited character: semicolon"),
    (re.compile(r"`"),                    "prohibited character: backtick"),
    (re.compile(r"\$\("),                 "prohibited pattern: $(...)"),
    (re.compile(r"\$\{"),                 "prohibited pattern: ${...}"),
    (re.compile(r"\|"),                   "prohibited character: pipe"),
    (re.compile(r"&&"),                   "prohibited pattern: &&"),
    (re.compile(r"\|\|"),                 "prohibited pattern: ||"),
    (re.compile(r"\n"),                   "prohibited character: newline"),
    (re.compile(r"\r"),                   "prohibited character: carriage return"),
    (re.compile(r">\s*/"),                "prohibited pattern: redirect to absolute path"),
    (re.compile(r">>\s*/"),               "prohibited pattern: append-redirect to absolute path"),
]

_SAFE_URL_SCHEMES = frozenset({"http", "https", "ssh", "git"})


def sanitize_shell_arg(value: str) -> str:
    """Validate *value* is free of shell-injection patterns.

    Returns the original string unchanged when safe.
    Raises ``ValueError`` with a message containing "prohibited" when an
    injection pattern is detected.
    """
    for pattern, message in _INJECTION_PATTERNS:
        if pattern.search(value):
            raise ValueError(f"Shell injection blocked: {message}")
    return value


def validate_url(url: str) -> str:
    """Validate that *url* uses a safe scheme and passes shell-arg checks.

    Accepted schemes: http, https, ssh, git.
    Raises ``ValueError`` for dangerous schemes or injection content.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in _SAFE_URL_SCHEMES:
        raise ValueError(
            f"prohibited URL scheme: {scheme!r} "
            f"(allowed: {', '.join(sorted(_SAFE_URL_SCHEMES))})"
        )
    if not parsed.netloc:
        raise ValueError("prohibited URL: missing netloc (host)")
    # Also run shell-arg sanitization on the full URL.
    sanitize_shell_arg(url)
    return url


def sanitize_tool_args(args: dict[str, object]) -> dict[str, object]:
    """Sanitize every string value in *args*.

    Non-string values are passed through unchanged.
    Raises ``ValueError`` on the first injection found.
    """
    for key, value in args.items():
        if isinstance(value, str):
            sanitize_shell_arg(value)
    return args
