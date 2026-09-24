from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .endpoint_policy import validate_loopback_http_endpoint
from .network_policy import EgressPolicy, EgressRule


MAX_ACTUATOR_RESPONSE_BYTES = 128 * 1024


@dataclass(frozen=True, slots=True)
class DeploymentActuationRequest:
    request_id: str
    service: str
    action: str
    from_slot: str | None
    to_slot: str
    version: str
    target_endpoint: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DeploymentActuationResult:
    applied: bool
    status_code: int
    latency_ms: int
    response_sha256: str
    reported_active_slot: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DeploymentActuatorError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_actuator_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        raise ValueError("deployment_actuator_url_required")
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError as exc:
        raise ValueError("deployment_actuator_url_invalid") from exc

    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "deployment_actuator_credentials_query_fragment_blocked"
        )

    if parsed.scheme == "http":
        decision = validate_loopback_http_endpoint(raw)
        if not decision.allowed:
            raise ValueError(
                f"deployment_actuator_insecure_http_blocked:{decision.reason}"
            )
        return decision.normalized_url

    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("deployment_actuator_https_required")

    policy = EgressPolicy([
        EgressRule(
            rule_id="deployment-actuator",
            host=parsed.hostname,
            path_prefix=parsed.path or "/",
            methods=("POST",),
        )
    ])
    decision = policy.decide(raw, "POST")
    if not decision.allowed:
        raise ValueError(
            f"deployment_actuator_blocked:{decision.reason}"
        )
    return raw


class HttpDeploymentActuator:
    """Signed, idempotent HTTP hook for applying deployment traffic switches."""

    def __init__(
        self,
        *,
        url: str,
        shared_secret: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.url = _validate_actuator_url(url)
        secret = str(shared_secret or "")
        if len(secret) < 16:
            raise ValueError("deployment_actuator_secret_too_short")
        self.shared_secret = secret.encode("utf-8")
        self.timeout_seconds = max(
            1.0,
            min(float(timeout_seconds), 30.0),
        )

    def apply(
        self,
        request_value: DeploymentActuationRequest,
    ) -> DeploymentActuationResult:
        body = json.dumps(
            request_value.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        timestamp = str(int(time.time()))
        signature = hmac.new(
            self.shared_secret,
            timestamp.encode("ascii") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "OneBridge-Deployment-Actuator/1",
                "X-OneBridge-Timestamp": timestamp,
                "X-OneBridge-Signature": f"sha256={signature}",
                "X-OneBridge-Idempotency-Key": request_value.request_id,
            },
            method="POST",
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirect(),
        )
        started = time.monotonic()
        try:
            response = opener.open(
                request,
                timeout=self.timeout_seconds,
            )
        except urllib.error.HTTPError as exc:
            response = exc
        except OSError as exc:
            raise DeploymentActuatorError(
                "deployment_actuator_transport_error"
            ) from exc

        try:
            raw = response.read(MAX_ACTUATOR_RESPONSE_BYTES + 1)
            if len(raw) > MAX_ACTUATOR_RESPONSE_BYTES:
                raise DeploymentActuatorError(
                    "deployment_actuator_response_too_large"
                )
            status = int(
                getattr(response, "status", getattr(response, "code", 0))
            )
        finally:
            response.close()

        reported_active_slot: str | None = None
        reported_ok: bool | None = None
        if raw:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict):
                if isinstance(payload.get("ok"), bool):
                    reported_ok = payload["ok"]
                active_slot = payload.get("active_slot")
                if isinstance(active_slot, str):
                    reported_active_slot = active_slot[:40]

        applied = (
            200 <= status < 300
            and reported_ok is not False
            and (
                reported_active_slot is None
                or reported_active_slot == request_value.to_slot
            )
        )
        result = DeploymentActuationResult(
            applied=applied,
            status_code=status,
            latency_ms=int((time.monotonic() - started) * 1000),
            response_sha256=hashlib.sha256(raw).hexdigest(),
            reported_active_slot=reported_active_slot,
        )
        if not result.applied:
            raise DeploymentActuatorError(
                f"deployment_actuator_rejected:{status}"
            )
        return result
