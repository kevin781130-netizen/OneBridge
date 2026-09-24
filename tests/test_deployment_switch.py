from pathlib import Path

import pytest

from onebridge.db import Database
from onebridge.deployment_switch import (
    DeploymentSwitchService,
    ProbeResult,
)


def service(tmp_path: Path) -> DeploymentSwitchService:
    db = Database(f"sqlite:///{tmp_path / 'deploy.db'}")
    db.create_all()
    return DeploymentSwitchService(db)


def healthy(status_code: int = 200) -> ProbeResult:
    return ProbeResult(
        healthy=True,
        status_code=status_code,
        latency_ms=12,
        content_type="application/json",
        body_sha256="a" * 64,
        reported_ok=True,
    )


def test_blue_green_promotion_requires_health_evidence(tmp_path: Path):
    deployments = service(tmp_path)
    blue = deployments.register(
        "openclaw",
        "blue",
        endpoint="http://127.0.0.1:4101/health",
        version="2026.9.1",
    )
    assert blue.state == "candidate"
    assert blue.health_status == "unknown"

    with pytest.raises(ValueError):
        deployments.promote("openclaw", "blue")

    deployments.record_health("openclaw", "blue", healthy())
    active = deployments.promote("openclaw", "blue")
    assert active.state == "active"


def test_promote_green_keeps_blue_as_healthy_standby_for_rollback(tmp_path: Path):
    deployments = service(tmp_path)
    deployments.register(
        "openclaw",
        "blue",
        endpoint="http://127.0.0.1:4101/health",
        version="2026.9.1",
    )
    deployments.record_health("openclaw", "blue", healthy())
    deployments.promote("openclaw", "blue")

    deployments.register(
        "openclaw",
        "green",
        endpoint="http://127.0.0.1:4102/health",
        version="2026.9.2",
    )
    deployments.record_health("openclaw", "green", healthy())
    green = deployments.promote("openclaw", "green")

    assert green.state == "active"
    assert deployments.get("openclaw", "blue").state == "standby"

    rolled_back = deployments.rollback("openclaw")
    assert rolled_back.slot == "blue"
    assert rolled_back.state == "active"
    assert deployments.get("openclaw", "green").state == "standby"


def test_active_slot_cannot_be_replaced_in_place(tmp_path: Path):
    deployments = service(tmp_path)
    deployments.register(
        "openclaw",
        "blue",
        endpoint="http://127.0.0.1:4101/health",
        version="2026.9.1",
    )
    deployments.record_health("openclaw", "blue", healthy())
    deployments.promote("openclaw", "blue")

    with pytest.raises(ValueError):
        deployments.register(
            "openclaw",
            "blue",
            endpoint="http://127.0.0.1:4101/health",
            version="2026.9.2",
        )


def test_remote_plain_http_deployment_is_blocked(tmp_path: Path):
    deployments = service(tmp_path)
    with pytest.raises(ValueError):
        deployments.register(
            "openclaw",
            "blue",
            endpoint="http://example.com:4101/health",
            version="2026.9.1",
        )
