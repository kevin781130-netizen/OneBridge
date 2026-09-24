from pathlib import Path

from onebridge.adapters.mock import default_mock_registry
from onebridge.artifacts import LocalObjectStore
from onebridge.audit import HashChainAuditLog
from onebridge.checkpoints import CheckpointStore
from onebridge.contracts import TaskContract, TextArtifactRevisionRequest
from onebridge.db import Database
from onebridge.durable_service import DurableOneBridgeService


def test_human_revision_supersedes_latest_artifact(tmp_path: Path):
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
    })
    service.submit(contract)
    service.run(contract.task_id)
    original = service.artifacts(contract.task_id)[0]

    revised = service.revise_text_artifact(
        contract.task_id,
        original.artifact_id,
        TextArtifactRevisionRequest(
            content="<html><body>Edited</body></html>",
            filename="index.html",
            media_type="text/html",
            actor="reviewer",
            reason="Fix headline",
        ),
    )

    assert revised.revision == original.revision + 1
    assert revised.status == "awaiting_review"
    assert revised.producer_adapter == "human_review"
    values = service.artifacts(contract.task_id)
    old = next(item for item in values if item.artifact_id == original.artifact_id)
    assert old.status == "superseded"
    assert service.status(contract.task_id)["status"] == "waiting_approval"
    assert service.audit.verify()["valid"] is True


def test_revision_requires_latest_parent(tmp_path: Path):
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
        "input": {"goal": "Build page", "required_outputs": ["design"]},
        "context": {"tenant_id": "tenant", "user_id": "user"},
    })
    service.submit(contract)
    service.run(contract.task_id)
    original = service.artifacts(contract.task_id)[0]
    request = TextArtifactRevisionRequest(
        content="<html>1</html>",
        filename="index.html",
        media_type="text/html",
        actor="reviewer",
    )
    service.revise_text_artifact(contract.task_id, original.artifact_id, request)

    import pytest
    with pytest.raises(ValueError):
        service.revise_text_artifact(contract.task_id, original.artifact_id, request)
