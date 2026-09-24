from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True, slots=True)
class EndpointPolicyResult:
    allowed: bool
    normalized_url: str = ""
    reason: str = ""


def validate_loopback_http_endpoint(
    value: str,
    *,
    required_path: str | None = None,
) -> EndpointPolicyResult:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return EndpointPolicyResult(False, reason="endpoint_missing")

    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return EndpointPolicyResult(False, reason="endpoint_invalid")

    if parsed.scheme != "http":
        return EndpointPolicyResult(False, reason="https_or_remote_transport_not_allowed")
    if (parsed.hostname or "").lower() not in _LOOPBACK_HOSTS:
        return EndpointPolicyResult(False, reason="non_loopback_endpoint_blocked")
    if parsed.username is not None or parsed.password is not None:
        return EndpointPolicyResult(False, reason="embedded_credentials_blocked")
    if parsed.query or parsed.fragment:
        return EndpointPolicyResult(False, reason="endpoint_query_or_fragment_blocked")
    if not port:
        return EndpointPolicyResult(False, reason="explicit_port_required")

    normalized = raw
    if required_path is not None:
        wanted = "/" + required_path.strip("/")
        current = parsed.path.rstrip("/")
        if not current:
            normalized += wanted
        elif current != wanted:
            return EndpointPolicyResult(False, reason="endpoint_path_not_supported")

    return EndpointPolicyResult(True, normalized_url=normalized, reason="ready")
