from pathlib import Path

from onebridge.config import Settings
from onebridge.production_readiness import (
    evaluate_production_readiness,
)


class Adapter:
    def __init__(self, name: str, version: str):
        self.name = name
        self.version = version


class Registry:
    def __init__(self, mock: bool = False):
        suffix = "mock-1" if mock else "real-1"
        self.values = {
            "flowise": Adapter("flowise", suffix),
            "open_design": Adapter("open_design", suffix),
            "hermes": Adapter("hermes", suffix),
        }

    def get(self, adapter_id: str):
        if adapter_id not in self.values:
            raise KeyError(adapter_id)
        return self.values[adapter_id]


def ready_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="postgresql://onebridge@db/onebridge",
        state_root=tmp_path,
        storage_backend="s3",
        storage_root=tmp_path / "objects",
        checkpoint_root=tmp_path / "checkpoints",
        audit_log=tmp_path / "audit.jsonl",
        identity_db=tmp_path / "identity.db",
        require_api_key=True,
        release_controller_required=True,
        release_two_person_required=True,
        release_admin_workspaces=("ws_release_a", "ws_release_b"),
        openclaw_actuator_url="https://deploy.example.test/switch",
        openclaw_actuator_secret="configured-at-runtime",
        openclaw_smoke_url="https://traffic.example.test/health",
        task_queue_url="postgresql://queue@db/queue",
        flowise_base_url="https://flowise.example.test",
        flowise_chatflow_id="content-flow",
        open_design_mcp_url="https://design.example.test/mcp",
        hermes_executable="/opt/hermes/bin/hermes",
        hermes_require_strong_sandbox=True,
        otel_endpoint="https://otel.example.test/v1/traces",
    )


def test_ready_production_configuration_passes(tmp_path: Path):
    report = evaluate_production_readiness(
        ready_settings(tmp_path),
        Registry(),
    )

    assert report.ready
    assert report.failures == ()


def test_readiness_fails_closed_for_local_or_mock_runtime(tmp_path: Path):
    settings = ready_settings(tmp_path)
    settings.database_url = "sqlite:///./onebridge.db"
    settings.storage_backend = "local"
    settings.release_two_person_required = False
    settings.release_admin_workspaces = ("ws_release_a",)

    report = evaluate_production_readiness(
        settings,
        Registry(mock=True),
    )

    assert not report.ready
    assert "database.postgresql" in report.failures
    assert "storage.remote" in report.failures
    assert "release.two_person" in report.failures
    assert "release.admins" in report.failures
    assert "adapter.flowise" in report.failures


def test_readiness_requires_distinct_release_admin_identities(tmp_path: Path):
    settings = ready_settings(tmp_path)
    settings.release_admin_workspaces = (
        "ws_release_a",
        "ws_release_a",
    )

    report = evaluate_production_readiness(
        settings,
        Registry(),
    )

    assert not report.ready
    assert "release.admins" in report.failures
