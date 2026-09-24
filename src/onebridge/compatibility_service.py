from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from sqlalchemy import select

from .adapters.base import AdapterRegistry
from .db import CompatibilityRecord, Database, QualificationRecord
from .qualification import AdapterQualification, qualify_adapter


@dataclass(frozen=True, slots=True)
class CompatibilityStatus:
    adapter_id: str
    version: str
    state: str
    contract_version: str
    notes: str
    latest_qualification_passed: bool | None
    latest_qualification_id: int | None


class CompatibilityService:
    """Persistent compatibility matrix with evidence-gated promotion."""

    def __init__(self, db: Database, registry: AdapterRegistry) -> None:
        self.db = db
        self.registry = registry

    def register_current(
        self,
        adapter_id: str,
        *,
        state: str = "candidate",
        notes: str = "",
    ) -> CompatibilityStatus:
        if state not in {"candidate", "active", "blocked"}:
            raise ValueError("invalid compatibility state")
        adapter = self.registry.get(adapter_id)

        with self.db.Session() as session:
            row = session.scalar(
                select(CompatibilityRecord).where(
                    CompatibilityRecord.adapter_id == adapter.name,
                    CompatibilityRecord.version == adapter.version,
                )
            )
            if row is None:
                row = CompatibilityRecord(
                    adapter_id=adapter.name,
                    version=adapter.version,
                    state=state,
                    contract_version="1.0",
                    notes=notes,
                )
                session.add(row)
            else:
                row.notes = notes or row.notes
                if row.state == "blocked" and state != "blocked":
                    row.state = state
            session.commit()
        return self.get(adapter.name, adapter.version)

    def qualify_current(self, adapter_id: str) -> AdapterQualification:
        adapter = self.registry.get(adapter_id)
        if str(adapter.version).startswith("mock-"):
            raise ValueError("mock adapters cannot be qualified for promotion")
        self.register_current(adapter_id, state="candidate")
        result = qualify_adapter(adapter)

        with self.db.Session() as session:
            session.add(
                QualificationRecord(
                    adapter_id=result.adapter_id,
                    version=result.version,
                    passed=1 if result.passed else 0,
                    report_json=json.dumps(
                        result.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
            )
            row = session.scalar(
                select(CompatibilityRecord).where(
                    CompatibilityRecord.adapter_id == result.adapter_id,
                    CompatibilityRecord.version == result.version,
                )
            )
            if row is not None and not result.passed:
                row.state = "blocked"
                row.notes = "latest qualification failed"
            session.commit()
        return result

    def _latest_qualification(
        self,
        session,
        adapter_id: str,
        version: str,
    ) -> QualificationRecord | None:
        return session.scalar(
            select(QualificationRecord)
            .where(
                QualificationRecord.adapter_id == adapter_id,
                QualificationRecord.version == version,
            )
            .order_by(QualificationRecord.id.desc())
            .limit(1)
        )

    def promote(self, adapter_id: str, version: str) -> CompatibilityStatus:
        with self.db.Session() as session:
            candidate = session.scalar(
                select(CompatibilityRecord).where(
                    CompatibilityRecord.adapter_id == adapter_id,
                    CompatibilityRecord.version == version,
                )
            )
            if candidate is None:
                raise KeyError((adapter_id, version))
            if candidate.state not in {"candidate", "blocked"}:
                raise ValueError("only candidate or blocked versions can be promoted")

            qualification = self._latest_qualification(
                session,
                adapter_id,
                version,
            )
            if qualification is None:
                raise ValueError("promotion requires qualification evidence")
            if not bool(qualification.passed):
                raise ValueError("latest qualification did not pass")

            active_rows = list(
                session.scalars(
                    select(CompatibilityRecord).where(
                        CompatibilityRecord.adapter_id == adapter_id,
                        CompatibilityRecord.state == "active",
                    )
                )
            )
            for row in active_rows:
                if row.version != version:
                    row.state = "candidate"
                    row.notes = "previous active version"

            candidate.state = "active"
            candidate.notes = candidate.notes or "qualified active version"
            session.commit()

        return self.get(adapter_id, version)

    def block(
        self,
        adapter_id: str,
        version: str,
        *,
        notes: str = "",
    ) -> CompatibilityStatus:
        with self.db.Session() as session:
            row = session.scalar(
                select(CompatibilityRecord).where(
                    CompatibilityRecord.adapter_id == adapter_id,
                    CompatibilityRecord.version == version,
                )
            )
            if row is None:
                raise KeyError((adapter_id, version))
            row.state = "blocked"
            row.notes = notes or "blocked by operator"
            session.commit()
        return self.get(adapter_id, version)

    def get(self, adapter_id: str, version: str) -> CompatibilityStatus:
        with self.db.Session() as session:
            row = session.scalar(
                select(CompatibilityRecord).where(
                    CompatibilityRecord.adapter_id == adapter_id,
                    CompatibilityRecord.version == version,
                )
            )
            if row is None:
                raise KeyError((adapter_id, version))
            qualification = self._latest_qualification(
                session,
                adapter_id,
                version,
            )
            return CompatibilityStatus(
                adapter_id=row.adapter_id,
                version=row.version,
                state=row.state,
                contract_version=row.contract_version,
                notes=row.notes,
                latest_qualification_passed=(
                    bool(qualification.passed)
                    if qualification is not None
                    else None
                ),
                latest_qualification_id=(
                    qualification.id
                    if qualification is not None
                    else None
                ),
            )

    def require_active_registry(self) -> None:
        errors: list[str] = []
        for adapter in self.registry.list():
            if str(adapter.version).startswith("mock-"):
                errors.append(
                    f"{adapter.name}:mock_adapter_not_allowed"
                )
                continue
            try:
                status = self.get(adapter.name, adapter.version)
            except KeyError:
                errors.append(
                    f"{adapter.name}@{adapter.version}:compatibility_entry_missing"
                )
                continue
            if status.state != "active":
                errors.append(
                    f"{adapter.name}@{adapter.version}:not_active"
                )
            if status.latest_qualification_passed is not True:
                errors.append(
                    f"{adapter.name}@{adapter.version}:qualification_missing_or_failed"
                )
        if errors:
            raise RuntimeError(
                "qualified adapter gate failed: " + ", ".join(errors)
            )

    def list(self) -> list[CompatibilityStatus]:
        with self.db.Session() as session:
            rows = list(
                session.scalars(
                    select(CompatibilityRecord).order_by(
                        CompatibilityRecord.adapter_id,
                        CompatibilityRecord.version,
                    )
                )
            )
            values: list[CompatibilityStatus] = []
            for row in rows:
                qualification = self._latest_qualification(
                    session,
                    row.adapter_id,
                    row.version,
                )
                values.append(
                    CompatibilityStatus(
                        adapter_id=row.adapter_id,
                        version=row.version,
                        state=row.state,
                        contract_version=row.contract_version,
                        notes=row.notes,
                        latest_qualification_passed=(
                            bool(qualification.passed)
                            if qualification is not None
                            else None
                        ),
                        latest_qualification_id=(
                            qualification.id
                            if qualification is not None
                            else None
                        ),
                    )
                )
            return values

    @staticmethod
    def as_dict(status: CompatibilityStatus) -> dict:
        return asdict(status)
