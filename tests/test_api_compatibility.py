from pathlib import Path

from fastapi.testclient import TestClient

from onebridge.api import create_app
from onebridge.config import Settings


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        state_root=tmp_path,
        storage_root=tmp_path / "objects",
        checkpoint_root=tmp_path / "checkpoints",
        audit_log=tmp_path / "audit.jsonl",
        identity_db=tmp_path / "identity.db",
    )


def test_compatibility_candidate_and_mock_guard(tmp_path: Path):
    client = TestClient(create_app(settings(tmp_path)))

    candidate = client.post(
        "/api/v1/adapters/flowise/compatibility/candidate"
    )
    assert candidate.status_code == 200
    assert candidate.json()["state"] == "candidate"
    assert candidate.json()["version"].startswith("mock-")

    matrix = client.get("/api/v1/compatibility")
    assert matrix.status_code == 200
    assert any(
        item["adapter_id"] == "flowise"
        for item in matrix.json()
    )

    qualification = client.post("/api/v1/adapters/flowise/qualify")
    assert qualification.status_code == 409
    assert "mock adapters cannot be qualified" in qualification.json()["detail"]
