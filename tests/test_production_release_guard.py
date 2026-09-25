from pathlib import Path

import pytest

from onebridge.adapters.base import AdapterRegistry
from onebridge.compatibility_service import CompatibilityService
from onebridge.db import Database
from onebridge.deployment_switch import DeploymentSwitchService
from onebridge.production_release import ProductionReleaseController


class FakeActuator:
    def apply(self, request_value):
        raise AssertionError("actuation should not start")


def test_external_actuation_requires_smoke_url(tmp_path: Path):
    db = Database(f"sqlite:///{tmp_path / 'release.db'}")
    db.create_all()
    compatibility = CompatibilityService(db, AdapterRegistry())
    deployments = DeploymentSwitchService(
        db,
        actuator=FakeActuator(),
    )
    deployments.register(
        "openclaw",
        "green",
        endpoint="http://127.0.0.1:4102/health",
        version="new",
    )

    controller = ProductionReleaseController(
        db,
        compatibility,
        deployments,
    )
    with pytest.raises(ValueError):
        controller.run("green", ["flowise"])
