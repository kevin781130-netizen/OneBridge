from __future__ import annotations

from .audit import HashChainAuditLog
from .checkpoints import CheckpointStore
from .contracts import ApprovalRequest, TaskContract, TextArtifactRevisionRequest
from .db import TaskRecord
from .service import OneBridgeService
from .workflow_graph import default_pipeline


class DurableOneBridgeService(OneBridgeService):
    """Durability wrapper that preserves the simple core service contract.

    It records task lifecycle events and reconstructs digest-validated workflow
    checkpoints from committed artifacts after each successful execution.
    """

    def __init__(
        self,
        *args,
        audit: HashChainAuditLog,
        checkpoints: CheckpointStore,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.audit = audit
        self.checkpoints = checkpoints

    def _contract(self, task_id: str) -> TaskContract:
        with self.db.Session() as session:
            task = session.get(TaskRecord, task_id)
            if task is None:
                raise KeyError(task_id)
            return TaskContract.model_validate_json(task.contract_json)

    def _checkpoint_artifacts(self, task_id: str) -> None:
        contract = self._contract(task_id)
        latest = {}
        for artifact in self.artifacts(task_id):
            current = latest.get(artifact.kind)
            if current is None or artifact.revision > current.revision:
                latest[artifact.kind] = artifact

        lineage: list[str] = []
        for step in default_pipeline(contract.input.required_outputs):
            artifact = latest.get(step.output_kind)
            if artifact is None:
                break
            self.checkpoints.save(
                task_id,
                step=step,
                input_lineage=tuple(lineage),
                output_artifact_ids=(artifact.artifact_id,),
                metadata={
                    "sha256": artifact.sha256,
                    "adapter": artifact.producer_adapter,
                    "adapter_version": artifact.producer_version,
                },
            )
            lineage.append(artifact.artifact_id)

    def submit(self, contract: TaskContract) -> dict:
        result = super().submit(contract)
        self.audit.append(
            "task.submitted",
            actor=contract.context.user_id,
            task_id=contract.task_id,
            payload={"project_id": contract.project_id, "operation": contract.operation},
        )
        return result

    def run(self, task_id: str) -> dict:
        self.audit.append("task.started", actor="onebridge", task_id=task_id)
        try:
            result = super().run(task_id)
        except Exception as exc:
            self.audit.append(
                "task.failed",
                actor="onebridge",
                task_id=task_id,
                payload={"error_type": type(exc).__name__},
            )
            raise

        self._checkpoint_artifacts(task_id)
        artifacts = self.artifacts(task_id)
        for artifact in artifacts:
            self.audit.append(
                "artifact.observed",
                actor="onebridge",
                task_id=task_id,
                artifact_id=artifact.artifact_id,
                payload={
                    "kind": artifact.kind,
                    "revision": artifact.revision,
                    "sha256": artifact.sha256,
                    "status": artifact.status,
                },
            )
        self.audit.append(
            "task.execution_completed",
            actor="onebridge",
            task_id=task_id,
            payload={"status": result["status"], "artifact_count": len(artifacts)},
        )
        return result

    def revise_text_artifact(
        self,
        task_id: str,
        artifact_id: str,
        request: TextArtifactRevisionRequest,
    ):
        revised = super().revise_text_artifact(task_id, artifact_id, request)
        self.audit.append(
            "artifact.revised",
            actor=request.actor,
            task_id=task_id,
            artifact_id=revised.artifact_id,
            payload={
                "parent_artifact_id": artifact_id,
                "revision": revised.revision,
                "sha256": revised.sha256,
                "reason": request.reason,
            },
        )
        return revised

    def approve(self, task_id: str, request: ApprovalRequest) -> dict:
        result = super().approve(task_id, request)
        for artifact_id in request.artifact_ids:
            self.audit.append(
                f"artifact.{request.decision}",
                actor=request.actor,
                task_id=task_id,
                artifact_id=artifact_id,
                payload={"reason": request.reason},
            )
        return result

    def release(self, task_id: str):
        released = super().release(task_id)
        self.audit.append(
            "task.released",
            actor="onebridge",
            task_id=task_id,
            artifact_id=released.artifact_id,
            payload={
                "release_sha256": released.sha256,
                "input_artifact_ids": released.input_artifacts,
            },
        )
        return released

    def retry(self, task_id: str) -> dict:
        result = super().retry(task_id)
        self.audit.append("task.requeued", actor="onebridge", task_id=task_id)
        return result

    def cancel(self, task_id: str) -> dict:
        result = super().cancel(task_id)
        self.audit.append("task.cancelled", actor="onebridge", task_id=task_id)
        return result
