from pathlib import Path

import pytest

from onebridge.adapters.base import (
    AdapterHealth,
    AdapterOutput,
    AdapterRequest,
    AdapterResult,
    AdapterRegistry,
)
from onebridge.compatibility_service import CompatibilityService
from onebridge.db import Database


class QualifiedAdapter:
    name = "flowise"
    version = "real-1"

    def health(self):
        return AdapterHealth("healthy", "ready")

    def capabilities(self):
        return ["content"]

    def execute(self, request: AdapterRequest):
        return AdapterResult(
            external_task_id="external-1",
            outputs=[
                AdapterOutput(
                    kind="content",
                    media_type="application/json",
                    content=b'{"ok": true}',
                    filename="content.json",
                )
            ],
        )

    def cancel(self, external_task_id: str):
        return None


class FailingAdapter(QualifiedAdapter):
    version = "real-2"

    def execute(self, request: AdapterRequest):
        raise RuntimeError("upstream failed")


def database(tmp_path: Path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 'compat.db'}")
    db.create_all()
    return db


def test_promotion_requires_passing_qualification(tmp_path: Path):
    registry = AdapterRegistry()
    registry.register(QualifiedAdapter())
    service = CompatibilityService(database(tmp_path), registry)

    candidate = service.register_current("flowise")
    assert candidate.state == "candidate"

    with pytest.raises(ValueError):
        service.promote("flowise", "real-1")

    qualification = service.qualify_current("flowise")
    assert qualification.passed

    promoted = service.promote("flowise", "real-1")
    assert promoted.state == "active"
    assert promoted.latest_qualification_passed is True


def test_failed_qualification_blocks_candidate(tmp_path: Path):
    registry = AdapterRegistry()
    registry.register(FailingAdapter())
    service = CompatibilityService(database(tmp_path), registry)

    result = service.qualify_current("flowise")
    assert not result.passed

    status = service.get("flowise", "real-2")
    assert status.state == "blocked"
    assert status.latest_qualification_passed is False

    with pytest.raises(ValueError):
        service.promote("flowise", "real-2")
