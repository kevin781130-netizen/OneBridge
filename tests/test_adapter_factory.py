from pathlib import Path

import pytest

from onebridge.adapters.factory import build_adapter_registry
from onebridge.adapters.flowise import FlowisePredictionAdapter
from onebridge.config import Settings


def base_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        state_root=tmp_path,
        storage_root=tmp_path / "objects",
        checkpoint_root=tmp_path / "checkpoints",
        audit_log=tmp_path / "audit.jsonl",
        identity_db=tmp_path / "identity.db",
    )


def test_factory_uses_real_flowise_when_fully_configured(tmp_path: Path):
    settings = base_settings(tmp_path)
    settings.flowise_base_url = "http://127.0.0.1:3000"
    settings.flowise_chatflow_id = "flow_123"

    registry = build_adapter_registry(settings)
    assert isinstance(registry.get("flowise"), FlowisePredictionAdapter)
    assert registry.names() == ["flowise", "hermes", "open_design"]


def test_factory_fails_closed_on_partial_flowise_config(tmp_path: Path):
    settings = base_settings(tmp_path)
    settings.flowise_base_url = "http://127.0.0.1:3000"
    settings.flowise_chatflow_id = None

    with pytest.raises(ValueError):
        build_adapter_registry(settings)
