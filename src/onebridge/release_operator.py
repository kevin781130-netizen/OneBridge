from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from .db import (
    Database,
    ReleaseApprovalRecord,
    ReleaseLeaseRecord,
    utcnow,
)
from .production_release import ProductionReleaseController


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
    ) -> dict:
        if decision not in {"approve", "reject"}:
            raise ValueError("release decision must be approve or reject")
        actor_value = str(actor or "").strip()
        if not actor_value:
            raise ValueError("release approval actor is required")

        approval_id = f"relapp_{uuid4().hex}"
        with self.db.Session() as session:
            session.add(
                ReleaseApprovalRecord(
                    id=approval_id,
                    service=plan.service,
                    target_slot=plan.target_slot,
                    fingerprint=plan.fingerprint,
                    adapters_json=json.dumps(
                        [
                            {
                                "adapter_id": adapter_id,
                                "version": version,
                            }
                            for adapter_id, version in plan.adapters
                        ],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    decision=decision,
                    actor=actor_value[:200],
                    reason=str(reason or "")[:4000],
                )
            )
            session.commit()
        return self.approval(approval_id)

    def approval(self, approval_id: str) -> dict:
        with self.db.Session() as session:
            row = session.get(ReleaseApprovalRecord, approval_id)
            if row is None:
                raise KeyError(approval_id)
            return {
                "approval_id": row.id,
                "service": row.service,
                "target_slot": row.target_slot,
                "fingerprint": row.fingerprint,
                "decision": row.decision,
                "actor": row.actor,
                "reason": row.reason,
                "consumed_at": (
                    row.consumed_at.isoformat()
                    if row.consumed_at is not None
                    else None
                ),
                "created_at": row.created_at.isoformat(),
            }

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
            row.decision = "revoked"
            row.actor = actor_value[:200]
            row.reason = str(reason or "revoked by operator")[:4000]
            session.commit()
        return self.approval(approval_id)

    def approvals(self, *, limit: int = 50) -> list[dict]:
        from sqlalchemy import select

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
                    "consumed_at": (
                        row.consumed_at.isoformat()
                        if row.consumed_at is not None
                        else None
                    ),
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

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

        with self.db.Session() as session:
            row = session.get(ReleaseApprovalRecord, approval_id)
            if row is None:
                raise KeyError(approval_id)
            try:
                stored = json.loads(row.adapters_json or "[]")
            except json.JSONDecodeError as exc:
                raise ValueError("release approval adapter set is invalid") from exc

        adapter_ids = [
            str(item.get("adapter_id") or "")
            for item in stored
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

            result = self.controller.run(
                current.target_slot,
                [name for name, _ in current.adapters],
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
            return {
                "service": row.service,
                "release_id": row.release_id,
                "owner": row.owner,
                "acquired_at": row.acquired_at.isoformat(),
            }
