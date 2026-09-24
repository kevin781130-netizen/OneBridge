from pathlib import Path

from onebridge.adapters.mock import default_mock_registry
from onebridge.artifacts import LocalObjectStore
from onebridge.contracts import ApprovalRequest, TaskContract
from onebridge.db import Database
from onebridge.service import OneBridgeService


def test_mock_end_to_end(tmp_path: Path):
    db = Database(f"sqlite:///{tmp_path / 'test.db'}")
    db.create_all()
    service = OneBridgeService(db, default_mock_registry(), LocalObjectStore(tmp_path / "objects"))
    contract = TaskContract.model_validate({
        "input": {
            "goal": "Create a product page",
            "required_outputs": ["content", "design", "code", "test_report"],
        },
        "context": {"tenant_id": "tenant", "user_id": "user", "channel": "test"},
        "policy": {"approval": "before_publish"},
    })

    assert service.submit(contract)["status"] == "queued"
    assert service.run(contract.task_id)["status"] == "waiting_approval"
    artifacts = service.artifacts(contract.task_id)
    assert {a.kind for a in artifacts} == {"content", "design", "code", "test_report"}
    assert all(a.status == "awaiting_review" for a in artifacts)
    assert all(a.sha256 and len(a.sha256) == 64 for a in artifacts)

    result = service.approve(
        contract.task_id,
        ApprovalRequest(
            artifact_ids=[a.artifact_id for a in artifacts],
            decision="approve",
            actor="tester",
            reason="looks good",
        ),
    )
    assert result["status"] == "succeeded"
