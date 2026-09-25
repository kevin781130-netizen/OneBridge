from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import select

from .compatibility_service import CompatibilityService
from .db import Database, ProductionReleaseRecord
from .deployment_switch import DeploymentSwitchService, probe_deployment
from .redaction import redact_text
from .release_pipeline import AdapterReleasePipeline


class ProductionReleaseController:
    def __init__(
        self,
        db: Database,
        compatibility: CompatibilityService,
        deployments: DeploymentSwitchService,
        *,
        smoke_url: str | None = None,
        timeout_seconds: float = 5.0,
        smoke_probe=probe_deployment,
    ) -> None:
        self.db = db
        self.compatibility = compatibility
        self.deployments = deployments
        self.smoke_url = str(smoke_url or "").strip() or None
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 20.0))
        self.smoke_probe = smoke_probe

    def _active(self):
        return next(
            (x for x in self.deployments.list("openclaw") if x.state == "active"),
            None,
        )

    def _create(self, slot: str, previous: str | None, adapters: tuple[str, ...]) -> str:
        release_id = f"rel_{uuid4().hex}"
        with self.db.Session() as session:
            session.add(ProductionReleaseRecord(
                id=release_id,
                service="openclaw",
                target_slot=slot,
                previous_slot=previous,
                status="started",
                adapters_json=json.dumps(list(adapters)),
                evidence_json="{}",
            ))
            session.commit()
        return release_id

    def _save(
        self,
        release_id: str,
        status: str,
        evidence: dict,
        error: str | None = None,
    ) -> None:
        with self.db.Session() as session:
            row = session.get(ProductionReleaseRecord, release_id)
            if row is None:
                return
            row.status = status
            row.evidence_json = json.dumps(
                evidence,
                ensure_ascii=False,
                sort_keys=True,
            )
            row.error = redact_text(str(error))[:4000] if error else None
            session.commit()

    def _rollback(self, release_id: str, evidence: dict, errors: list[str]):
        try:
            value = self.deployments.rollback("openclaw")
        except Exception as exc:
            message = redact_text(f"rollback:{type(exc).__name__}:{exc}")[:1000]
            errors.append(message)
            self._save(release_id, "rollback_failed", evidence, message)
            return False, None
        evidence["rollback"] = {"slot": value.slot, "version": value.version}
        self._save(release_id, "rolled_back", evidence, errors[-1] if errors else "rolled_back")
        return True, value.slot

    def run(self, target_slot: str, adapter_ids: list[str] | tuple[str, ...]) -> dict:
        requested = tuple(dict.fromkeys(x.strip() for x in adapter_ids if x.strip()))
        if not requested:
            raise ValueError("empty adapter release set")

        if (
            self.deployments.actuator is not None
            and self.smoke_url is None
        ):
            raise ValueError(
                "external deployment actuation requires an OpenClaw smoke URL"
            )

        target = self.deployments.get("openclaw", target_slot)
        previous = self._active()
        if previous is not None and previous.slot == target.slot:
            raise ValueError("target slot already active")

        release_id = self._create(
            target.slot,
            previous.slot if previous else None,
            requested,
        )
        evidence: dict = {}
        errors: list[str] = []

        qualified = AdapterReleasePipeline(self.compatibility).run(
            requested,
            promote=False,
        )
        evidence["qualification"] = qualified.to_dict()
        adapters = [
            (item.adapter_id, item.version)
            for item in qualified.items
        ]
        if not qualified.passed:
            errors.extend(qualified.errors or ("qualification_failed",))
            self._save(release_id, "qualification_failed", evidence, ";".join(errors))
            return self.get(release_id)

        try:
            target = self.deployments.probe(
                "openclaw",
                target.slot,
                timeout_seconds=self.timeout_seconds,
            )
            evidence["target_probe"] = target.health_evidence
            if target.health_status != "healthy":
                raise RuntimeError("target unhealthy")
            if previous is not None:
                previous = self.deployments.probe(
                    "openclaw",
                    previous.slot,
                    timeout_seconds=self.timeout_seconds,
                )
                evidence["rollback_probe"] = previous.health_evidence
                if previous.health_status != "healthy":
                    raise RuntimeError("rollback target unhealthy")
        except Exception as exc:
            message = redact_text(f"probe:{type(exc).__name__}:{exc}")[:1000]
            errors.append(message)
            self._save(release_id, "probe_failed", evidence, message)
            return self.get(release_id)

        try:
            promoted = self.deployments.promote("openclaw", target.slot)
        except Exception as exc:
            message = redact_text(f"switch:{type(exc).__name__}:{exc}")[:1000]
            errors.append(message)
            actions = self.deployments.actions("openclaw", limit=1)
            evidence["deployment_actions"] = actions
            status = (
                "reconcile_required"
                if actions and actions[0].get("status") == "reconcile_required"
                else "switch_failed"
            )
            self._save(release_id, status, evidence, message)
            return self.get(release_id)

        try:
            smoke = self.smoke_probe(
                self.smoke_url or promoted.endpoint,
                timeout_seconds=self.timeout_seconds,
            )
            evidence["smoke"] = smoke.to_dict()
            if not smoke.healthy:
                raise RuntimeError("smoke unhealthy")
            if (
                smoke.reported_active_slot is not None
                and smoke.reported_active_slot != promoted.slot
            ):
                raise RuntimeError("smoke active slot mismatch")
            if (
                smoke.reported_version is not None
                and smoke.reported_version != promoted.version
            ):
                raise RuntimeError("smoke version mismatch")
        except Exception as exc:
            errors.append(redact_text(f"smoke:{type(exc).__name__}:{exc}")[:1000])
            self._rollback(release_id, evidence, errors)
            return self.get(release_id)

        try:
            active = self.compatibility.promote_many(adapters)
        except Exception as exc:
            errors.append(redact_text(f"adapter_promote:{type(exc).__name__}:{exc}")[:1000])
            self._rollback(release_id, evidence, errors)
            return self.get(release_id)

        evidence["adapters"] = [
            {"adapter_id": x.adapter_id, "version": x.version, "state": x.state}
            for x in active
        ]
        evidence["active_slot"] = promoted.slot
        self._save(release_id, "succeeded", evidence)
        return self.get(release_id)

    def get(self, release_id: str) -> dict:
        with self.db.Session() as session:
            row = session.get(ProductionReleaseRecord, release_id)
            if row is None:
                raise KeyError(release_id)
            try:
                evidence = json.loads(row.evidence_json or "{}")
            except json.JSONDecodeError:
                evidence = {}
            return {
                "release_id": row.id,
                "service": row.service,
                "target_slot": row.target_slot,
                "previous_slot": row.previous_slot,
                "status": row.status,
                "evidence": evidence,
                "error": row.error,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }

    def list(self, limit: int = 50) -> list[dict]:
        with self.db.Session() as session:
            rows = list(session.scalars(
                select(ProductionReleaseRecord)
                .order_by(ProductionReleaseRecord.created_at.desc())
                .limit(max(1, min(int(limit), 200)))
            ))
            return [
                {
                    "release_id": row.id,
                    "target_slot": row.target_slot,
                    "previous_slot": row.previous_slot,
                    "status": row.status,
                    "error": row.error,
                    "created_at": row.created_at.isoformat(),
                    "updated_at": row.updated_at.isoformat(),
                }
                for row in rows
            ]
