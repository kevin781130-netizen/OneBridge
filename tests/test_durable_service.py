from pathlib import Path

from onebridge.adapters.mock import default_mock_registry
from onebridge.artifacts import LocalObjectStore
from onebridge.audit import HashChainAuditLog
from onebridge.checkpoints import CheckpointStore
from onebridge.contracts import TaskContract
from onebridge.db import Database
from onebridge.durable_service import DurableOneBridgeService


def test_durable_service_records_audit_and_checkpoints(tmp_path: Path):
    db = Database(f"sqlite:///{tmp_path / 'db.sqlite'}")
    db.create_all()
    audit = HashChainAuditLog(tmp_path / "audit.jsonl")
    service = DurableOneBridgeService(
        db,
        default_mock_registry(),
        LocalObjectStore(tmp_path / "objects"),
        audit=audit,
        checkpoints=CheckpointStore(tmp_path / "checkpoints"),
    )
    contract = TaskContract.model_validate({
        "input": {"goal": "Build page", "required_outputs": ["content", "design"]},
        "context": {"tenant_id": "t1", "user_id": "u1"},
    })

    service.submit(contract)
    result = service.run(contract.task_id)

    assert result["status"] == "waiting_approval"
    assert audit.verify()["valid"] is True
    assert (tmp_path / "checkpoints" / contract.task_id / "step-0.json").is_file()
    assert (tmp_path / "checkpoints" / contract.task_id / "step-1.json").is_file()
