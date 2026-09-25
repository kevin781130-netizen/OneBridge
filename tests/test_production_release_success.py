from pathlib import Path

from onebridge.adapters.base import (
    AdapterHealth,
    AdapterOutput,
    AdapterRequest,
    AdapterResult,
    AdapterRegistry,
)
from onebridge.compatibility_service import CompatibilityService
from onebridge.db import Database
from onebridge.deployment_switch import DeploymentSwitchService, ProbeResult
from onebridge.production_release import ProductionReleaseController


class GoodAdapter:
    def __init__(self, name, version, kinds):
        self.name = name
        self.version = version
        self.kinds = tuple(kinds)

    def health(self):
        return AdapterHealth("healthy", "ready")

    def capabilities(self):
        return list(self.kinds)

    def execute(self, request: AdapterRequest):
        outputs = []
        for kind in self.kinds:
            media = "application/json" if kind in {"content", "test_report"} else "text/plain"
            outputs.append(AdapterOutput(
                kind=kind,
                media_type=media,
                content=b'{"ok":true}' if media == "application/json" else b"ok",
                filename=kind + (".json" if media == "application/json" else ".txt"),
            ))
        return AdapterResult(external_task_id="ext", outputs=outputs)

    def cancel(self, external_task_id: str):
        return None


def healthy():
    return ProbeResult(
        healthy=True,
        status_code=200,
        latency_ms=2,
        content_type="application/json",
        body_sha256="a" * 64,
        reported_ok=True,
    )


def test_release_switches_then_promotes_adapter_set(tmp_path: Path, monkeypatch):
    db = Database(f"sqlite:///{tmp_path / 'release.db'}")
    db.create_all()
    registry = AdapterRegistry()
    registry.register(GoodAdapter("flowise", "1", ["content"]))
    registry.register(GoodAdapter("open_design", "2", ["design"]))
    registry.register(GoodAdapter("hermes", "3", ["code", "test_report"]))
    compatibility = CompatibilityService(db, registry)
    deployments = DeploymentSwitchService(db)

    deployments.register(
        "openclaw",
        "blue",
        endpoint="http://127.0.0.1:4101/health",
        version="old",
    )
    deployments.record_health("openclaw", "blue", healthy())
    deployments.promote("openclaw", "blue")
    deployments.register(
        "openclaw",
        "green",
        endpoint="http://127.0.0.1:4102/health",
        version="new",
    )
    monkeypatch.setattr(
        "onebridge.deployment_switch.probe_deployment",
        lambda endpoint, timeout_seconds=5.0: healthy(),
    )

    controller = ProductionReleaseController(
        db,
        compatibility,
        deployments,
        smoke_url="http://127.0.0.1:4199/health",
        smoke_probe=lambda endpoint, timeout_seconds=5.0: healthy(),
    )
    result = controller.run(
        "green",
        ["flowise", "open_design", "hermes"],
        governance={
            "request_id": "relreq_test",
            "approval_id": "relapp_test",
            "requested_by": "requester",
            "approved_by": "approver",
            "executed_by": "requester",
        },
    )

    assert result["status"] == "succeeded"
    assert deployments.get("openclaw", "green").state == "active"
    assert deployments.get("openclaw", "blue").state == "standby"
    assert compatibility.get("flowise", "1").state == "active"
    assert compatibility.get("open_design", "2").state == "active"
    assert compatibility.get("hermes", "3").state == "active"


    stored = controller.get(result["release_id"])
    assert stored["evidence"]["governance"]["request_id"] == "relreq_test"
    assert stored["evidence"]["governance"]["approved_by"] == "approver"
