from pathlib import Path

from onebridge.identity import IdentityStore


def test_workspace_key_lifecycle(tmp_path: Path):
    store = IdentityStore(tmp_path / "identity.db")
    workspace = store.create_workspace("Acme")
    info, raw = store.create_api_key(workspace.id, "ci")
    assert raw.startswith("ob_")
    assert store.authenticate(raw).id == workspace.id
    store.revoke_api_key(info.id)
    assert store.authenticate(raw) is None
