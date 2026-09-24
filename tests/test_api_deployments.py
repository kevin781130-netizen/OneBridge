from pathlib import Path

from fastapi.testclient import TestClient

from onebridge.api import create_app
from onebridge.config import Settings
from onebridge.deployment_switch import ProbeResult


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        state_root=tmp_path,
        storage_root=tmp_path / "objects",
        checkpoint_root=tmp_path / "checkpoints",
        audit_log=tmp_path / "audit.jsonl",
        identity_db=tmp_path / "identity.db",
    )


def probe_result() -> ProbeResult:
    return ProbeResult(
        healthy=True,
        status_code=200,
        latency_ms=5,
        content_type="application/json",
        body_sha256="b" * 64,
        reported_ok=True,
    )


def test_openclaw_deployment_can_probe_promote_and_rollback(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(
        "onebridge.deployment_switch.probe_deployment",
        lambda endpoint, timeout_seconds=5.0: probe_result(),
    )
    client = TestClient(create_app(settings(tmp_path)))

    blue = client.post(
        "/api/v1/deployments/openclaw/blue",
        json={
            "endpoint": "http://127.0.0.1:4101/health",
            "version": "2026.9.1",
        },
    )
    assert blue.status_code == 200

    assert client.post(
        "/api/v1/deployments/openclaw/blue/probe"
    ).status_code == 200
    promoted = client.post(
        "/api/v1/deployments/openclaw/blue/promote"
    )
    assert promoted.status_code == 200
    assert promoted.json()["state"] == "active"

    client.post(
        "/api/v1/deployments/openclaw/green",
        json={
            "endpoint": "http://127.0.0.1:4102/health",
            "version": "2026.9.2",
        },
    )
    client.post("/api/v1/deployments/openclaw/green/probe")
    green = client.post(
        "/api/v1/deployments/openclaw/green/promote"
    )
    assert green.json()["state"] == "active"

    rollback = client.post(
        "/api/v1/deployments/openclaw/actions/rollback"
    )
    assert rollback.status_code == 200
    assert rollback.json()["slot"] == "blue"

    matrix = client.get("/api/v1/deployments/openclaw")
    assert matrix.status_code == 200
    assert len(matrix.json()) == 2
