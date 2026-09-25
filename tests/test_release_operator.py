from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from onebridge.adapters.base import AdapterRegistry
from onebridge.db import Database, ReleaseApprovalRecord, ReleaseLeaseRecord
from onebridge.deployment_switch import DeploymentSwitchService
from onebridge.release_operator import ProductionReleaseOperator


class Adapter:
    def __init__(self, name: str, version: str):
        self.name = name
        self.version = version

    def health(self):
        from onebridge.adapters.base import AdapterHealth
        return AdapterHealth("healthy", "ready")

    def capabilities(self):
        return ["content"]

    def execute(self, request):
        raise NotImplementedError

    def cancel(self, external_task_id: str):
        return None


class Compatibility:
    def __init__(self, registry):
        self.registry = registry


class Controller:
    def __init__(self, db, registry):
        self.compatibility = Compatibility(registry)
        self.deployments = DeploymentSwitchService(db)
        self.calls = []

    def run(self, slot, adapters):
        self.calls.append((slot, tuple(adapters)))
        return {
            "release_id": "release-core",
            "target_slot": slot,
            "status": "succeeded",
        }


def build(tmp_path: Path):
    db = Database(f"sqlite:///{tmp_path / 'operator.db'}")
    db.create_all()
    registry = AdapterRegistry()
    flowise = Adapter("flowise", "1.0")
    design = Adapter("open_design", "2.0")
    registry.register(flowise)
    registry.register(design)
    controller = Controller(db, registry)
    controller.deployments.register(
        "openclaw",
        "green",
        endpoint="http://127.0.0.1:4102/health",
        version="2026.9.6",
    )
    return db, controller, flowise


def test_approved_plan_executes_once_and_consumes_approval(tmp_path: Path):
    db, controller, _ = build(tmp_path)
    operator = ProductionReleaseOperator(db, controller)

    plan = operator.plan("green", ["flowise", "open_design"])
    approval = operator.approve(
        plan,
        actor="release-manager",
        reason="qualified change",
    )
    result = operator.execute(
        approval["approval_id"],
        owner="ci-release",
    )

    assert result["status"] == "succeeded"
    assert result["approval_id"] == approval["approval_id"]
    assert controller.calls == [
        ("green", ("flowise", "open_design"))
    ]
    assert operator.approval(
        approval["approval_id"]
    )["consumed_at"] is not None
    assert operator.lease() is None

    with pytest.raises(ValueError):
        operator.execute(approval["approval_id"])


def test_stale_approval_is_rejected_when_adapter_version_changes(tmp_path: Path):
    db, controller, flowise = build(tmp_path)
    operator = ProductionReleaseOperator(db, controller)
    plan = operator.plan("green", ["flowise"])
    approval = operator.approve(plan, actor="release-manager")

    flowise.version = "1.1"

    with pytest.raises(ValueError, match="stale"):
        operator.execute(approval["approval_id"])
    assert controller.calls == []


def test_fresh_release_lease_blocks_second_release_without_consuming_approval(
    tmp_path: Path,
):
    db, controller, _ = build(tmp_path)
    operator = ProductionReleaseOperator(db, controller)
    plan = operator.plan("green", ["flowise"])
    approval = operator.approve(plan, actor="release-manager")

    with db.Session() as session:
        session.add(
            ReleaseLeaseRecord(
                service="openclaw",
                release_id="other-release",
                owner="other-worker",
            )
        )
        session.commit()

    with pytest.raises(RuntimeError, match="busy"):
        operator.execute(approval["approval_id"])

    assert operator.approval(
        approval["approval_id"]
    )["consumed_at"] is None


def test_approval_expires_fail_closed(tmp_path: Path):
    db, controller, _ = build(tmp_path)
    operator = ProductionReleaseOperator(
        db,
        controller,
        approval_max_age_seconds=60,
    )
    plan = operator.plan("green", ["flowise"])
    approval = operator.approve(plan, actor="release-manager")

    with db.Session() as session:
        row = session.get(
            ReleaseApprovalRecord,
            approval["approval_id"],
        )
        row.created_at = (
            datetime.now(timezone.utc) - timedelta(minutes=10)
        )
        session.commit()

    with pytest.raises(ValueError, match="expired"):
        operator.execute(approval["approval_id"])
    assert controller.calls == []


def test_approval_binds_target_endpoint_even_when_version_is_same(
    tmp_path: Path,
):
    db, controller, _ = build(tmp_path)
    operator = ProductionReleaseOperator(db, controller)
    plan = operator.plan("green", ["flowise"])
    approval = operator.approve(plan, actor="release-manager")

    controller.deployments.register(
        "openclaw",
        "green",
        endpoint="http://127.0.0.1:4112/health",
        version="2026.9.6",
    )

    with pytest.raises(ValueError, match="stale"):
        operator.execute(approval["approval_id"])
    assert controller.calls == []
