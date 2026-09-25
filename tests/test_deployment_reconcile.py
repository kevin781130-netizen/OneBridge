from pathlib import Path

from onebridge.db import Database, DeploymentActionRecord
from onebridge.deployment_switch import DeploymentSwitchService, ProbeResult


def healthy():
    return ProbeResult(
        healthy=True,
        status_code=200,
        latency_ms=1,
        content_type="application/json",
        body_sha256="a" * 64,
        reported_ok=True,
    )


def test_reconcile_updates_registry_to_observed_slot(tmp_path: Path):
    db = Database(f"sqlite:///{tmp_path / 'reconcile.db'}")
    db.create_all()
    service = DeploymentSwitchService(db)

    service.register(
        "openclaw",
        "blue",
        endpoint="http://127.0.0.1:4101/health",
        version="old",
    )
    service.record_health("openclaw", "blue", healthy())
    service.promote("openclaw", "blue")

    service.register(
        "openclaw",
        "green",
        endpoint="http://127.0.0.1:4102/health",
        version="new",
    )

    with db.Session() as session:
        session.add(
            DeploymentActionRecord(
                id="depact_reconcile",
                service="openclaw",
                action="promote",
                from_slot="blue",
                to_slot="green",
                version="new",
                status="reconcile_required",
                evidence_json="{}",
                error="registry commit failed",
            )
        )
        session.commit()

    active = service.reconcile(
        "openclaw",
        observed_slot="green",
        observed_version="new",
    )

    assert active.slot == "green"
    assert active.state == "active"
    assert service.get("openclaw", "blue").state == "standby"
    actions = service.actions("openclaw")
    assert actions[0]["status"] == "reconciled"
    assert actions[0]["evidence"]["reconciliation"]["observed_slot"] == "green"
