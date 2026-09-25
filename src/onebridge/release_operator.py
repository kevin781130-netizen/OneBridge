from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .db import (
    Database,
    ReleaseApprovalRecord,
    ReleaseLeaseRecord,
    ReleaseRequestRecord,
    utcnow,
)
from .production_release import ProductionReleaseController
from .redaction import redact_text


@dataclass(frozen=True, slots=True)
class ReleasePlan:
    service: str
    target_slot: str
    target_version: str
    target_endpoint: str
    previous_slot: str | None
    previous_version: str | None
    smoke_url: str | None
    external_actuation: bool
    adapters: tuple[tuple[str, str], ...]
    adapter_states: tuple[tuple[str, str], ...]
    fingerprint: str

    def to_dict(self) -> dict:
        value = asdict(self)
        value["adapters"] = [
            {"adapter_id": adapter_id, "version": version}
            for adapter_id, version in self.adapters
        ]
        value["adapter_states"] = [
            {"adapter_id": adapter_id, "state": state}
            for adapter_id, state in self.adapter_states
        ]
        return value


class ProductionReleaseOperator:
    """Approval-gated, single-flight wrapper around production releases."""

    def __init__(
        self,
        db: Database,
        controller: ProductionReleaseController,
        *,
        lease_max_age_seconds: float = 1800.0,
        approval_max_age_seconds: float = 1800.0,
        two_person_required: bool = False,
    ) -> None:
        self.db = db
        self.controller = controller
        self.lease_max_age_seconds = max(
            60.0,
            float(lease_max_age_seconds),
        )
        self.approval_max_age_seconds = max(
            60.0,
            float(approval_max_age_seconds),
        )
        self.two_person_required = bool(two_person_required)

    def plan(
        self,
        target_slot: str,
        adapter_ids: list[str] | tuple[str, ...],
    ) -> ReleasePlan:
        target = self.controller.deployments.get(
            "openclaw",
            target_slot,
        )
        requested = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in adapter_ids
                if str(value).strip()
            )
        )
        if not requested:
            raise ValueError("release plan adapter set cannot be empty")

        adapters: list[tuple[str, str]] = []
        adapter_states: list[tuple[str, str]] = []
        for adapter_id in requested:
            adapter = self.controller.compatibility.registry.get(
                adapter_id
            )
            version = str(adapter.version)
            if version.startswith("mock-"):
                raise ValueError(
                    f"{adapter.name}:mock adapter cannot enter a production plan"
                )
            state = "unregistered"
            compatibility_get = getattr(
                self.controller.compatibility,
                "get",
                None,
            )
            if callable(compatibility_get):
                try:
                    status = compatibility_get(
                        adapter.name,
                        version,
                    )
                except KeyError:
                    status = None
                if status is not None:
                    state = str(status.state)
            if state == "blocked":
                raise ValueError(
                    f"{adapter.name}@{version}:blocked adapter cannot enter a production plan"
                )
            adapters.append((adapter.name, version))
            adapter_states.append((adapter.name, state))

        previous = next(
            (
                item
                for item in self.controller.deployments.list("openclaw")
                if item.state == "active"
            ),
            None,
        )
        body = {
            "service": "openclaw",
            "target_slot": target.slot,
            "target_version": target.version,
            "target_endpoint": target.endpoint,
            "previous_slot": (
                previous.slot
                if previous is not None
                else None
            ),
            "previous_version": (
                previous.version
                if previous is not None
                else None
            ),
            "smoke_url": self.controller.smoke_url,
            "external_actuation": (
                self.controller.deployments.actuator is not None
            ),
            "adapters": [
                {
                    "adapter_id": name,
                    "version": version,
                    "state": dict(adapter_states).get(
                        name,
                        "unregistered",
                    ),
                }
                for name, version in adapters
            ],
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return ReleasePlan(
            service="openclaw",
            target_slot=target.slot,
            target_version=target.version,
            target_endpoint=target.endpoint,
            previous_slot=(
                previous.slot
                if previous is not None
                else None
            ),
            previous_version=(
                previous.version
                if previous is not None
                else None
            ),
            smoke_url=self.controller.smoke_url,
            external_actuation=(
                self.controller.deployments.actuator is not None
            ),
            adapters=tuple(adapters),
            adapter_states=tuple(adapter_states),
            fingerprint=fingerprint,
        )

    def approve(
        self,
        plan: ReleasePlan,
        *,
        actor: str,
        decision: str = "approve",
        reason: str = "",
        request_id: str | None = None,
        requested_by: str | None = None,
    ) -> dict:
        if decision not in {"approve", "reject"}:
            raise ValueError("release decision must be approve or reject")
        actor_value = str(actor or "").strip()
        if not actor_value:
            raise ValueError("release approval actor is required")
        requester_value = str(requested_by or "").strip() or None
        if self.two_person_required and not request_id:
            raise ValueError(
                "two-person release approval requires a persisted release request"
            )
        if (
            requester_value is not None
            and actor_value == requester_value
        ):
            raise ValueError(
                "release requester and approver must be different identities"
            )

        approval_id = f"relapp_{uuid4().hex}"
        with self.db.Session() as session:
            session.add(
                ReleaseApprovalRecord(
                    id=approval_id,
                    service=plan.service,
                    target_slot=plan.target_slot,
                    fingerprint=plan.fingerprint,
                    adapters_json=json.dumps(
                        {
                            "schema": 3,
                            "plan": plan.to_dict(),
                            "request_id": request_id,
                            "requested_by": requester_value,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    decision=decision,
                    actor=redact_text(actor_value)[:200],
                    reason=redact_text(str(reason or ""))[:4000],
                )
            )
            session.commit()
        return self.approval(approval_id)

    @staticmethod
    def _stored_payload(raw: str) -> dict:
        try:
            value = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return {}
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, list):
            return {
                "schema": 1,
                "plan": {"adapters": value},
            }
        return {}

    @classmethod
    def _stored_plan(cls, raw: str) -> dict:
        payload = cls._stored_payload(raw)
        plan = payload.get("plan")
        return dict(plan) if isinstance(plan, dict) else {}

    def approval(self, approval_id: str) -> dict:
        with self.db.Session() as session:
            row = session.get(ReleaseApprovalRecord, approval_id)
            if row is None:
                raise KeyError(approval_id)
            payload = self._stored_payload(row.adapters_json)
            return {
                "approval_id": row.id,
                "service": row.service,
                "target_slot": row.target_slot,
                "fingerprint": row.fingerprint,
                "decision": row.decision,
                "actor": row.actor,
                "reason": row.reason,
                "request_id": payload.get("request_id"),
                "requested_by": payload.get("requested_by"),
                "plan": self._stored_plan(row.adapters_json),
                "consumed_at": (
                    row.consumed_at.isoformat()
                    if row.consumed_at is not None
                    else None
                ),
                "created_at": row.created_at.isoformat(),
            }

    def request(
        self,
        plan: ReleasePlan,
        *,
        actor: str,
        reason: str = "",
    ) -> dict:
        actor_value = str(actor or "").strip()
        if not actor_value:
            raise ValueError("release requester is required")
        request_id = f"relreq_{uuid4().hex}"
        with self.db.Session() as session:
            session.add(
                ReleaseRequestRecord(
                    id=request_id,
                    service=plan.service,
                    target_slot=plan.target_slot,
                    fingerprint=plan.fingerprint,
                    plan_json=json.dumps(
                        plan.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    requested_by=redact_text(actor_value)[:200],
                    reason=redact_text(str(reason or ""))[:4000],
                    status="pending",
                )
            )
            session.commit()
        return self.request_status(request_id)

    def request_status(self, request_id: str) -> dict:
        with self.db.Session() as session:
            row = session.get(ReleaseRequestRecord, request_id)
            if row is None:
                raise KeyError(request_id)
            try:
                plan = json.loads(row.plan_json or "{}")
            except json.JSONDecodeError:
                plan = {}
            return {
                "request_id": row.id,
                "service": row.service,
                "target_slot": row.target_slot,
                "fingerprint": row.fingerprint,
                "requested_by": row.requested_by,
                "reason": row.reason,
                "status": row.status,
                "plan": plan if isinstance(plan, dict) else {},
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }

    def requests(self, *, limit: int = 50) -> list[dict]:
        with self.db.Session() as session:
            ids = list(
                session.scalars(
                    select(ReleaseRequestRecord.id)
                    .order_by(
                        ReleaseRequestRecord.created_at.desc()
                    )
                    .limit(max(1, min(int(limit), 200)))
                )
            )
        return [self.request_status(value) for value in ids]

    def approve_request(
        self,
        request_id: str,
        *,
        actor: str,
        decision: str = "approve",
        reason: str = "",
    ) -> dict:
        if decision not in {"approve", "reject"}:
            raise ValueError(
                "release decision must be approve or reject"
            )
        request_value = self.request_status(request_id)
        if request_value["status"] != "pending":
            raise ValueError("release request is not pending")
        actor_value = str(actor or "").strip()
        if not actor_value:
            raise ValueError("release approval actor is required")
        if actor_value == request_value["requested_by"]:
            raise ValueError(
                "release requester and approver must be different identities"
            )

        plan_snapshot = request_value.get("plan")
        if not isinstance(plan_snapshot, dict):
            raise ValueError("release request plan is invalid")
        adapters = plan_snapshot.get("adapters", [])
        if not isinstance(adapters, list):
            raise ValueError("release request adapter set is invalid")
        adapter_ids = [
            str(item.get("adapter_id") or "")
            for item in adapters
            if isinstance(item, dict)
        ]
        current = self.plan(
            request_value["target_slot"],
            adapter_ids,
        )
        if current.fingerprint != request_value["fingerprint"]:
            with self.db.Session() as session:
                result = session.execute(
                    update(ReleaseRequestRecord)
                    .where(
                        ReleaseRequestRecord.id == request_id,
                        ReleaseRequestRecord.status == "pending",
                    )
                    .values(status="stale")
                )
                if result.rowcount == 1:
                    session.commit()
                else:
                    session.rollback()
            raise ValueError("release request is stale")

        approval_id = f"relapp_{uuid4().hex}"
        final_status = (
            "approved"
            if decision == "approve"
            else "rejected"
        )
        with self.db.Session() as session:
            transition = session.execute(
                update(ReleaseRequestRecord)
                .where(
                    ReleaseRequestRecord.id == request_id,
                    ReleaseRequestRecord.status == "pending",
                )
                .values(status=final_status)
            )
            if transition.rowcount != 1:
                session.rollback()
                raise ValueError(
                    "release request was already decided"
                )
            session.add(
                ReleaseApprovalRecord(
                    id=approval_id,
                    service=current.service,
                    target_slot=current.target_slot,
                    fingerprint=current.fingerprint,
                    adapters_json=json.dumps(
                        {
                            "schema": 3,
                            "plan": current.to_dict(),
                            "request_id": request_id,
                            "requested_by": request_value[
                                "requested_by"
                            ],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    decision=decision,
                    actor=redact_text(actor_value)[:200],
                    reason=redact_text(
                        str(reason or "")
                    )[:4000],
                )
            )
            session.commit()
        return self.approval(approval_id)

    def revoke(
        self,
        approval_id: str,
        *,
        actor: str,
        reason: str = "",
    ) -> dict:
        actor_value = str(actor or "").strip()
        if not actor_value:
            raise ValueError("release revocation actor is required")
        with self.db.Session() as session:
            row = session.get(ReleaseApprovalRecord, approval_id)
            if row is None:
                raise KeyError(approval_id)
            if row.consumed_at is not None:
                raise ValueError(
                    "consumed release approval cannot be revoked"
                )
            if row.decision != "approve":
                raise ValueError(
                    "only an approved release can be revoked"
                )
            payload = self._stored_payload(row.adapters_json)
            row.decision = "revoked"
            row.reason = redact_text(
                str(reason or "revoked by operator")
            )[:4000]
            request_id = str(
                payload.get("request_id") or ""
            ).strip()
            if request_id:
                request_row = session.get(
                    ReleaseRequestRecord,
                    request_id,
                )
                if request_row is not None:
                    request_row.status = "revoked"
            session.commit()
        result = self.approval(approval_id)
        result["revoked_by"] = redact_text(actor_value)[:200]
        return result

    def approvals(self, *, limit: int = 50) -> list[dict]:
        with self.db.Session() as session:
            rows = list(
                session.scalars(
                    select(ReleaseApprovalRecord)
                    .order_by(
                        ReleaseApprovalRecord.created_at.desc()
                    )
                    .limit(max(1, min(int(limit), 200)))
                )
            )
            return [
                {
                    "approval_id": row.id,
                    "service": row.service,
                    "target_slot": row.target_slot,
                    "fingerprint": row.fingerprint,
                    "decision": row.decision,
                    "actor": row.actor,
                    "reason": row.reason,
                    "request_id": self._stored_payload(
                        row.adapters_json
                    ).get("request_id"),
                    "requested_by": self._stored_payload(
                        row.adapters_json
                    ).get("requested_by"),
                    "plan": self._stored_plan(row.adapters_json),
                    "consumed_at": (
                        row.consumed_at.isoformat()
                        if row.consumed_at is not None
                        else None
                    ),
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    def _set_request_status(
        self,
        request_id: str | None,
        status: str,
    ) -> None:
        value = str(request_id or "").strip()
        if not value:
            return
        with self.db.Session() as session:
            row = session.get(ReleaseRequestRecord, value)
            if row is not None:
                row.status = str(status)[:24]
                session.commit()

    def _acquire(
        self,
        release_id: str,
        owner: str,
    ) -> None:
        now = utcnow()
        with self.db.Session() as session:
            current = session.get(ReleaseLeaseRecord, "openclaw")
            if current is not None:
                acquired = current.acquired_at
                if acquired.tzinfo is None:
                    acquired = acquired.replace(tzinfo=timezone.utc)
                age = (
                    now - acquired.astimezone(timezone.utc)
                ).total_seconds()
                if age <= self.lease_max_age_seconds:
                    raise RuntimeError(
                        f"production release busy:{current.release_id}"
                    )
                session.delete(current)
                session.flush()

            session.add(
                ReleaseLeaseRecord(
                    service="openclaw",
                    release_id=release_id,
                    owner=str(owner or "operator")[:200],
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise RuntimeError(
                    "production release busy"
                ) from exc

    def _release(self, release_id: str) -> None:
        with self.db.Session() as session:
            row = session.get(ReleaseLeaseRecord, "openclaw")
            if row is not None and row.release_id == release_id:
                session.delete(row)
                session.commit()

    def execute(
        self,
        approval_id: str,
        *,
        owner: str = "operator",
    ) -> dict:
        approval = self.approval(approval_id)
        if approval["decision"] != "approve":
            raise ValueError("release approval was not approved")
        if approval["consumed_at"] is not None:
            raise ValueError("release approval has already been consumed")
        if self.two_person_required:
            requester = str(
                approval.get("requested_by") or ""
            ).strip()
            request_id = str(
                approval.get("request_id") or ""
            ).strip()
            if not requester or not request_id:
                raise ValueError(
                    "two-person release approval metadata is missing"
                )
            if requester == approval["actor"]:
                raise ValueError(
                    "release requester and approver must be different identities"
                )
            if str(owner or "").strip() == approval["actor"]:
                raise ValueError(
                    "release approver cannot execute the same production release"
                )
        created = datetime.fromisoformat(approval["created_at"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        approval_age = (
            datetime.now(timezone.utc)
            - created.astimezone(timezone.utc)
        ).total_seconds()
        if (
            approval_age < 0
            or approval_age > self.approval_max_age_seconds
        ):
            raise ValueError("release approval has expired")

        stored_plan = approval.get("plan")
        if not isinstance(stored_plan, dict):
            raise ValueError("release approval plan is invalid")
        stored_adapters = stored_plan.get("adapters", [])
        if not isinstance(stored_adapters, list):
            raise ValueError("release approval adapter set is invalid")
        adapter_ids = [
            str(item.get("adapter_id") or "")
            for item in stored_adapters
            if isinstance(item, dict)
        ]
        current = self.plan(
            approval["target_slot"],
            adapter_ids,
        )
        if current.fingerprint != approval["fingerprint"]:
            raise ValueError("release approval is stale")

        run_id = approval_id
        self._acquire(run_id, owner)
        try:
            with self.db.Session() as session:
                row = session.get(
                    ReleaseApprovalRecord,
                    approval_id,
                )
                if row is None or row.consumed_at is not None:
                    raise ValueError(
                        "release approval is no longer available"
                    )
                row.consumed_at = utcnow()
                session.commit()

            linked_request_id = str(
                approval.get("request_id") or ""
            ).strip() or None
            self._set_request_status(
                linked_request_id,
                "executing",
            )
            try:
                result = self.controller.run(
                    current.target_slot,
                    [name for name, _ in current.adapters],
                    governance={
                        "approval_id": approval_id,
                        "request_id": approval.get(
                            "request_id"
                        ),
                        "requested_by": approval.get(
                            "requested_by"
                        ),
                        "approved_by": approval["actor"],
                        "executed_by": redact_text(
                            str(owner or "operator")
                        )[:200],
                        "plan_fingerprint": current.fingerprint,
                    },
                )
            except Exception:
                self._set_request_status(
                    linked_request_id,
                    "failed",
                )
                raise
            self._set_request_status(
                linked_request_id,
                (
                    "completed"
                    if result.get("status") == "succeeded"
                    else "failed"
                ),
            )
            result["approval_id"] = approval_id
            result["plan_fingerprint"] = current.fingerprint
            return result
        finally:
            self._release(run_id)

    def lease(self) -> dict | None:
        with self.db.Session() as session:
            row = session.get(ReleaseLeaseRecord, "openclaw")
            if row is None:
                return None
            acquired = row.acquired_at
            if acquired.tzinfo is None:
                acquired = acquired.replace(tzinfo=timezone.utc)
            expires = acquired + timedelta(
                seconds=self.lease_max_age_seconds
            )
            return {
                "service": row.service,
                "release_id": row.release_id,
                "owner": row.owner,
                "acquired_at": acquired.isoformat(),
                "expires_at": expires.isoformat(),
                "expired": datetime.now(timezone.utc) > expires,
            }
