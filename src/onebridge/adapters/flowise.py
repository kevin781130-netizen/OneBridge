from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping
from uuid import uuid4

from onebridge.endpoint_policy import validate_loopback_http_endpoint
from onebridge.network_policy import EgressPolicy

from .base import AdapterHealth, AdapterOutput, AdapterRequest, AdapterResult
from .validation import validate_adapter_outputs


MAX_RESPONSE_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: int
    body: bytes
    content_type: str


Transport = Callable[[str, Mapping[str, str], bytes, float], HttpResult]


class FlowiseAdapterError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_transport(
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout: float,
) -> HttpResult:
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
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise FlowiseAdapterError("flowise_response_too_large")
        return HttpResult(
            status=int(getattr(response, "status", getattr(response, "code", 0))),
            body=raw,
            content_type=str(
                response.headers.get("Content-Type") or "application/json"
            )[:200],
        )
    finally:
        response.close()


class FlowisePredictionAdapter:
    name = "flowise"

    def __init__(
        self,
        *,
        base_url: str,
        chatflow_id: str,
        api_key: str | None = None,
        timeout_seconds: float = 120.0,
        allowed_override_keys: tuple[str, ...] = (),
        egress_policy: EgressPolicy | None = None,
        transport: Transport | None = None,
        version: str = "prediction-v1",
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.chatflow_id = str(chatflow_id or "").strip()
        self.api_key = str(api_key).strip() if api_key else None
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 600.0))
        self.allowed_override_keys = tuple(dict.fromkeys(allowed_override_keys))
        self.egress_policy = egress_policy
        self.transport = transport or _default_transport
        self.version = version

        if not self.base_url:
            raise ValueError("flowise_base_url_required")
        if not self.chatflow_id or len(self.chatflow_id) > 300:
            raise ValueError("flowise_chatflow_id_invalid")

        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("flowise_base_url_invalid")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("flowise_base_url_invalid")

    def health(self) -> AdapterHealth:
        try:
            self._prediction_url()
        except ValueError as exc:
            return AdapterHealth("unhealthy", str(exc))
        return AdapterHealth("healthy", "configured")

    def capabilities(self) -> list[str]:
        return ["content"]

    def _prediction_url(self) -> str:
        parsed = urllib.parse.urlsplit(self.base_url)
        path = parsed.path.rstrip("/")
        if path.endswith("/api/v1"):
            endpoint_path = f"{path}/prediction/{self.chatflow_id}"
        else:
            endpoint_path = f"{path}/api/v1/prediction/{self.chatflow_id}"
        url = urllib.parse.urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                endpoint_path,
                "",
                "",
            )
        )

        if parsed.scheme == "http":
            decision = validate_loopback_http_endpoint(self.base_url)
            if not decision.allowed:
                raise ValueError(
                    f"flowise_insecure_remote_http_blocked:{decision.reason}"
                )
        else:
            if self.egress_policy is None:
                raise ValueError("flowise_remote_egress_policy_required")
            decision = self.egress_policy.decide(url, "POST")
            if not decision.allowed:
                raise ValueError(f"flowise_egress_denied:{decision.reason}")

        return url

    def _override_config(self, request: AdapterRequest) -> dict:
        raw = request.inputs.get("flowise_override", {})
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise FlowiseAdapterError("flowise_override_must_be_object")

        unknown = sorted(set(raw) - set(self.allowed_override_keys))
        if unknown:
            raise FlowiseAdapterError(
                f"flowise_override_key_blocked:{','.join(unknown)}"
            )
        return {key: raw[key] for key in self.allowed_override_keys if key in raw}

    def execute(self, request: AdapterRequest) -> AdapterResult:
        url = self._prediction_url()
        payload = {
            "question": request.goal,
            "streaming": False,
        }
        override = self._override_config(request)
        if override:
            payload["overrideConfig"] = override

        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "OneBridge-Flowise-Adapter/1",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            response = self.transport(
                url,
                headers,
                body,
                self.timeout_seconds,
            )
        except TimeoutError as exc:
            raise FlowiseAdapterError("flowise_timeout") from exc
        except OSError as exc:
            raise FlowiseAdapterError("flowise_transport_error") from exc

        if not 200 <= response.status < 300:
            raise FlowiseAdapterError(
                f"flowise_http_error:{response.status}"
            )

        try:
            parsed = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FlowiseAdapterError("flowise_invalid_json_response") from exc

        content = json.dumps(
            parsed,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        output = AdapterOutput(
            kind="content",
            media_type="application/json",
            content=content,
            filename="content_spec.json",
        )
        report = validate_adapter_outputs(
            [output],
            expected_kind="content",
            max_total_bytes=MAX_RESPONSE_BYTES,
        )
        if not report.valid:
            raise FlowiseAdapterError(
                "flowise_output_validation_failed:"
                + ";".join(report.errors)
            )

        external_task_id = ""
        if isinstance(parsed, dict):
            external_task_id = str(
                parsed.get("chatMessageId")
                or parsed.get("chatId")
                or ""
            )[:300]
        if not external_task_id:
            external_task_id = f"flowise_{uuid4().hex}"

        return AdapterResult(
            external_task_id=external_task_id,
            outputs=[output],
            metadata={
                "http_status": response.status,
                "content_type": response.content_type,
            },
        )

    def cancel(self, external_task_id: str) -> None:
        return None
