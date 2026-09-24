from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
MAX_SERVERS = 8
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_LOCAL_HTTP_CATEGORIES = frozenset({"design", "browser", "observability"})
_VALID_CATEGORIES = frozenset({"mcp", "design", "browser", "observability"})


@dataclass(frozen=True, slots=True)
class ExtensionSnapshot:
    available: bool
    servers: dict[str, dict[str, Any]]
    reason: str | None = None


def _approved_remote_url(url: str, category: str) -> bool:
    parsed = urlsplit(url)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return False
    try:
        port = parsed.port
    except ValueError:
        return False

    if parsed.scheme == "https":
        return port is None or 1 <= port <= 65535

    if category not in _LOCAL_HTTP_CATEGORIES or parsed.scheme != "http":
        return False
    if (parsed.hostname or "").lower() not in _LOOPBACK_HOSTS:
        return False
    if parsed.path.rstrip("/") != "/mcp":
        return False
    return port is not None and 1 <= port <= 65535


def load_approved_extensions(state_root: str | Path) -> ExtensionSnapshot:
    """Load explicitly operator-approved MCP/extension endpoints.

    The config accepts URLs and timeout only. Credentials, headers, commands,
    environment variables, OAuth material, and arbitrary unknown fields are
    rejected so secrets remain outside extension configuration.
    """
    path = Path(state_root).resolve() / "extensions.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ExtensionSnapshot(False, {}, "no_approved_extensions")
    except (OSError, ValueError):
        return ExtensionSnapshot(False, {}, "invalid_extension_config")

    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return ExtensionSnapshot(False, {}, "invalid_extension_config")

    raw_servers = payload.get("servers")
    if not isinstance(raw_servers, dict) or len(raw_servers) > MAX_SERVERS:
        return ExtensionSnapshot(False, {}, "invalid_extension_config")

    approved: dict[str, dict[str, Any]] = {}
    for name, value in raw_servers.items():
        if (
            not isinstance(name, str)
            or not _NAME.fullmatch(name)
            or not isinstance(value, dict)
        ):
            return ExtensionSnapshot(False, {}, "invalid_extension_config")

        if value.get("approved") is not True or value.get("enabled") is not True:
            continue

        if set(value) - {
            "type",
            "url",
            "enabled",
            "approved",
            "category",
            "timeout",
        }:
            return ExtensionSnapshot(
                False,
                {},
                "extension_secrets_or_unknown_fields_blocked",
            )

        category = value.get("category", "mcp")
        if category not in _VALID_CATEGORIES:
            return ExtensionSnapshot(False, {}, "invalid_extension_category")

        url = str(value.get("url") or "")
        if value.get("type") != "remote" or not _approved_remote_url(url, category):
            return ExtensionSnapshot(False, {}, "insecure_extension_blocked")

        raw_timeout = value.get("timeout", 5000)
        if (
            isinstance(raw_timeout, bool)
            or not isinstance(raw_timeout, (int, float))
            or not math.isfinite(float(raw_timeout))
        ):
            return ExtensionSnapshot(False, {}, "invalid_extension_config")

        approved[name] = {
            "type": "remote",
            "url": url,
            "enabled": True,
            "category": category,
            "timeout": max(1000, min(int(raw_timeout), 15_000)),
        }

    return ExtensionSnapshot(
        bool(approved),
        approved,
        None if approved else "no_enabled_extensions",
    )
