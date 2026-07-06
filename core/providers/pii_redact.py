"""PII redaction middleware for LLM Proxy.

Scans text for common PII patterns and replaces them with placeholders
before the content reaches cloud LLM providers.

Enable/disable via env var: PII_REDACT_ENABLED=true (default: true)
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PII pattern definitions -- ordered most-specific first
# ---------------------------------------------------------------------------

@dataclass
class PIIPattern:
    label: str
    placeholder: str
    regex: re.Pattern

_PATTERNS: list[PIIPattern] = []

def _p(label: str, placeholder: str, pattern: str, flags: int = re.ASCII | re.IGNORECASE) -> None:
    _PATTERNS.append(PIIPattern(label, placeholder, re.compile(pattern, flags)))

# Email addresses
_p("email", "[EMAIL]", r"[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9][A-Za-z0-9.-]{0,252}\.[A-Za-z]{2,}")

# US phone numbers: +1 (555) 123-4567, 555-123-4567, (555) 123-4567
_p("phone", "[PHONE]", r"(?:\+?\d{1,2}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")

# International phone: +44 20 7946 0958
_p("phone_intl", "[PHONE]", r"\+\d{1,3}[-.\s]?\d{2,4}[-.\s]?\d{2,4}[-.\s]?\d{3,4}\b")

# SSN (US Social Security Number)
_p("ssn", "[SSN]", r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")

# Credit card numbers (16-digit with optional separators)
_p("credit_card", "[CREDIT_CARD]", r"\b(?:\d{4}[-.\s]?){3}\d{4}\b")

# Private IPv4 addresses
_p("ip_private", "[IP_ADDRESS]", r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|127\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")

# API keys / tokens
_p("api_key", "[API_KEY]", r"\b(?:sk-[A-Za-z0-9_-]{20,}|pk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36,}|gho_[A-Za-z0-9]{36,}|ghu_[A-Za-z0-9]{36,}|xox[bpras]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})\b")

# Date of birth
_p("dob", "[DOB]", r"\b(?:dob|date\s*of\s*birth|birth\s*date|born)\s*(?:on\s+)?[:=]?\s*\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b", re.IGNORECASE)

# US/UK street addresses
_p("address", "[ADDRESS]", r"\b\d{1,5}\s+[A-Za-z]\w+(?:\s+\w+){0,3}\s+(?:St(?:reet)?|Ave(?:nue)?|Blvd|Boulevard|Dr(?:ive)?|Ln|Lane|Rd|Road|Ct|Court|Way|Pl(?:ace)?|Cir(?:cle)?|Hwy|Highway|Pkwy|Parkway)\b")

# Inline credentials: password=xxx, secret: xxx
_p("credential", "[CREDENTIAL]", r"(?i)(?:password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]\s*\S{8,}")

# Passport / driver license (letter + 8 digits)
_p("passport", "[PASSPORT]", r"\b[A-Z]\d{8}\b")


@dataclass
class PIIResult:
    """Result of PII redaction."""
    text: str
    redacted: dict[str, int] = field(default_factory=dict)
    total: int = 0


def redact(text: str) -> PIIResult:
    """Scan *text* for PII patterns and replace with placeholders."""
    result = PIIResult(text=text, redacted={})
    for pattern in _PATTERNS:
        n = len(pattern.regex.findall(result.text))
        if n:
            result.text = pattern.regex.sub(pattern.placeholder, result.text)
            result.redacted[pattern.label] = result.redacted.get(pattern.label, 0) + n
            result.total += n
    if result.total:
        log.info("PII redacted: %s", result.redacted)
    return result


def is_enabled() -> bool:
    """Check if PII redaction is enabled via environment."""
    val = os.environ.get("PII_REDACT_ENABLED", "true").strip().lower()
    return val in ("true", "1", "yes", "y")
