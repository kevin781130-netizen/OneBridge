from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .endpoint_policy import validate_loopback_http_endpoint
from .network_policy import EgressPolicy


MAX_MCP_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class McpHttpResponse:
    status: int
    body: bytes
    content_type: str
    headers: Mapping[str, str]


McpTransport = Callable[
    [str, Mapping[str, str], bytes, float],
    McpHttpResponse,
]


class McpHttpError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_transport(
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout: float,
) -> McpHttpResponse:
    request = urllib.request.Request(
        url,
        data=body,
        headers=dict(headers),
        method="POST",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    try:
        raw = response.read(MAX_MCP_RESPONSE_BYTES + 1)
        if len(raw) > MAX_MCP_RESPONSE_BYTES:
            raise McpHttpError("mcp_response_too_large")
        response_headers = {
            str(key).lower(): str(value)
            for key, value in response.headers.items()
        }
        return McpHttpResponse(
            status=int(getattr(response, "status", getattr(response, "code", 0))),
            body=raw,
            content_type=str(
                response.headers.get("Content-Type") or "application/json"
            )[:200],
            headers=response_headers,
        )
    finally:
        response.close()


class McpHttpClient:
    """Minimal bounded MCP Streamable HTTP client.

    The client implements the JSON-RPC initialize -> initialized -> tools/call
    flow and accepts either JSON responses or SSE frames carrying JSON-RPC data.
    It intentionally does not support redirects, arbitrary methods, or unbounded
    server output.
    """

    def __init__(
        self,
        *,
        url: str,
        timeout_seconds: float = 120.0,
        protocol_version: str = "2025-06-18",
        egress_policy: EgressPolicy | None = None,
        transport: McpTransport | None = None,
        authorization: str | None = None,
    ) -> None:
        self.url = str(url or "").rstrip("/")
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 600.0))
        self.protocol_version = str(protocol_version)
        self.egress_policy = egress_policy
        self.transport = transport or _default_transport
        self.authorization = str(authorization).strip() if authorization else None
        self.session_id: str | None = None
        self._next_id = 1
        self._initialized = False

        parsed = urllib.parse.urlsplit(self.url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("mcp_url_invalid")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("mcp_url_invalid")
        if parsed.scheme == "http":
            decision = validate_loopback_http_endpoint(
                self.url,
                required_path="/mcp",
            )
            if not decision.allowed:
                raise ValueError(
                    f"mcp_insecure_http_blocked:{decision.reason}"
                )
        elif self.egress_policy is None:
            raise ValueError("mcp_remote_egress_policy_required")

    def _authorize(self) -> None:
        parsed = urllib.parse.urlsplit(self.url)
        if parsed.scheme != "https":
            return
        assert self.egress_policy is not None
        decision = self.egress_policy.decide(self.url, "POST")
        if not decision.allowed:
            raise ValueError(f"mcp_egress_denied:{decision.reason}")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "OneBridge-MCP-Client/1",
            "MCP-Protocol-Version": self.protocol_version,
        }
        if self.session_id:
            headers["MCP-Session-Id"] = self.session_id
        if self.authorization:
            headers["Authorization"] = self.authorization
        return headers

    @staticmethod
    def _decode_payload(
        response: McpHttpResponse,
        request_id: int | None,
    ) -> dict[str, Any]:
        if not response.body:
            return {}

        content_type = response.content_type.lower()
        text = response.body.decode("utf-8", "replace")
        if "text/event-stream" in content_type or text.lstrip().startswith(("event:", "data:")):
            candidates: list[dict[str, Any]] = []
            for line in text.splitlines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    candidates.append(value)
            if request_id is not None:
                for value in candidates:
                    if value.get("id") == request_id:
                        return value
            if candidates:
                return candidates[-1]
            raise McpHttpError("mcp_invalid_sse_response")

        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise McpHttpError("mcp_invalid_json_response") from exc
        if not isinstance(value, dict):
            raise McpHttpError("mcp_invalid_json_response")
        return value

    def _post(
        self,
        payload: dict[str, Any],
        *,
        request_id: int | None,
    ) -> dict[str, Any]:
        self._authorize()
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            response = self.transport(
                self.url,
                self._headers(),
                body,
                self.timeout_seconds,
            )
        except TimeoutError as exc:
            raise McpHttpError("mcp_timeout") from exc
        except OSError as exc:
            raise McpHttpError("mcp_transport_error") from exc

        if not 200 <= response.status < 300:
            raise McpHttpError(f"mcp_http_error:{response.status}")

        session = (
            response.headers.get("mcp-session-id")
            or response.headers.get("MCP-Session-Id")
        )
        if session:
            self.session_id = str(session)[:500]

        decoded = self._decode_payload(response, request_id)
        if decoded.get("error") is not None:
            error = decoded.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                message = str(error.get("message") or "mcp_error")[:500]
                raise McpHttpError(f"mcp_rpc_error:{code}:{message}")
            raise McpHttpError("mcp_rpc_error")
        return decoded

    def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        request_id = self._next_id
        self._next_id += 1
        decoded = self._post(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
            request_id=request_id,
        )
        if decoded.get("id") not in {None, request_id}:
            raise McpHttpError("mcp_response_id_mismatch")
        return decoded.get("result")

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._post(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
            },
            request_id=None,
        )

    def initialize(self) -> dict[str, Any]:
        if self._initialized:
            return {
                "protocolVersion": self.protocol_version,
                "sessionId": self.session_id,
            }
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {
                    "name": "onebridge",
                    "version": "0.1.0",
                },
            },
        )
        if not isinstance(result, dict):
            raise McpHttpError("mcp_initialize_invalid")
        negotiated = result.get("protocolVersion")
        if isinstance(negotiated, str) and negotiated:
            self.protocol_version = negotiated[:80]
        self._notify("notifications/initialized")
        self._initialized = True
        return result

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._initialized:
            self.initialize()
        result = self._rpc(
            "tools/call",
            {
                "name": str(name),
                "arguments": arguments,
            },
        )
        if not isinstance(result, dict):
            raise McpHttpError("mcp_tool_result_invalid")
        if result.get("isError") is True:
            raise McpHttpError("mcp_tool_reported_error")
        return result
