from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ProjectRecord(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(40), default="received", index=True)
    goal: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    operation: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    contract_json: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    project: Mapped[ProjectRecord] = relationship()


class ArtifactRecord(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    media_type: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    uri: Mapped[str] = mapped_column(Text)
    producer_adapter: Mapped[str] = mapped_column(String(200))
    producer_version: Mapped[str] = mapped_column(String(100), default="0")
    status: Mapped[str] = mapped_column(String(40), default="generated", index=True)
    input_artifacts_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApprovalRecord(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), index=True)
    decision: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompatibilityRecord(Base):
    __tablename__ = "compatibility_entries"
    __table_args__ = (
        UniqueConstraint("adapter_id", "version", name="uq_compatibility_adapter_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    adapter_id: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(20), default="candidate", index=True)
    contract_version: Mapped[str] = mapped_column(String(20), default="1.0")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )


class QualificationRecord(Base):
    __tablename__ = "adapter_qualifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    adapter_id: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(100), index=True)
    passed: Mapped[int] = mapped_column(Integer, default=0, index=True)
    report_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeploymentRecord(Base):
    __tablename__ = "service_deployments"
    __table_args__ = (
        UniqueConstraint(
            "service",
            "slot",
            name="uq_service_deployment_slot",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    service: Mapped[str] = mapped_column(String(100), index=True)
    slot: Mapped[str] = mapped_column(String(40))
    endpoint: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(
        String(20),
        default="candidate",
        index=True,
    )
    health_status: Mapped[str] = mapped_column(
        String(20),
        default="unknown",
        index=True,
    )
    health_evidence_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )
    promoted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )


class DeploymentActionRecord(Base):
    __tablename__ = "deployment_actions"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    service: Mapped[str] = mapped_column(String(100), index=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    from_slot: Mapped[str | None] = mapped_column(String(40), nullable=True)
    to_slot: Mapped[str] = mapped_column(String(40))
    version: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        index=True,
    )
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )


class ProductionReleaseRecord(Base):
    __tablename__ = "production_releases"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    service: Mapped[str] = mapped_column(String(100), index=True)
    target_slot: Mapped[str] = mapped_column(String(40), index=True)
    previous_slot: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="started",
        index=True,
    )
    adapters_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )


class ReleaseLeaseRecord(Base):
    __tablename__ = "release_leases"

    service: Mapped[str] = mapped_column(String(100), primary_key=True)
    release_id: Mapped[str] = mapped_column(String(96), index=True)
    owner: Mapped[str] = mapped_column(String(200))
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
    )


class ReleaseApprovalRecord(Base):
    __tablename__ = "release_approvals"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    service: Mapped[str] = mapped_column(String(100), index=True)
    target_slot: Mapped[str] = mapped_column(String(40), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    adapters_json: Mapped[str] = mapped_column(Text, default="[]")
    decision: Mapped[str] = mapped_column(String(20), index=True)
    actor: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text, default="")
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
    )


class Database:
    def __init__(self, url: str) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, future=True, connect_args=connect_args)
        self.Session = sessionmaker(self.engine, expire_on_commit=False, class_=Session)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def session(self) -> Iterator[Session]:
        with self.Session() as session:
            yield session

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self.Session() as session:
            return session.get(TaskRecord, task_id)

    def task_artifacts(self, task_id: str) -> list[ArtifactRecord]:
        with self.Session() as session:
            return list(session.scalars(select(ArtifactRecord).where(ArtifactRecord.task_id == task_id).order_by(ArtifactRecord.created_at)))
