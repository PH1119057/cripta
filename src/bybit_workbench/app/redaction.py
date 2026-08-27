from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .credentials import BybitCredentials

REDACTED = "***REDACTED***"
_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|api[_-]?secret|secret|signature|authorization|x-bapi-sign)",
    re.IGNORECASE,
)


def redact_text(value: object, credentials: BybitCredentials | None = None) -> str:
    """Return display/log-safe text without retaining key material."""

    text = str(value).strip() or value.__class__.__name__
    if credentials is not None:
        for secret in (credentials.api_key, credentials.api_secret):
            if secret:
                text = text.replace(secret, REDACTED)
    text = re.sub(
        r"(?i)((?:api[_-]?key|api[_-]?secret|secret|signature|authorization)\s*[=:]\s*)[^\s,;]+",
        rf"\1{REDACTED}",
        text,
    )
    return text


def redact_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if _SENSITIVE_KEY.search(str(key)):
            result[str(key)] = REDACTED
        elif isinstance(item, Mapping):
            result[str(key)] = redact_mapping(item)
        elif isinstance(item, list):
            result[str(key)] = [redact_mapping(x) if isinstance(x, Mapping) else x for x in item]
        else:
            result[str(key)] = item
    return result
