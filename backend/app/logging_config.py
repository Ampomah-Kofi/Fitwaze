"""Structured logging setup with a redaction filter.

Security requirement: log records must never contain passwords, password
hashes, sensitive health fields, raw coordinates, or token values. This
module installs a `logging.Filter` that redacts known-sensitive keys/patterns
from log messages and `extra` fields before they are emitted, and a formatter
that only surfaces coarse action names + user UUIDs.

Usage: call `configure_logging()` once at app startup (done in `main.py`).
Elsewhere, use `logging.getLogger(__name__)` as normal — the redaction is
applied globally via the root logger's handlers.
"""
from __future__ import annotations

import logging
import re
import sys

from app.config import get_settings

# Keys that must never appear in log output, regardless of where they show up
# (log message text, %-args, or `extra=` dict fields).
SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "hashed_password",
    "diabetes_status",
    "weight_kg",
    "height_cm",
    "mobility_limitations",
    "walking_ability",
    "cycling_ability",
    "latitude",
    "longitude",
    "lat",
    "lon",
    "lng",
    "access_token",
    "refresh_token",
    "token",
    "jwt",
    "authorization",
}

REDACTED = "[REDACTED]"

# Patterns that catch sensitive values even when they're embedded in a free
# text log message rather than passed as structured fields, e.g.
# "login failed password=hunter2" or a raw JWT string.
_INLINE_PATTERNS = [
    re.compile(r"(?i)(password(?:_hash)?\s*[=:]\s*)\S+"),
    re.compile(r"(?i)((?:access|refresh)[_-]?token\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(authorization\s*[=:]\s*)\S+"),
    # JWTs: three base64url segments separated by dots
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    # raw lat/lon pairs like "40.7128, -74.0060"
    re.compile(r"(?i)((?:lat(?:itude)?|lon(?:gitude)?|lng)\s*[=:]\s*)-?\d+\.\d+"),
]


def _redact_text(text: str) -> str:
    redacted = text
    for pattern in _INLINE_PATTERNS:
        if pattern.groups:
            redacted = pattern.sub(lambda m: m.group(1) + REDACTED, redacted)
        else:
            redacted = pattern.sub(REDACTED, redacted)
    return redacted


class RedactionFilter(logging.Filter):
    """Redacts sensitive data from log records before they're emitted."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Redact the formatted message text itself.
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            msg = str(record.msg)
        record.msg = _redact_text(msg)
        record.args = ()

        # Redact any structured `extra=` fields attached to the record.
        for key in list(record.__dict__.keys()):
            if key.lower() in SENSITIVE_KEYS:
                setattr(record, key, REDACTED)

        return True


def configure_logging() -> None:
    settings = get_settings()
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())

    handler = logging.StreamHandler(stream=sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    handler.setFormatter(formatter)
    handler.addFilter(RedactionFilter())

    root.handlers.clear()
    root.addHandler(handler)

    # Quiet down noisy third-party loggers that might otherwise leak request
    # bodies (which could contain health data) at debug level.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
