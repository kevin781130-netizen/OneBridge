from pathlib import Path

import pytest

from onebridge.db import Database
from onebridge.deployment_switch import DeploymentSwitchService
from onebridge.release_operator import ProductionReleaseOperator


class Adapter:
    def __init__(self, name: str, version: str):
        self.name = name
        self.version = version


class Registry:
    def __init__(self):
        self.values = {
            "flowise": Adapter("flowise", "1.0"),
            "open_design": Adapter("open_design", "2.0"),
            "hermes": Adapter("hermes", "3.0"),
        }

    def get(self, adapter_id: str):
        if adapter_id not in self.values:
            raise KeyError(adapter_id)
        return self.values[adapter_id]


class Compatibility:
    def __init__(self):
        self.registry = Registry()

    def get(self, adapter_id: str, version: str):
        raise KeyError((adapter_id, version))


class Controller:
    smoke_url = None

    def __init__(self, db: Database):
        self.compatibility = Compatibility()
        self.deployments = DeploymentSwitchService(db)
        self.calls = []

    def run(self, slot, adapters, *, governance=None):
        self.calls.append((slot, tuple(adapters)))
        return {
            "release_id": "rel_core",
            "target_slot": slot,
            "status": "succeeded",
        }


def build(tmp_path: Path):
    db = Database(f"sqlite:///{tmp_path / 'two-person.db'}")
    db.create_all()
    controller = Controller(db)
    controller.deployments.register(
        "openclaw",
        "green",
        endpoint="http://127.0.0.1:4102/health",
        version="new",
    )
    operator = ProductionReleaseOperator(
        db,
        controller,
        two_person_required=True,
    )
    return operator, controller


def test_requester_cannot_approve_own_release(tmp_path: Path):
    operator, _ = build(tmp_path)
    plan = operator.plan("green", ["flowise"])
    request_value = operator.request(
        plan,
        actor="requester",
    )

    with pytest.raises(ValueError, match="different identities"):
        operator.approve_request(
            request_value["request_id"],
            actor="requester",
        )

    assert operator.request_status(
        request_value["request_id"]
    )["status"] == "pending"


def test_two_person_request_approval_and_execution_roles(tmp_path: Path):
    operator, controller = build(tmp_path)
    plan = operator.plan(
        "green",
        ["flowise", "open_design", "hermes"],
    )
    request_value = operator.request(
        plan,
        actor="requester",
        reason="scheduled change",
    )
    approval = operator.approve_request(
        request_value["request_id"],
        actor="approver",
    )

    assert approval["requested_by"] == "requester"
    assert approval["actor"] == "approver"
    assert operator.request_status(
        request_value["request_id"]
    )["status"] == "approved"

    with pytest.raises(ValueError, match="cannot execute"):
        operator.execute(
            approval["approval_id"],
            owner="approver",
        )

    result = operator.execute(
        approval["approval_id"],
        owner="requester",
    )
    assert result["status"] == "succeeded"
    assert operator.request_status(
        request_value["request_id"]
    )["status"] == "completed"
    assert controller.calls == [
        (
            "green",
            ("flowise", "open_design", "hermes"),
        )
    ]


def test_direct_approval_is_disabled_when_two_person_is_required(
    tmp_path: Path,
):
    operator, _ = build(tmp_path)
    plan = operator.plan("green", ["flowise"])

    with pytest.raises(ValueError, match="persisted release request"):
        operator.approve(
            plan,
            actor="approver",
        )


def test_release_request_can_only_be_decided_once(tmp_path: Path):
    operator, _ = build(tmp_path)
    plan = operator.plan("green", ["flowise"])
    request_value = operator.request(
        plan,
        actor="requester",
    )

    first = operator.approve_request(
        request_value["request_id"],
        actor="approver-a",
    )
    assert first["decision"] == "approve"

    with pytest.raises(ValueError, match="not pending"):
        operator.approve_request(
            request_value["request_id"],
            actor="approver-b",
        )

    approvals = operator.approvals()
    assert len(approvals) == 1
    assert approvals[0]["approval_id"] == first["approval_id"]


def test_revoking_approval_revokes_linked_release_request(tmp_path: Path):
    operator, _ = build(tmp_path)
    plan = operator.plan("green", ["flowise"])
    request_value = operator.request(
        plan,
        actor="requester",
    )
    approval = operator.approve_request(
        request_value["request_id"],
        actor="approver",
    )

    operator.revoke(
        approval["approval_id"],
        actor="security-admin",
        reason="hold",
    )

    assert operator.request_status(
        request_value["request_id"]
    )["status"] == "revoked"
