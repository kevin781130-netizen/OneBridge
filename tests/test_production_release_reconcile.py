from pathlib import Path

from onebridge.adapters.base import AdapterRegistry
from onebridge.compatibility_service import CompatibilityService
from onebridge.db import (
    Database,
    DeploymentActionRecord,
    ProductionReleaseRecord,
)
from onebridge.deployment_switch import DeploymentSwitchService, ProbeResult
from onebridge.production_release import ProductionReleaseController


def healthy():
    return ProbeResult(
        healthy=True,
        status_code=200,
        latency_ms=1,
        content_type="application/json",
        body_sha256="a" * 64,
        reported_ok=True,
    )


def test_reconcile_resolves_release_journal_without_auto_continuation(
    tmp_path: Path,
):
    db = Database(f"sqlite:///{tmp_path / 'release.db'}")
    db.create_all()
    compatibility = CompatibilityService(db, AdapterRegistry())
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

    with db.Session() as session:
        session.add(
            DeploymentActionRecord(
                id="depact_pending",
                service="openclaw",
                action="promote",
                from_slot="blue",
                to_slot="green",
                version="new",
                status="reconcile_required",
                evidence_json="{}",
            )
        )
        session.add(
            ProductionReleaseRecord(
                id="rel_pending",
                service="openclaw",
                target_slot="green",
                previous_slot="blue",
                status="reconcile_required",
                adapters_json="[]",
                evidence_json="{}",
            )
        )
        session.commit()

    controller = ProductionReleaseController(
        db,
        compatibility,
        deployments,
    )
    result = controller.reconcile(
        observed_slot="green",
        observed_version="new",
    )

    assert result["release_id"] == "rel_pending"
    assert result["outcome"] == "target_observed_new_approval_required"
    assert deployments.get("openclaw", "green").state == "active"
    assert controller.get("rel_pending")["status"] == "reconciled"
