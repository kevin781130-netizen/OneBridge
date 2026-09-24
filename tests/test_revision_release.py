from pathlib import Path

from onebridge.adapters.mock import default_mock_registry
from onebridge.artifacts import LocalObjectStore
from onebridge.audit import HashChainAuditLog
from onebridge.checkpoints import CheckpointStore
from onebridge.contracts import ApprovalRequest, TaskContract, TextArtifactRevisionRequest
from onebridge.db import Database
from onebridge.durable_service import DurableOneBridgeService


def test_revised_artifact_can_be_approved_and_released(tmp_path: Path):
    db = Database(f"sqlite:///{tmp_path / 'db.sqlite'}")
    db.create_all()
    service = DurableOneBridgeService(
        db,
        default_mock_registry(),
        LocalObjectStore(tmp_path / "objects"),
        audit=HashChainAuditLog(tmp_path / "audit.jsonl"),
        checkpoints=CheckpointStore(tmp_path / "checkpoints"),
    )

    contract = TaskContract.model_validate({
        "input": {
            "goal": "Build page",
            "required_outputs": ["design"],
        },
        "context": {"tenant_id": "tenant", "user_id": "user"},
        "policy": {"approval": "before_publish"},
    })
    service.submit(contract)
    service.run(contract.task_id)
    original = service.artifacts(contract.task_id)[0]

    revised = service.revise_text_artifact(
        contract.task_id,
        original.artifact_id,
        TextArtifactRevisionRequest(
            content="<html><body>Reviewed</body></html>",
            filename="index.html",
            media_type="text/html",
            actor="reviewer",
        ),
    )
    approved = service.approve(
        contract.task_id,
        ApprovalRequest(
            artifact_ids=[revised.artifact_id],
            decision="approve",
            actor="reviewer",
        ),
    )
    assert approved["status"] == "succeeded"

    release = service.release(contract.task_id)
    assert release.kind == "release"
    assert release.status == "released"
    assert release.input_artifacts == [revised.artifact_id]
    assert len(release.sha256) == 64
    assert service.audit.verify()["valid"] is True
