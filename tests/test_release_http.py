from fastapi import FastAPI
from fastapi.testclient import TestClient

from onebridge.deployment_switch import DeploymentStatus, ProbeResult
from onebridge.release_http import build_release_router


class Plan:
    fingerprint = "f" * 64

    def to_dict(self):
        return {
            "service": "openclaw",
            "target_slot": "green",
            "target_version": "new",
            "fingerprint": self.fingerprint,
        }


class Operator:
    def plan(self, slot, adapters):
        return Plan()

    def approve(self, plan, *, actor, decision, reason):
        return {
            "approval_id": "relapp_1",
            "service": "openclaw",
            "target_slot": "green",
            "fingerprint": plan.fingerprint,
            "decision": decision,
            "actor": actor,
            "reason": reason,
            "consumed_at": None,
            "created_at": "2026-09-25T00:00:00+00:00",
        }

    def execute(self, approval_id, *, owner):
        return {
            "release_id": "rel_1",
            "status": "succeeded",
            "approval_id": approval_id,
            "owner": owner,
        }

    def approval(self, approval_id):
        return {
            "approval_id": approval_id,
            "decision": "approve",
            "actor": "release-manager",
        }

    def approvals(self):
        return [self.approval("relapp_1")]

    def revoke(self, approval_id, *, actor, reason):
        return {
            "approval_id": approval_id,
            "target_slot": "green",
            "decision": "revoked",
            "actor": "original-approver",
            "revoked_by": actor,
            "reason": reason,
        }

    def lease(self):
        return None


class Controller:
    smoke_url = "https://traffic.example.test/health"
    timeout_seconds = 5.0

    def __init__(self, deployments):
        self.deployments = deployments

    def list(self):
        return [{"release_id": "rel_1", "status": "succeeded"}]

    def get(self, release_id):
        return {"release_id": release_id, "status": "succeeded"}

    def reconcile(self, *, observed_slot, observed_version):
        active = self.deployments.reconcile(
            "openclaw",
            observed_slot=observed_slot,
            observed_version=observed_version,
        )
        return {
            "deployment": {
                "slot": active.slot,
                "version": active.version,
                "state": active.state,
            },
            "release_id": None,
            "outcome": "deployment_only",
        }


class Deployments:
    def __init__(self):
        self.observed = None

    def actions(self, service):
        return []

    def reconcile(self, service, *, observed_slot, observed_version):
        self.observed = (observed_slot, observed_version)
        return DeploymentStatus(
            service=service,
            slot=observed_slot,
            endpoint="https://green.example.test/health",
            version=observed_version or "new",
            state="active",
            health_status="healthy",
            health_evidence={},
            promoted_at=None,
        )


def client(monkeypatch):
    deployments = Deployments()
    monkeypatch.setattr(
        "onebridge.release_http.probe_deployment",
        lambda url, timeout_seconds=5.0: ProbeResult(
            healthy=True,
            status_code=200,
            latency_ms=1,
            content_type="application/json",
            body_sha256="a" * 64,
            reported_ok=True,
            reported_active_slot="green",
            reported_version="new",
        ),
    )

    def auth():
        return None

    app = FastAPI()
    app.include_router(build_release_router(
        operator=Operator(),
        controller=Controller(deployments),
        deployments=deployments,
        current_auth=auth,
    ))
    return TestClient(app), deployments


def test_approval_requires_actor_when_auth_is_off(monkeypatch):
    c, _ = client(monkeypatch)
    response = c.post(
        "/api/v1/releases/production/approve",
        json={"target_slot": "green"},
    )
    assert response.status_code == 400


def test_approve_execute_and_reconcile(monkeypatch):
    c, deployments = client(monkeypatch)

    approved = c.post(
        "/api/v1/releases/production/approve",
        json={
            "target_slot": "green",
            "actor": "release-manager",
        },
    )
    assert approved.status_code == 200

    executed = c.post(
        "/api/v1/releases/production/execute",
        json={"approval_id": "relapp_1"},
    )
    assert executed.status_code == 200
    assert executed.json()["status"] == "succeeded"

    bad_url = c.post(
        "/api/v1/releases/production/actions/reconcile",
        json={"smoke_url": "https://other.example.test/health"},
    )
    assert bad_url.status_code == 409

    reconciled = c.post(
        "/api/v1/releases/production/actions/reconcile",
        json={},
    )
    assert reconciled.status_code == 200
    assert deployments.observed == ("green", "new")


def test_release_approval_history_and_revoke(monkeypatch):
    c, _ = client(monkeypatch)

    history = c.get(
        "/api/v1/releases/production/approvals"
    )
    assert history.status_code == 200
    assert history.json()[0]["approval_id"] == "relapp_1"

    revoked = c.post(
        "/api/v1/releases/production/approvals/relapp_1/revoke",
        json={
            "actor": "release-manager",
            "reason": "hold",
        },
    )
    assert revoked.status_code == 200
    assert revoked.json()["decision"] == "revoked"
