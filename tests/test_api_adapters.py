from pathlib import Path

from fastapi.testclient import TestClient

from onebridge.api import create_app
from onebridge.config import Settings


def test_adapter_status_endpoint(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        state_root=tmp_path,
        storage_root=tmp_path / "objects",
        checkpoint_root=tmp_path / "checkpoints",
        audit_log=tmp_path / "audit.jsonl",
        identity_db=tmp_path / "identity.db",
    )
    client = TestClient(create_app(settings))
    response = client.get("/api/v1/adapters")
    assert response.status_code == 200
    payload = response.json()
    assert [item["name"] for item in payload] == [
        "flowise",
        "hermes",
        "open_design",
    ]
    assert all(item["health"]["status"] == "healthy" for item in payload)
