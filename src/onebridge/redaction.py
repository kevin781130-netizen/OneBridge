from __future__ import annotations

import re
from typing import Any


SECRET_KEYS = (
    "api_key",
    "token",
    "secret",
    "password",
    "authorization",
    "auth",
    "bearer",
)

SECRET_ENV_NAMES = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "GITHUB_TOKEN",
    "FLOWISE_API_KEY",
    "HERMES_API_KEY",
    "ONEBRIDGE_S3_SECRET_KEY",
    "Authorization",
    "HTTP_AUTHORIZATION",
)

_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api_key|token|secret|password|authorization|auth|bearer)([=:]\s*)([^\s,&]+)"
)
_AUTH_HEADER_RE = re.compile(
    r"(?i)\b(authorization)([=:]\s*)bearer\s+[A-Za-z0-9._~+/=-]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_STANDALONE_CREDENTIAL_RE = re.compile(
    r"\b(sk|nvapi)-[A-Za-z0-9._~+/=-]{6,}"
)


def is_secret_key(key: str) -> bool:
    lowered = str(key or "").lower()
    return any(
        lowered == marker or lowered.endswith(f"_{marker}")
        for marker in SECRET_KEYS
    )


def redact_text(text: str) -> str:
    value = str(text or "")
    value = _AUTH_HEADER_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}Bearer <redacted>",
        value,
    )
    value = _ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}<redacted>",
        value,
    )
    value = _BEARER_RE.sub("Bearer <redacted>", value)

    for name in SECRET_ENV_NAMES:
        value = re.sub(
            rf"(?i)\b{re.escape(name)}([=:]\s*)([^\s,&]+)",
            rf"{name}\1<redacted>",
            value,
        )

    return _STANDALONE_CREDENTIAL_RE.sub(
        lambda match: f"({match.group(1)}-key-redacted)",
        value,
    )


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if is_secret_key(str(key)) else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value
