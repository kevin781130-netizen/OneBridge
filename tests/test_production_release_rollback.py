from pathlib import Path

from onebridge.adapters.base import (
    AdapterHealth, AdapterOutput, AdapterRequest, AdapterResult, AdapterRegistry,
)
from onebridge.compatibility_service import CompatibilityService
from onebridge.db import Database
from onebridge.deployment_switch import DeploymentSwitchService, ProbeResult
from onebridge.production_release import ProductionReleaseController


class Adapter:
    def __init__(self, name, kind):
        self.name = name
        self.version = "1"
        self.kind = kind

    def health(self):
        return AdapterHealth("healthy", "ready")

    def capabilities(self):
        return [self.kind]

    def execute(self, request: AdapterRequest):
        media = "application/json" if self.kind == "content" else "text/plain"
        return AdapterResult(
            external_task_id="ext",
            outputs=[AdapterOutput(
                kind=self.kind,
                media_type=media,
                content=b'{"ok":true}' if media == "application/json" else b"ok",
                filename="result.json" if media == "application/json" else "result.txt",
            )],
        )

    def cancel(self, external_task_id: str):
        return None


def probe(ok=True):
    return ProbeResult(
        healthy=ok,
        status_code=200 if ok else 503,
        latency_ms=1,
        content_type="application/json",
        body_sha256=("a" if ok else "b") * 64,
        reported_ok=ok,
    )


def test_smoke_failure_rolls_back_before_adapter_promotion(tmp_path: Path, monkeypatch):
    db = Database(f"sqlite:///{tmp_path / 'release.db'}")
    db.create_all()
    registry = AdapterRegistry()
    registry.register(Adapter("flowise", "content"))
    registry.register(Adapter("open_design", "design"))
    compatibility = CompatibilityService(db, registry)
    deployments = DeploymentSwitchService(db)

    deployments.register(
        "openclaw", "blue",
        endpoint="http://127.0.0.1:4101/health",
        version="old",
    )
    deployments.record_health("openclaw", "blue", probe())
    deployments.promote("openclaw", "blue")
    deployments.register(
        "openclaw", "green",
        endpoint="http://127.0.0.1:4102/health",
        version="new",
    )
    monkeypatch.setattr(
        "onebridge.deployment_switch.probe_deployment",
        lambda endpoint, timeout_seconds=5.0: probe(),
    )

    controller = ProductionReleaseController(
        db,
        compatibility,
        deployments,
        smoke_url="http://127.0.0.1:4199/health",
        smoke_probe=lambda endpoint, timeout_seconds=5.0: probe(False),
    )
    result = controller.run(
        "green",
        ["flowise", "open_design"],
    )

    assert result["status"] == "rolled_back"
    assert deployments.get("openclaw", "blue").state == "active"
    assert deployments.get("openclaw", "green").state == "standby"
    assert compatibility.get("flowise", "1").state == "candidate"
    assert compatibility.get("open_design", "1").state == "candidate"


def test_smoke_identity_mismatch_rolls_back(tmp_path: Path, monkeypatch):
    db = Database(f"sqlite:///{tmp_path / 'identity.db'}")
    db.create_all()
    registry = AdapterRegistry()
    registry.register(Adapter("flowise", "content"))
    compatibility = CompatibilityService(db, registry)
    deployments = DeploymentSwitchService(db)

    deployments.register(
        "openclaw", "blue",
        endpoint="http://127.0.0.1:4201/health",
        version="old",
    )
    deployments.record_health("openclaw", "blue", probe())
    deployments.promote("openclaw", "blue")
    deployments.register(
        "openclaw", "green",
        endpoint="http://127.0.0.1:4202/health",
        version="new",
    )
    monkeypatch.setattr(
        "onebridge.deployment_switch.probe_deployment",
        lambda endpoint, timeout_seconds=5.0: probe(),
    )
    wrong = ProbeResult(
        healthy=True,
        status_code=200,
        latency_ms=1,
        content_type="application/json",
        body_sha256="c" * 64,
        reported_ok=True,
        reported_active_slot="blue",
        reported_version="old",
    )
    controller = ProductionReleaseController(
        db,
        compatibility,
        deployments,
        smoke_url="http://127.0.0.1:4299/health",
        smoke_probe=lambda endpoint, timeout_seconds=5.0: wrong,
    )

    result = controller.run("green", ["flowise"])

    assert result["status"] == "rolled_back"
    assert deployments.get("openclaw", "blue").state == "active"
    assert compatibility.get("flowise", "1").state == "candidate"
