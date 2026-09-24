from pathlib import Path

import pytest

from onebridge.api import build_service
from onebridge.config import Settings


def test_production_gate_rejects_mock_adapters(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        state_root=tmp_path,
        storage_root=tmp_path / "objects",
        checkpoint_root=tmp_path / "checkpoints",
        audit_log=tmp_path / "audit.jsonl",
        identity_db=tmp_path / "identity.db",
        require_qualified_adapters=True,
    )
    with pytest.raises(RuntimeError) as error:
        build_service(settings)
    assert "mock_adapter_not_allowed" in str(error.value)
