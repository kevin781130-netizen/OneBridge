from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import select

from .db import Database, DeploymentActionRecord, DeploymentRecord, utcnow
from .deployment_actuator import DeploymentActuationRequest, DeploymentActuationResult
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
    reported_active_slot: str | None = None
    reported_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DeploymentProbeError(RuntimeError):
    pass


class DeploymentActuatorProtocol(Protocol):
    def apply(
        self,
        request_value: DeploymentActuationRequest,
    ) -> DeploymentActuationResult: ...


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
    reported_active_slot: str | None = None
    reported_version: str | None = None
    if body and "json" in content_type.lower():
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            if isinstance(payload.get("ok"), bool):
                reported_ok = payload["ok"]
            if isinstance(payload.get("active_slot"), str):
                reported_active_slot = payload["active_slot"][:40]
            if isinstance(payload.get("version"), str):
                reported_version = payload["version"][:120]

    healthy = 200 <= status < 300 and reported_ok is not False
    return ProbeResult(
        healthy=healthy,
        status_code=status,
        latency_ms=int((time.monotonic() - started) * 1000),
        content_type=content_type,
        body_sha256=hashlib.sha256(body).hexdigest(),
        reported_ok=reported_ok,
        reported_active_slot=reported_active_slot,
        reported_version=reported_version,
    )


class DeploymentSwitchService:
    """Persistent evidence-gated blue/green deployment registry."""

    def __init__(
        self,
        db: Database,
        *,
        actuator: DeploymentActuatorProtocol | None = None,
        health_max_age_seconds: float = 300.0,
    ) -> None:
        self.db = db
        self.actuator = actuator
        self.health_max_age_seconds = max(
            10.0,
            float(health_max_age_seconds),
        )

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

    def _health_current(
        self,
        status: DeploymentStatus,
    ) -> bool:
        if status.health_status != "healthy":
            return False
        checked_at = status.health_evidence.get("checked_at")
        if not isinstance(checked_at, str) or not checked_at:
            return False
        try:
            checked = datetime.fromisoformat(checked_at)
        except ValueError:
            return False
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        age = (
            datetime.now(timezone.utc)
            - checked.astimezone(timezone.utc)
        ).total_seconds()
        return 0 <= age <= self.health_max_age_seconds

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

    def _record_action_pending(
        self,
        *,
        service: str,
        action: str,
        from_slot: str | None,
        target: DeploymentStatus,
    ) -> str:
        action_id = f"depact_{uuid4().hex}"
        with self.db.Session() as session:
            session.add(
                DeploymentActionRecord(
                    id=action_id,
                    service=service,
                    action=action,
                    from_slot=from_slot,
                    to_slot=target.slot,
                    version=target.version,
                    status="pending",
                    evidence_json="{}",
                )
            )
            session.commit()
        return action_id

    def _record_action_result(
        self,
        action_id: str,
        *,
        status: str,
        evidence: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self.db.Session() as session:
            row = session.get(DeploymentActionRecord, action_id)
            if row is None:
                return
            row.status = status
            if evidence is not None:
                row.evidence_json = json.dumps(
                    evidence,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            if error is not None:
                row.error = str(error)[:2000] or None
            session.commit()

    def _actuate(
        self,
        *,
        service: str,
        action: str,
        from_slot: str | None,
        target: DeploymentStatus,
    ) -> str | None:
        if self.actuator is None:
            return None
        action_id = self._record_action_pending(
            service=service,
            action=action,
            from_slot=from_slot,
            target=target,
        )
        try:
            result = self.actuator.apply(
                DeploymentActuationRequest(
                    request_id=action_id,
                    service=service,
                    action=action,
                    from_slot=from_slot,
                    to_slot=target.slot,
                    version=target.version,
                    target_endpoint=target.endpoint,
                )
            )
        except Exception as exc:
            self._record_action_result(
                action_id,
                status="failed",
                error=f"{type(exc).__name__}:{str(exc)[:1000]}",
            )
            raise
        self._record_action_result(
            action_id,
            status="actuated",
            evidence=result.to_dict(),
        )
        return action_id

    def promote(
        self,
        service: str,
        slot: str,
    ) -> DeploymentStatus:
        slot_name = self._slot(slot)
        target_status = self.get(service, slot_name)
        if not self._health_current(target_status):
            raise ValueError(
                "deployment promotion requires current healthy probe evidence"
            )
        if target_status.state == "active":
            return target_status

        active_status = next(
            (
                item
                for item in self.list(service)
                if item.state == "active"
            ),
            None,
        )
        action_id = self._actuate(
            service=service,
            action="promote",
            from_slot=(
                active_status.slot
                if active_status is not None
                else None
            ),
            target=target_status,
        )

        try:
            with self.db.Session() as session:
                target = session.scalar(
                    select(DeploymentRecord).where(
                        DeploymentRecord.service == service,
                        DeploymentRecord.slot == slot_name,
                    )
                )
                if target is None:
                    raise KeyError((service, slot_name))
                current_status = self._status(target)
                if not self._health_current(current_status):
                    raise ValueError(
                        "deployment health changed before promotion commit"
                    )

                active = list(
                    session.scalars(
                        select(DeploymentRecord).where(
                            DeploymentRecord.service == service,
                            DeploymentRecord.state == "active",
                        )
                    )
                )
                for row in active:
                    if row.slot != slot_name:
                        row.state = "standby"

                target.state = "active"
                target.promoted_at = utcnow()
                session.commit()
        except Exception:
            if action_id is not None:
                self._record_action_result(
                    action_id,
                    status="reconcile_required",
                    error="registry_commit_failed_after_actuation",
                )
            raise

        if action_id is not None:
            current = self.get(service, slot_name)
            with self.db.Session() as session:
                row = session.get(DeploymentActionRecord, action_id)
                if row is not None:
                    row.status = "committed"
                    session.commit()
            return current
        return self.get(service, slot_name)

    def rollback(self, service: str) -> DeploymentStatus:
        values = self.list(service)
        active_status = next(
            (item for item in values if item.state == "active"),
            None,
        )
        standby_status = next(
            (
                item
                for item in sorted(
                    values,
                    key=lambda value: value.promoted_at or "",
                    reverse=True,
                )
                if (
                    item.state == "standby"
                    and self._health_current(item)
                )
            ),
            None,
        )
        if standby_status is None:
            raise ValueError(
                "no healthy standby deployment available"
            )

        action_id = self._actuate(
            service=service,
            action="rollback",
            from_slot=(
                active_status.slot
                if active_status is not None
                else None
            ),
            target=standby_status,
        )

        try:
            with self.db.Session() as session:
                active = session.scalar(
                    select(DeploymentRecord).where(
                        DeploymentRecord.service == service,
                        DeploymentRecord.state == "active",
                    )
                )
                standby = session.scalar(
                    select(DeploymentRecord).where(
                        DeploymentRecord.service == service,
                        DeploymentRecord.slot == standby_status.slot,
                        DeploymentRecord.state == "standby",
                        DeploymentRecord.health_status == "healthy",
                    )
                )
                if standby is None or not self._health_current(
                    self._status(standby)
                ):
                    raise ValueError(
                        "healthy standby changed before rollback commit"
                    )
                if active is not None:
                    active.state = "standby"
                standby.state = "active"
                standby.promoted_at = utcnow()
                session.commit()
        except Exception:
            if action_id is not None:
                self._record_action_result(
                    action_id,
                    status="reconcile_required",
                    error="registry_commit_failed_after_actuation",
                )
            raise

        if action_id is not None:
            with self.db.Session() as session:
                row = session.get(DeploymentActionRecord, action_id)
                if row is not None:
                    row.status = "committed"
                    session.commit()
        return self.get(service, standby_status.slot)

    def reconcile(
        self,
        service: str,
        *,
        observed_slot: str,
        observed_version: str | None = None,
    ) -> DeploymentStatus:
        slot_name = self._slot(observed_slot)
        with self.db.Session() as session:
            action = session.scalar(
                select(DeploymentActionRecord)
                .where(
                    DeploymentActionRecord.service == service,
                    DeploymentActionRecord.status == "reconcile_required",
                )
                .order_by(DeploymentActionRecord.updated_at.desc())
                .limit(1)
            )
            if action is None:
                raise ValueError("no deployment reconciliation is pending")
            if slot_name not in {
                action.from_slot,
                action.to_slot,
            }:
                raise ValueError(
                    "observed slot does not match pending deployment action"
                )

            target = session.scalar(
                select(DeploymentRecord).where(
                    DeploymentRecord.service == service,
                    DeploymentRecord.slot == slot_name,
                )
            )
            if target is None:
                raise KeyError((service, slot_name))
            if (
                observed_version is not None
                and str(observed_version)
                and target.version != str(observed_version)
            ):
                raise ValueError(
                    "observed deployment version does not match registry"
                )

            rows = list(
                session.scalars(
                    select(DeploymentRecord).where(
                        DeploymentRecord.service == service
                    )
                )
            )
            for row in rows:
                if row.slot == slot_name:
                    row.state = "active"
                    row.promoted_at = utcnow()
                elif row.state == "active":
                    row.state = "standby"

            try:
                evidence = json.loads(
                    action.evidence_json or "{}"
                )
            except json.JSONDecodeError:
                evidence = {}
            if not isinstance(evidence, dict):
                evidence = {}
            evidence["reconciliation"] = {
                "observed_slot": slot_name,
                "observed_version": (
                    str(observed_version)
                    if observed_version is not None
                    else target.version
                ),
                "reconciled_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }
            action.evidence_json = json.dumps(
                evidence,
                ensure_ascii=False,
                sort_keys=True,
            )
            action.status = "reconciled"
            action.error = None
            session.commit()
            return self._status(target)

    def actions(
        self,
        service: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with self.db.Session() as session:
            rows = list(
                session.scalars(
                    select(DeploymentActionRecord)
                    .where(DeploymentActionRecord.service == service)
                    .order_by(DeploymentActionRecord.created_at.desc())
                    .limit(max(1, min(int(limit), 200)))
                )
            )
            result: list[dict[str, Any]] = []
            for row in rows:
                try:
                    evidence = json.loads(row.evidence_json or "{}")
                except json.JSONDecodeError:
                    evidence = {}
                result.append({
                    "id": row.id,
                    "service": row.service,
                    "action": row.action,
                    "from_slot": row.from_slot,
                    "to_slot": row.to_slot,
                    "version": row.version,
                    "status": row.status,
                    "evidence": (
                        evidence
                        if isinstance(evidence, dict)
                        else {}
                    ),
                    "error": row.error,
                    "created_at": row.created_at.isoformat(),
                    "updated_at": row.updated_at.isoformat(),
                })
            return result

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
