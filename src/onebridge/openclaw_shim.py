from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .contracts import ApprovalRequest, TaskContract
from .endpoint_policy import validate_loopback_http_endpoint
from .network_policy import EgressPolicy


MAX_RESPONSE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ShimHttpResult:
    status: int
    body: bytes
    content_type: str


ShimTransport = Callable[
    [str, str, Mapping[str, str], bytes | None, float],
    ShimHttpResult,
]


class OneBridgeShimError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_transport(
    url: str,
    method: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> ShimHttpResult:
    request = urllib.request.Request(
        url,
        data=body,
        headers=dict(headers),
        method=method,
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
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise OneBridgeShimError("onebridge_response_too_large")
        return ShimHttpResult(
            status=int(getattr(response, "status", getattr(response, "code", 0))),
            body=raw,
            content_type=str(
                response.headers.get("Content-Type") or "application/json"
            )[:200],
        )
    finally:
        response.close()


class OpenClawOneBridgeShim:
    """Thin, stateless OneBridge client intended for OpenClaw tool wrappers.

    It owns no workflow state, never reads the OneBridge database and never
    executes workers directly. OpenClaw integrations can expose these methods as
    submit/status/artifacts/approve/retry/cancel tools.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        egress_policy: EgressPolicy | None = None,
        transport: ShimTransport | None = None,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = str(api_key).strip() if api_key else None
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))
        self.egress_policy = egress_policy
        self.transport = transport or _default_transport

        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("onebridge_base_url_invalid")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("onebridge_base_url_invalid")
        if parsed.scheme == "http":
            decision = validate_loopback_http_endpoint(self.base_url)
            if not decision.allowed:
                raise ValueError(
                    f"onebridge_insecure_remote_http_blocked:{decision.reason}"
                )

    def _url(self, path: str) -> str:
        url = self.base_url + path
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme == "https":
            if self.egress_policy is None:
                raise ValueError("onebridge_remote_egress_policy_required")
            decision = self.egress_policy.decide(url, "GET")
            if not decision.allowed:
                # POST may be the operation; caller validates again with method.
                get_reason = decision.reason
                if get_reason not in {"egress_method_invalid", "egress_policy_denied"}:
                    raise ValueError(f"onebridge_egress_denied:{get_reason}")
        return url

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = self._url(path)
        if urllib.parse.urlsplit(url).scheme == "https":
            assert self.egress_policy is not None
            decision = self.egress_policy.decide(url, method)
            if not decision.allowed:
                raise ValueError(f"onebridge_egress_denied:{decision.reason}")

        headers = {
            "Accept": "application/json",
            "User-Agent": "OpenClaw-OneBridge-Shim/1",
        }
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            response = self.transport(
                url,
                method,
                headers,
                body,
                self.timeout_seconds,
            )
        except TimeoutError as exc:
            raise OneBridgeShimError("onebridge_timeout") from exc
        except OSError as exc:
            raise OneBridgeShimError("onebridge_transport_error") from exc

        if not 200 <= response.status < 300:
            raise OneBridgeShimError(
                f"onebridge_http_error:{response.status}"
            )
        if not response.body:
            return {}
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OneBridgeShimError("onebridge_invalid_json_response") from exc

    def submit(self, contract: TaskContract) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/tasks",
            contract.model_dump(mode="json"),
        )

    def status(self, task_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v1/tasks/{urllib.parse.quote(task_id, safe='')}",
        )

    def artifacts(self, task_id: str) -> list[dict[str, Any]]:
        value = self._request(
            "GET",
            f"/api/v1/tasks/{urllib.parse.quote(task_id, safe='')}/artifacts",
        )
        if not isinstance(value, list):
            raise OneBridgeShimError("onebridge_invalid_artifacts_response")
        return value

    def approve(
        self,
        task_id: str,
        request: ApprovalRequest,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/tasks/{urllib.parse.quote(task_id, safe='')}/approve",
            request.model_dump(mode="json"),
        )

    def retry(self, task_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/tasks/{urllib.parse.quote(task_id, safe='')}/retry",
            {},
        )

    def cancel(self, task_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/tasks/{urllib.parse.quote(task_id, safe='')}/cancel",
            {},
        )
