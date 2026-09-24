from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import select

from .adapters.base import AdapterRegistry, AdapterRequest
from .adapters.validation import validate_adapter_outputs
from .artifacts import LocalObjectStore, S3ObjectStore
from .contracts import ApprovalRequest, ArtifactManifest, TaskContract, TextArtifactRevisionRequest
from .db import ApprovalRecord, ArtifactRecord, Database, ProjectRecord, TaskRecord
from .release import build_release_manifest
from .release_gate import evaluate_release_gate
from .telemetry import Telemetry


OUTPUT_ADAPTER = {
    "content": "flowise",
    "design": "open_design",
    "code": "hermes",
    "test_report": "hermes",
}


class OneBridgeService:
    def __init__(
        self,
        db: Database,
        registry: AdapterRegistry,
        store: LocalObjectStore | S3ObjectStore,
        *,
        telemetry: Telemetry | None = None,
    ) -> None:
        self.db = db
        self.registry = registry
        self.store = store
        self.telemetry = telemetry or Telemetry.disabled()

    def submit(self, contract: TaskContract) -> dict:
        with self.db.Session() as session:
            if session.get(TaskRecord, contract.task_id):
                raise ValueError("task_id already exists")
            project = session.get(ProjectRecord, contract.project_id)
            if project is None:
                project = ProjectRecord(
                    id=contract.project_id,
                    tenant_id=contract.context.tenant_id,
                    status="received",
                    goal=contract.input.goal,
                )
                session.add(project)
            task = TaskRecord(
                id=contract.task_id,
                project_id=contract.project_id,
                operation=contract.operation,
                status="queued",
                contract_json=contract.model_dump_json(),
            )
            session.add(task)
            session.commit()
        return self.status(contract.task_id)

    def contract(self, task_id: str) -> TaskContract:
        with self.db.Session() as session:
            task = session.get(TaskRecord, task_id)
            if task is None:
                raise KeyError(task_id)
            return TaskContract.model_validate_json(task.contract_json)

    def status(self, task_id: str) -> dict:
        with self.db.Session() as session:
            task = session.get(TaskRecord, task_id)
            if task is None:
                raise KeyError(task_id)
            return {
                "task_id": task.id,
                "project_id": task.project_id,
                "status": task.status,
                "attempt": task.attempt,
                "error": task.error,
            }

    def artifacts(self, task_id: str) -> list[ArtifactManifest]:
        with self.db.Session() as session:
            rows = list(session.scalars(select(ArtifactRecord).where(ArtifactRecord.task_id == task_id).order_by(ArtifactRecord.created_at)))
            return [
                ArtifactManifest(
                    artifact_id=row.id,
                    project_id=row.project_id,
                    task_id=row.task_id,
                    revision=row.revision,
                    kind=row.kind,
                    media_type=row.media_type,
                    sha256=row.sha256,
                    uri=row.uri,
                    producer_adapter=row.producer_adapter,
                    producer_version=row.producer_version,
                    status=row.status,
                    input_artifacts=json.loads(row.input_artifacts_json or "[]"),
                )
                for row in rows
            ]

    def run(self, task_id: str) -> dict:
        with self.db.Session() as session:
            task = session.get(TaskRecord, task_id)
            if task is None:
                raise KeyError(task_id)
            if task.status == "cancelled":
                raise ValueError("cancelled task cannot run")
            contract = TaskContract.model_validate_json(task.contract_json)
            task.status = "running"
            task.attempt += 1
            task.error = None
            task.project.status = "running"
            session.commit()

        try:
            existing_artifacts = self.artifacts(task_id)
            completed_kinds = {item.kind for item in existing_artifacts}
            produced_ids = [item.artifact_id for item in existing_artifacts]

            for kind in contract.input.required_outputs:
                if kind in completed_kinds:
                    continue

                adapter_name = OUTPUT_ADAPTER.get(kind)
                if not adapter_name:
                    raise RuntimeError(
                        f"no adapter route for output kind: {kind}"
                    )
                adapter = self.registry.get(adapter_name)

                missing_for_adapter = [
                    required
                    for required in contract.input.required_outputs
                    if (
                        required not in completed_kinds
                        and OUTPUT_ADAPTER.get(required) == adapter_name
                    )
                ]
                lineage = list(produced_ids)
                request = AdapterRequest(
                    task_id=contract.task_id,
                    project_id=contract.project_id,
                    goal=contract.input.goal,
                    operation=contract.operation,
                    inputs=contract.metadata,
                    artifact_inputs=[
                        item.model_dump()
                        for item in self.artifacts(task_id)
                    ],
                )
                adapter_attributes = {
                    "onebridge.adapter": adapter.name,
                    "onebridge.adapter_version": adapter.version,
                    "onebridge.task_id": contract.task_id,
                    "onebridge.project_id": contract.project_id,
                }
                adapter_started = time.monotonic()
                try:
                    with self.telemetry.span(
                        "onebridge.adapter.execute",
                        adapter_attributes,
                    ):
                        result = adapter.execute(request)
                except Exception:
                    self.telemetry.count(
                        "onebridge.adapter.failures",
                        attributes=adapter_attributes,
                    )
                    raise
                else:
                    self.telemetry.count(
                        "onebridge.adapter.calls",
                        attributes=adapter_attributes,
                    )
                    self.telemetry.record(
                        "onebridge.adapter.duration_seconds",
                        time.monotonic() - adapter_started,
                        adapter_attributes,
                    )

                for required_kind in missing_for_adapter:
                    report = validate_adapter_outputs(
                        result.outputs,
                        expected_kind=required_kind,
                    )
                    if not report.valid:
                        raise RuntimeError(
                            f"adapter {adapter_name} output validation failed: "
                            + "; ".join(report.errors)
                        )

                selected = {}
                for output in result.outputs:
                    if (
                        output.kind in missing_for_adapter
                        and output.kind not in selected
                    ):
                        selected[output.kind] = output

                missing = [
                    required
                    for required in missing_for_adapter
                    if required not in selected
                ]
                if missing:
                    raise RuntimeError(
                        f"adapter {adapter_name} did not produce required outputs: "
                        + ", ".join(missing)
                    )

                with self.db.Session() as session:
                    for produced_kind in missing_for_adapter:
                        output = selected[produced_kind]
                        stored = self.store.put_bytes(
                            output.content,
                            filename=output.filename,
                        )
                        revision = 1 + (
                            session.scalar(
                                select(ArtifactRecord.revision)
                                .where(
                                    ArtifactRecord.task_id == task_id,
                                    ArtifactRecord.kind == produced_kind,
                                )
                                .order_by(ArtifactRecord.revision.desc())
                                .limit(1)
                            )
                            or 0
                        )
                        record = ArtifactRecord(
                            id=f"art_{uuid4().hex}",
                            project_id=contract.project_id,
                            task_id=contract.task_id,
                            revision=revision,
                            kind=produced_kind,
                            media_type=output.media_type,
                            sha256=stored.sha256,
                            uri=stored.uri,
                            producer_adapter=adapter.name,
                            producer_version=adapter.version,
                            status=(
                                "awaiting_review"
                                if contract.policy.approval == "before_publish"
                                else "approved"
                            ),
                            input_artifacts_json=json.dumps(lineage),
                        )
                        session.add(record)
                        session.flush()
                        produced_ids.append(record.id)
                        completed_kinds.add(produced_kind)
                    session.commit()

            with self.db.Session() as session:
                task = session.get(TaskRecord, task_id)
                assert task is not None
                task.status = (
                    "waiting_approval"
                    if contract.policy.approval == "before_publish"
                    else "succeeded"
                )
                task.project.status = task.status
                session.commit()
        except Exception as exc:
            with self.db.Session() as session:
                task = session.get(TaskRecord, task_id)
                if task is not None:
                    task.status = "failed"
                    task.error = str(exc)[:8000]
                    task.project.status = "failed"
                    session.commit()
            raise
        return self.status(task_id)

    def read_text_artifact(
        self,
        task_id: str,
        artifact_id: str,
        *,
        max_bytes: int = 5_000_000,
    ) -> dict:
        artifact = next(
            (
                item
                for item in self.artifacts(task_id)
                if item.artifact_id == artifact_id
            ),
            None,
        )
        if artifact is None:
            raise KeyError(artifact_id)

        media_type = artifact.media_type.lower().split(";", 1)[0].strip()
        if not (
            media_type.startswith("text/")
            or media_type in {"application/json", "application/xml"}
            or media_type.endswith("+json")
        ):
            raise ValueError("artifact is not a supported text type")

        with tempfile.TemporaryDirectory(prefix="onebridge-read-") as temp:
            target = Path(temp) / "artifact"
            materialized = self.store.get_file(artifact.uri, target)
            if materialized.stat().st_size > max_bytes:
                raise ValueError("artifact text content exceeds review limit")
            try:
                value = materialized.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("artifact is not valid UTF-8 text") from exc

        filename = Path(urlparse(artifact.uri).path).name or "artifact.txt"
        return {
            "artifact_id": artifact.artifact_id,
            "revision": artifact.revision,
            "kind": artifact.kind,
            "media_type": artifact.media_type,
            "filename": filename,
            "content": value,
            "sha256": artifact.sha256,
        }

    def revise_text_artifact(
        self,
        task_id: str,
        artifact_id: str,
        request: TextArtifactRevisionRequest,
    ) -> ArtifactManifest:
        content = request.content.encode("utf-8")
        stored = self.store.put_bytes(content, filename=request.filename)

        with self.db.Session() as session:
            task = session.get(TaskRecord, task_id)
            if task is None:
                raise KeyError(task_id)

            parent = session.get(ArtifactRecord, artifact_id)
            if parent is None or parent.task_id != task_id:
                raise KeyError(artifact_id)

            latest = session.scalar(
                select(ArtifactRecord)
                .where(
                    ArtifactRecord.task_id == task_id,
                    ArtifactRecord.kind == parent.kind,
                )
                .order_by(ArtifactRecord.revision.desc())
                .limit(1)
            )
            if latest is None or latest.id != parent.id:
                raise ValueError("artifact revision parent must be the latest revision")

            parent.status = "superseded"
            record = ArtifactRecord(
                id=f"art_{uuid4().hex}",
                project_id=parent.project_id,
                task_id=parent.task_id,
                revision=parent.revision + 1,
                kind=parent.kind,
                media_type=request.media_type,
                sha256=stored.sha256,
                uri=stored.uri,
                producer_adapter="human_review",
                producer_version="1",
                status="awaiting_review",
                input_artifacts_json=json.dumps([parent.id]),
            )
            session.add(record)
            task.status = "waiting_approval"
            task.project.status = "waiting_approval"
            session.commit()

            return ArtifactManifest(
                artifact_id=record.id,
                project_id=record.project_id,
                task_id=record.task_id,
                revision=record.revision,
                kind=record.kind,
                media_type=record.media_type,
                sha256=record.sha256,
                uri=record.uri,
                producer_adapter=record.producer_adapter,
                producer_version=record.producer_version,
                status=record.status,
                input_artifacts=[parent.id],
            )

    def approve(self, task_id: str, request: ApprovalRequest) -> dict:
        with self.db.Session() as session:
            task = session.get(TaskRecord, task_id)
            if task is None:
                raise KeyError(task_id)
            rows = list(session.scalars(select(ArtifactRecord).where(
                ArtifactRecord.task_id == task_id,
                ArtifactRecord.id.in_(request.artifact_ids),
            )))
            if len(rows) != len(set(request.artifact_ids)):
                raise ValueError("one or more artifact_ids are not part of the task")
            for artifact in rows:
                artifact.status = "approved" if request.decision == "approve" else "rejected"
                session.add(ApprovalRecord(
                    artifact_id=artifact.id,
                    decision=request.decision,
                    actor=request.actor,
                    reason=request.reason,
                ))
            if request.decision == "reject":
                task.status = "blocked"
                task.project.status = "waiting_approval"
            else:
                session.flush()
                contract = TaskContract.model_validate_json(task.contract_json)
                required_approved = True
                for kind in contract.input.required_outputs:
                    latest = session.scalar(
                        select(ArtifactRecord)
                        .where(
                            ArtifactRecord.task_id == task_id,
                            ArtifactRecord.kind == kind,
                        )
                        .order_by(ArtifactRecord.revision.desc())
                        .limit(1)
                    )
                    if latest is None or latest.status != "approved":
                        required_approved = False
                        break
                if required_approved:
                    task.status = "succeeded"
                    task.project.status = "approved"
                else:
                    task.status = "waiting_approval"
                    task.project.status = "waiting_approval"
            session.commit()
        return self.status(task_id)

    def release(self, task_id: str) -> ArtifactManifest:
        contract = self.contract(task_id)
        status_value = self.status(task_id)
        artifacts = self.artifacts(task_id)
        decision = evaluate_release_gate(
            contract,
            artifacts,
            task_status=status_value["status"],
        )
        if not decision.allowed:
            raise ValueError(
                "release_gate_blocked:" + ",".join(decision.reasons)
            )

        selected_ids = set(decision.selected_artifact_ids)
        selected = [
            artifact
            for artifact in artifacts
            if artifact.artifact_id in selected_ids
        ]
        manifest = build_release_manifest(selected)
        content = (
            json.dumps(
                asdict(manifest),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        stored = self.store.put_bytes(
            content,
            filename="release_manifest.json",
        )

        with self.db.Session() as session:
            task = session.get(TaskRecord, task_id)
            if task is None:
                raise KeyError(task_id)
            revision = 1 + (
                session.scalar(
                    select(ArtifactRecord.revision)
                    .where(
                        ArtifactRecord.task_id == task_id,
                        ArtifactRecord.kind == "release",
                    )
                    .order_by(ArtifactRecord.revision.desc())
                    .limit(1)
                )
                or 0
            )
            record = ArtifactRecord(
                id=f"art_{uuid4().hex}",
                project_id=task.project_id,
                task_id=task_id,
                revision=revision,
                kind="release",
                media_type="application/json",
                sha256=stored.sha256,
                uri=stored.uri,
                producer_adapter="onebridge",
                producer_version="0.1.0",
                status="released",
                input_artifacts_json=json.dumps(
                    list(decision.selected_artifact_ids)
                ),
            )
            session.add(record)
            task.project.status = "released"
            session.commit()

            return ArtifactManifest(
                artifact_id=record.id,
                project_id=record.project_id,
                task_id=record.task_id,
                revision=record.revision,
                kind="release",
                media_type=record.media_type,
                sha256=record.sha256,
                uri=record.uri,
                producer_adapter=record.producer_adapter,
                producer_version=record.producer_version,
                status="released",
                input_artifacts=list(decision.selected_artifact_ids),
            )

    def retry(self, task_id: str) -> dict:
        with self.db.Session() as session:
            task = session.get(TaskRecord, task_id)
            if task is None:
                raise KeyError(task_id)
            if task.status not in {"failed", "blocked"}:
                raise ValueError("retry is only valid for failed or blocked tasks")
            task.status = "queued"
            task.error = None
            task.project.status = "planned"
            session.commit()
        return self.status(task_id)

    def cancel(self, task_id: str) -> dict:
        with self.db.Session() as session:
            task = session.get(TaskRecord, task_id)
            if task is None:
                raise KeyError(task_id)
            if task.status in {"succeeded", "cancelled"}:
                return self.status(task_id)
            task.status = "cancelled"
            task.project.status = "cancelled"
            session.commit()
        return self.status(task_id)
