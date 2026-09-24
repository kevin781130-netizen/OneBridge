from pathlib import Path

from onebridge.adapters.factory import build_adapter_registry
from onebridge.adapters.hermes import HermesCommandAdapter
from onebridge.adapters.open_design import OpenDesignMCPAdapter
from onebridge.artifacts import LocalObjectStore
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


def test_factory_selects_real_open_design(tmp_path: Path):
    config = settings(tmp_path)
    config.open_design_mcp_url = "http://127.0.0.1:9000/mcp"
    registry = build_adapter_registry(
        config,
        store=LocalObjectStore(tmp_path / "objects"),
    )
    assert isinstance(
        registry.get("open_design"),
        OpenDesignMCPAdapter,
    )


def test_factory_selects_real_hermes(tmp_path: Path):
    config = settings(tmp_path)
    config.hermes_executable = "hermes"
    config.hermes_require_strong_sandbox = False
    registry = build_adapter_registry(
        config,
        store=LocalObjectStore(tmp_path / "objects"),
    )
    assert isinstance(
        registry.get("hermes"),
        HermesCommandAdapter,
    )
