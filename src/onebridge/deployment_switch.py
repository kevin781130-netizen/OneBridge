from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select

from .db import Database, DeploymentRecord, utcnow
from .endpoint_policy import validate_loopback_http_endpoint
from .network_policy import EgressPolicy, EgressRule


MAX_HEALTH_RESPONSE_BYTES = 128 * 1024


@dataclass(frozen=True, slots=True)
class DeploymentStatus:
    service: str
    slot: str
    endpoint: str
    version: str
    state: str
    health_status: str
    health_evidence: dict[str, Any]
    promoted_at: str | None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    healthy: bool
    status_code: int
    latency_ms: int
    content_type: str
    body_sha256: str
    reported_ok: bool | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DeploymentProbeError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_probe_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        raise ValueError("deployment_endpoint_required")
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError as exc:
        raise ValueError("deployment_endpoint_invalid") from exc

    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("deployment_endpoint_credentials_query_fragment_blocked")

    if parsed.scheme == "http":
        decision = validate_loopback_http_endpoint(raw)
        if not decision.allowed:
            raise ValueError(
                f"deployment_insecure_http_blocked:{decision.reason}"
            )
        return decision.normalized_url

    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("deployment_endpoint_https_required")

    path = parsed.path or "/"
    policy = EgressPolicy([
        EgressRule(
            rule_id="deployment-health",
            host=parsed.hostname,
            path_prefix=path,
            methods=("GET",),
        )
    ])
    decision = policy.decide(raw, "GET")
    if not decision.allowed:
        raise ValueError(
            f"deployment_endpoint_blocked:{decision.reason}"
        )
    return raw


def probe_deployment(
    endpoint: str,
    *,
    timeout_seconds: float = 5.0,
) -> ProbeResult:
    url = _validate_probe_url(endpoint)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "OneBridge-Deployment-Probe/1",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )
    started = time.monotonic()
    try:
        response = opener.open(
            request,
            timeout=max(1.0, min(float(timeout_seconds), 20.0)),
        )
    except urllib.error.HTTPError as exc:
        response = exc
    except OSError as exc:
        raise DeploymentProbeError("deployment_probe_transport_error") from exc

    try:
        body = response.read(MAX_HEALTH_RESPONSE_BYTES + 1)
        if len(body) > MAX_HEALTH_RESPONSE_BYTES:
            raise DeploymentProbeError(
                "deployment_probe_response_too_large"
            )
        status = int(
            getattr(response, "status", getattr(response, "code", 0))
        )
        content_type = str(
            response.headers.get("Content-Type")
            or "application/octet-stream"
        )[:200]
    finally:
        response.close()

    reported_ok: bool | None = None
    if body and "json" in content_type.lower():
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("ok"), bool):
            reported_ok = payload["ok"]

    healthy = 200 <= status < 300 and reported_ok is not False
    return ProbeResult(
        healthy=healthy,
        status_code=status,
        latency_ms=int((time.monotonic() - started) * 1000),
        content_type=content_type,
        body_sha256=hashlib.sha256(body).hexdigest(),
        reported_ok=reported_ok,
    )


class DeploymentSwitchService:
    """Persistent evidence-gated blue/green deployment registry."""

    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _slot(value: str) -> str:
        slot = str(value or "").strip().lower()
        if slot not in {"blue", "green"}:
            raise ValueError("deployment_slot_must_be_blue_or_green")
        return slot

    @staticmethod
    def _status(row: DeploymentRecord) -> DeploymentStatus:
        try:
            evidence = json.loads(row.health_evidence_json or "{}")
        except json.JSONDecodeError:
            evidence = {}
        return DeploymentStatus(
            service=row.service,
            slot=row.slot,
            endpoint=row.endpoint,
            version=row.version,
            state=row.state,
            health_status=row.health_status,
            health_evidence=(
                evidence if isinstance(evidence, dict) else {}
            ),
            promoted_at=(
                row.promoted_at.isoformat()
                if row.promoted_at is not None
                else None
            ),
        )

    def register(
        self,
        service: str,
        slot: str,
        *,
        endpoint: str,
        version: str,
    ) -> DeploymentStatus:
        service_name = str(service or "").strip()
        if not service_name:
            raise ValueError("deployment_service_required")
        slot_name = self._slot(slot)
        normalized = _validate_probe_url(endpoint)
        version_value = str(version or "").strip()
        if not version_value:
            raise ValueError("deployment_version_required")

        with self.db.Session() as session:
            row = session.scalar(
                select(DeploymentRecord).where(
                    DeploymentRecord.service == service_name,
                    DeploymentRecord.slot == slot_name,
                )
            )
            if row is None:
                row = DeploymentRecord(
                    service=service_name,
                    slot=slot_name,
                    endpoint=normalized,
                    version=version_value,
                    state="candidate",
                    health_status="unknown",
                    health_evidence_json="{}",
                )
                session.add(row)
            else:
                changed = (
                    row.endpoint != normalized
                    or row.version != version_value
                )
                if changed and row.state == "active":
                    raise ValueError(
                        "cannot replace active deployment slot"
                    )
                row.endpoint = normalized
                row.version = version_value
                if changed:
                    row.state = "candidate"
                    row.health_status = "unknown"
                    row.health_evidence_json = "{}"
            session.commit()
            return self._status(row)

    def record_health(
        self,
        service: str,
        slot: str,
        result: ProbeResult,
    ) -> DeploymentStatus:
        slot_name = self._slot(slot)
        with self.db.Session() as session:
            row = session.scalar(
                select(DeploymentRecord).where(
                    DeploymentRecord.service == service,
                    DeploymentRecord.slot == slot_name,
                )
            )
            if row is None:
                raise KeyError((service, slot_name))
            evidence = result.to_dict()
            evidence["checked_at"] = datetime.now(
                timezone.utc
            ).isoformat()
            row.health_status = (
                "healthy" if result.healthy else "unhealthy"
            )
            row.health_evidence_json = json.dumps(
                evidence,
                ensure_ascii=False,
                sort_keys=True,
            )
            if not result.healthy and row.state == "candidate":
                row.state = "blocked"
            session.commit()
            return self._status(row)

    def probe(
        self,
        service: str,
        slot: str,
        *,
        timeout_seconds: float = 5.0,
    ) -> DeploymentStatus:
        current = self.get(service, slot)
        result = probe_deployment(
            current.endpoint,
            timeout_seconds=timeout_seconds,
        )
        return self.record_health(service, slot, result)

    def promote(
        self,
        service: str,
        slot: str,
    ) -> DeploymentStatus:
        slot_name = self._slot(slot)
        with self.db.Session() as session:
            target = session.scalar(
                select(DeploymentRecord).where(
                    DeploymentRecord.service == service,
                    DeploymentRecord.slot == slot_name,
                )
            )
            if target is None:
                raise KeyError((service, slot_name))
            if target.health_status != "healthy":
                raise ValueError(
                    "deployment promotion requires healthy probe evidence"
                )
            if target.state == "active":
                return self._status(target)

            active = list(
                session.scalars(
                    select(DeploymentRecord).where(
                        DeploymentRecord.service == service,
                        DeploymentRecord.state == "active",
                    )
                )
            )
            for row in active:
                row.state = "standby"

            target.state = "active"
            target.promoted_at = utcnow()
            session.commit()
            return self._status(target)

    def rollback(self, service: str) -> DeploymentStatus:
        with self.db.Session() as session:
            active = session.scalar(
                select(DeploymentRecord).where(
                    DeploymentRecord.service == service,
                    DeploymentRecord.state == "active",
                )
            )
            standby = session.scalar(
                select(DeploymentRecord)
                .where(
                    DeploymentRecord.service == service,
                    DeploymentRecord.state == "standby",
                    DeploymentRecord.health_status == "healthy",
                )
                .order_by(DeploymentRecord.updated_at.desc())
                .limit(1)
            )
            if standby is None:
                raise ValueError(
                    "no healthy standby deployment available"
                )
            if active is not None:
                active.state = "standby"
            standby.state = "active"
            standby.promoted_at = utcnow()
            session.commit()
            return self._status(standby)

    def get(
        self,
        service: str,
        slot: str,
    ) -> DeploymentStatus:
        slot_name = self._slot(slot)
        with self.db.Session() as session:
            row = session.scalar(
                select(DeploymentRecord).where(
                    DeploymentRecord.service == service,
                    DeploymentRecord.slot == slot_name,
                )
            )
            if row is None:
                raise KeyError((service, slot_name))
            return self._status(row)

    def list(self, service: str) -> list[DeploymentStatus]:
        with self.db.Session() as session:
            rows = list(
                session.scalars(
                    select(DeploymentRecord)
                    .where(DeploymentRecord.service == service)
                    .order_by(DeploymentRecord.slot)
                )
            )
            return [self._status(row) for row in rows]

    @staticmethod
    def as_dict(status: DeploymentStatus) -> dict[str, Any]:
        return asdict(status)
