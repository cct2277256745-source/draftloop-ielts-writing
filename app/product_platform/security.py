"""Secret redaction and bounded configuration projections."""
from __future__ import annotations

import re
from typing import Any, Mapping


_SECRET_KEY = re.compile(r"(?:api[_-]?key|token|password|secret|credential|authorization)", re.I)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/-]{8,}")
_URL_CREDENTIAL = re.compile(r"(https?://)[^/@\s:]+:[^/@\s]+@", re.I)


def redact_text(value: str) -> str:
    redacted = _BEARER.sub("Bearer [REDACTED]", value)
    return _URL_CREDENTIAL.sub(r"\1[REDACTED]@", redacted)


def redacted_config(config: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in config.items():
        if _SECRET_KEY.search(str(key)):
            result[str(key)] = "[REDACTED]"
        elif isinstance(value, Mapping):
            result[str(key)] = redacted_config(value)
        elif isinstance(value, str):
            result[str(key)] = redact_text(value)
        else:
            result[str(key)] = value
    return result
