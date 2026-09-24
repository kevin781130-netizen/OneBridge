from pathlib import Path

import pytest

from onebridge.db import Database
from onebridge.deployment_actuator import DeploymentActuationResult
from onebridge.deployment_switch import (
    DeploymentSwitchService,
    ProbeResult,
)


class FakeActuator:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.requests = []

    def apply(self, request_value):
        self.requests.append(request_value)
        if self.fail:
            raise RuntimeError("switch failed")
        return DeploymentActuationResult(
            applied=True,
            status_code=200,
            latency_ms=3,
            response_sha256="c" * 64,
            reported_active_slot=request_value.to_slot,
        )


def healthy() -> ProbeResult:
    return ProbeResult(
        healthy=True,
        status_code=200,
        latency_ms=5,
        content_type="application/json",
        body_sha256="a" * 64,
        reported_ok=True,
    )


def database(tmp_path: Path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 'deploy.db'}")
    db.create_all()
    return db


def test_successful_actuation_is_journaled_and_committed(tmp_path: Path):
    actuator = FakeActuator()
    service = DeploymentSwitchService(
        database(tmp_path),
        actuator=actuator,
    )
    service.register(
        "openclaw",
        "green",
        endpoint="http://127.0.0.1:4102/health",
        version="2026.9.6",
    )
    service.record_health("openclaw", "green", healthy())

    promoted = service.promote("openclaw", "green")

    assert promoted.state == "active"
    assert actuator.requests[0].to_slot == "green"
    actions = service.actions("openclaw")
    assert actions[0]["status"] == "committed"
    assert actions[0]["evidence"]["reported_active_slot"] == "green"


def test_failed_actuator_does_not_change_registry_active_state(tmp_path: Path):
    actuator = FakeActuator(fail=True)
    service = DeploymentSwitchService(
        database(tmp_path),
        actuator=actuator,
    )
    service.register(
        "openclaw",
        "green",
        endpoint="http://127.0.0.1:4102/health",
        version="2026.9.6",
    )
    service.record_health("openclaw", "green", healthy())

    with pytest.raises(RuntimeError):
        service.promote("openclaw", "green")

    assert service.get("openclaw", "green").state == "candidate"
    actions = service.actions("openclaw")
    assert actions[0]["status"] == "failed"
