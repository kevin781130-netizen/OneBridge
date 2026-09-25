from pathlib import Path

from fastapi.testclient import TestClient

from onebridge.api import create_app
from onebridge.config import Settings


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        state_root=tmp_path,
        storage_root=tmp_path / "objects",
        checkpoint_root=tmp_path / "checkpoints",
        audit_log=tmp_path / "audit.jsonl",
        identity_db=tmp_path / "identity.db",
        release_controller_required=True,
    )


def test_direct_promotions_are_blocked(tmp_path: Path):
    client = TestClient(create_app(make_settings(tmp_path)))
    a = client.post(
        "/api/v1/adapters/flowise/compatibility/x/promote"
    )
    d = client.post(
        "/api/v1/deployments/openclaw/green/promote"
    )
    assert a.status_code == 409
    assert d.status_code == 409
