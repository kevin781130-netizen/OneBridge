from pathlib import Path

from fastapi.testclient import TestClient

from onebridge.api import create_app
from onebridge.config import Settings
from onebridge.identity import IdentityStore


def make_settings(
    tmp_path: Path,
    allowed: tuple[str, ...],
) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        state_root=tmp_path,
        storage_root=tmp_path / "objects",
        checkpoint_root=tmp_path / "checkpoints",
        audit_log=tmp_path / "audit.jsonl",
        identity_db=tmp_path / "identity.db",
        require_api_key=True,
        release_admin_workspaces=allowed,
    )


def key_for(settings: Settings, workspace_id: str) -> str:
    identities = IdentityStore(settings.identity_db)
    try:
        identities.create_workspace(
            workspace_id,
            workspace_id=workspace_id,
        )
    except Exception:
        pass
    _, raw = identities.create_api_key(
        workspace_id,
        "release-test",
    )
    return raw


def test_release_api_allows_only_configured_admin_workspace(tmp_path: Path):
    settings = make_settings(tmp_path, ("ws_release",))
    admin_key = key_for(settings, "ws_release")
    tenant_key = key_for(settings, "ws_tenant")
    client = TestClient(create_app(settings))

    allowed = client.get(
        "/api/v1/releases/production/status",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    denied = client.get(
        "/api/v1/releases/production/status",
        headers={"Authorization": f"Bearer {tenant_key}"},
    )

    assert allowed.status_code == 200
    assert denied.status_code == 403


def test_release_api_fails_closed_without_admin_allowlist(tmp_path: Path):
    settings = make_settings(tmp_path, ())
    raw = key_for(settings, "ws_release")
    client = TestClient(create_app(settings))

    response = client.get(
        "/api/v1/releases/production/status",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 403


def test_global_release_mutations_require_release_admin(tmp_path: Path):
    settings = make_settings(tmp_path, ("ws_release",))
    admin_key = key_for(settings, "ws_release")
    tenant_key = key_for(settings, "ws_tenant")
    client = TestClient(create_app(settings))

    denied = client.post(
        "/api/v1/adapters/flowise/compatibility/candidate",
        headers={"Authorization": f"Bearer {tenant_key}"},
    )
    assert denied.status_code == 403

    allowed = client.post(
        "/api/v1/adapters/flowise/compatibility/candidate",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert allowed.status_code == 200

    deployment_denied = client.post(
        "/api/v1/deployments/openclaw/green",
        headers={"Authorization": f"Bearer {tenant_key}"},
        json={
            "endpoint": "http://127.0.0.1:4102/health",
            "version": "candidate",
        },
    )
    assert deployment_denied.status_code == 403


def test_release_admin_gate_protects_global_release_visibility(tmp_path: Path):
    settings = make_settings(tmp_path, ("ws_release",))
    admin_key = key_for(settings, "ws_release")
    tenant_key = key_for(settings, "ws_tenant")
    client = TestClient(create_app(settings))

    denied = client.get(
        "/api/v1/compatibility",
        headers={"Authorization": f"Bearer {tenant_key}"},
    )
    allowed = client.get(
        "/api/v1/compatibility",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    deployments = client.get(
        "/api/v1/deployments/openclaw",
        headers={"Authorization": f"Bearer {tenant_key}"},
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert deployments.status_code == 403
