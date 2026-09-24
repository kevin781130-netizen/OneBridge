from pathlib import Path

from fastapi.testclient import TestClient

from onebridge.api import create_app
from onebridge.config import Settings


def test_release_gate_is_fail_closed_before_approval(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        state_root=tmp_path,
        storage_root=tmp_path / "objects",
        checkpoint_root=tmp_path / "checkpoints",
        audit_log=tmp_path / "audit.jsonl",
        identity_db=tmp_path / "identity.db",
    )
    client = TestClient(create_app(settings))
    created = client.post(
        "/api/v1/tasks",
        json={
            "input": {
                "goal": "Build page",
                "required_outputs": ["content"],
            },
            "context": {"tenant_id": "local", "user_id": "user"},
            "policy": {"approval": "before_publish"},
        },
    )
    task_id = created.json()["task_id"]
    assert client.post(f"/api/v1/tasks/{task_id}/run").status_code == 200

    before = client.get(f"/api/v1/tasks/{task_id}/release-gate").json()
    assert before["allowed"] is False
    assert "human_approval_incomplete" in before["reasons"]

    artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
    approved = client.post(
        f"/api/v1/tasks/{task_id}/approve",
        json={
            "artifact_ids": [item["artifact_id"] for item in artifacts],
            "decision": "approve",
            "actor": "reviewer",
        },
    )
    assert approved.status_code == 200

    after = client.get(f"/api/v1/tasks/{task_id}/release-gate").json()
    assert after["allowed"] is True
    assert after["reasons"] == []
