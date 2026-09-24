from pathlib import Path

from fastapi.testclient import TestClient

from onebridge.api import create_app
from onebridge.config import Settings
from onebridge.identity import IdentityStore


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'onebridge.db'}",
        state_root=tmp_path,
        storage_backend="local",
        storage_root=tmp_path / "objects",
        checkpoint_root=tmp_path / "checkpoints",
        audit_log=tmp_path / "audit.jsonl",
        identity_db=tmp_path / "identity.db",
        require_api_key=True,
    )


def test_api_requires_key_and_enforces_tenant(tmp_path: Path):
    config = settings(tmp_path)
    app = create_app(config)
    client = TestClient(app)

    identities = IdentityStore(config.identity_db)
    workspace = identities.create_workspace("Acme")
    _, raw = identities.create_api_key(workspace.id, "test")
    headers = {"Authorization": f"Bearer {raw}"}

    payload = {
        "input": {"goal": "Build page", "required_outputs": ["content"]},
        "context": {"tenant_id": workspace.id, "user_id": "u1"},
    }

    assert client.post("/api/v1/tasks", json=payload).status_code == 401

    wrong = dict(payload)
    wrong["context"] = {"tenant_id": "ws_other", "user_id": "u1"}
    assert client.post("/api/v1/tasks", json=wrong, headers=headers).status_code == 403

    response = client.post("/api/v1/tasks", json=payload, headers=headers)
    assert response.status_code == 200
    task_id = response.json()["task_id"]

    assert client.get(f"/api/v1/tasks/{task_id}", headers=headers).status_code == 200
