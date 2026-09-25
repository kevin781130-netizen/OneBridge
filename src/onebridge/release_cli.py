from __future__ import annotations

import argparse
import json
import socket

from .compatibility_service import CompatibilityService
from .config import Settings
from .deployment_actuator import HttpDeploymentActuator
from .deployment_switch import DeploymentSwitchService, probe_deployment
from .production_release import ProductionReleaseController
from .release_operator import ProductionReleaseOperator
from .runtime import build_runtime_service


def _adapter_ids(raw: str) -> list[str]:
    return [
        item.strip()
        for item in str(raw).split(",")
        if item.strip()
    ]


def _build(settings: Settings):
    settings.require_qualified_adapters = False
    service = build_runtime_service(settings)
    compatibility = CompatibilityService(
        service.db,
        service.registry,
    )

    values = (
        settings.openclaw_actuator_url,
        settings.openclaw_actuator_secret,
    )
    if any(values) and not all(values):
        raise ValueError(
            "OpenClaw actuator configuration is incomplete"
        )

    actuator = None
    if all(values):
        actuator = HttpDeploymentActuator(
            url=str(settings.openclaw_actuator_url),
            shared_secret=str(settings.openclaw_actuator_secret),
            timeout_seconds=settings.openclaw_actuator_timeout_seconds,
        )

    deployments = DeploymentSwitchService(
        service.db,
        actuator=actuator,
        health_max_age_seconds=settings.openclaw_health_max_age_seconds,
    )
    controller = ProductionReleaseController(
        service.db,
        compatibility,
        deployments,
        smoke_url=settings.openclaw_smoke_url,
        timeout_seconds=settings.openclaw_smoke_timeout_seconds,
    )
    operator = ProductionReleaseOperator(
        service.db,
        controller,
        lease_max_age_seconds=settings.release_lease_ttl_seconds,
        approval_max_age_seconds=settings.release_approval_ttl_seconds,
        two_person_required=settings.release_two_person_required,
    )
    return service, deployments, controller, operator


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onebridge-release")
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan")
    plan.add_argument("--slot", required=True, choices=["blue", "green"])
    plan.add_argument(
        "--adapters",
        default="flowise,open_design,hermes",
    )

    approve = sub.add_parser("approve")
    approve.add_argument("--slot", required=True, choices=["blue", "green"])
    approve.add_argument(
        "--adapters",
        default="flowise,open_design,hermes",
    )
    approve.add_argument("--actor", required=True)
    approve.add_argument(
        "--decision",
        choices=["approve", "reject"],
        default="approve",
    )
    approve.add_argument("--reason", default="")

    execute = sub.add_parser("execute")
    execute.add_argument("--approval-id", required=True)
    execute.add_argument(
        "--owner",
        default=socket.gethostname(),
    )

    revoke = sub.add_parser("revoke")
    revoke.add_argument("--approval-id", required=True)
    revoke.add_argument("--actor", required=True)
    revoke.add_argument("--reason", default="")

    status = sub.add_parser("status")
    status.add_argument("--release-id")
    status.add_argument("--approval-id")

    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument(
        "--smoke-url",
        default=None,
    )

    args = parser.parse_args(argv)
    settings = Settings()

    try:
        service, deployments, controller, operator = _build(
            settings
        )

        if args.command == "plan":
            result = operator.plan(
                args.slot,
                _adapter_ids(args.adapters),
            ).to_dict()
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0

        if args.command == "approve":
            plan_value = operator.plan(
                args.slot,
                _adapter_ids(args.adapters),
            )
            result = operator.approve(
                plan_value,
                actor=args.actor,
                decision=args.decision,
                reason=args.reason,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if args.decision == "approve" else 3

        if args.command == "execute":
            result = operator.execute(
                args.approval_id,
                owner=args.owner,
            )
            audit = getattr(service, "audit", None)
            if audit is not None:
                audit.append(
                    "production_release.executed",
                    actor=args.owner,
                    payload={
                        "release_id": result.get("release_id"),
                        "approval_id": args.approval_id,
                        "status": result.get("status"),
                    },
                )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result.get("status") == "succeeded" else 6

        if args.command == "revoke":
            result = operator.revoke(
                args.approval_id,
                actor=args.actor,
                reason=args.reason,
            )
            audit = getattr(service, "audit", None)
            if audit is not None:
                audit.append(
                    "production_release.approval_revoked",
                    actor=args.actor,
                    payload={
                        "approval_id": args.approval_id,
                        "target_slot": result["target_slot"],
                    },
                )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0

        if args.command == "status":
            if args.approval_id:
                result = operator.approval(args.approval_id)
            elif args.release_id:
                result = controller.get(args.release_id)
            else:
                result = {
                    "lease": operator.lease(),
                    "approvals": operator.approvals(),
                    "releases": controller.list(),
                    "deployment_actions": deployments.actions(
                        "openclaw"
                    ),
                }
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0

        if args.command == "reconcile":
            smoke_url = str(
                settings.openclaw_smoke_url or ""
            ).strip()
            if not smoke_url:
                raise ValueError(
                    "reconcile requires the configured stable smoke URL"
                )
            requested_url = str(
                args.smoke_url or ""
            ).strip()
            if requested_url and requested_url != smoke_url:
                raise ValueError(
                    "reconcile smoke URL must match configured stable path"
                )
            observed = probe_deployment(
                smoke_url,
                timeout_seconds=settings.openclaw_smoke_timeout_seconds,
            )
            if not observed.healthy:
                raise ValueError(
                    "reconcile smoke probe is unhealthy"
                )
            if not observed.reported_active_slot:
                raise ValueError(
                    "reconcile smoke response must report active_slot"
                )
            result = controller.reconcile(
                observed_slot=observed.reported_active_slot,
                observed_version=observed.reported_version,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
    except (KeyError, RuntimeError, ValueError) as exc:
        print(json.dumps({
            "status": "rejected",
            "error": str(exc),
        }, indent=2, sort_keys=True))
        return 5

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
