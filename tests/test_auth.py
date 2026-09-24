from pathlib import Path

import pytest

from onebridge.auth import AuthenticationError, authenticate_bearer, require_tenant
from onebridge.identity import IdentityStore


def test_bearer_authentication_and_tenant_scope(tmp_path: Path):
    store = IdentityStore(tmp_path / "identity.db")
    workspace = store.create_workspace("Acme")
    _, raw = store.create_api_key(workspace.id, "test")

    context = authenticate_bearer(f"Bearer {raw}", store)
    assert context.workspace_id == workspace.id
    require_tenant(context, workspace.id)

    with pytest.raises(AuthenticationError):
        require_tenant(context, "ws_other")


def test_revoked_key_is_rejected(tmp_path: Path):
    store = IdentityStore(tmp_path / "identity.db")
    workspace = store.create_workspace("Acme")
    info, raw = store.create_api_key(workspace.id, "test")
    store.revoke_api_key(info.id)

    with pytest.raises(AuthenticationError):
        authenticate_bearer(f"Bearer {raw}", store)
