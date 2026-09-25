from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .deployment_switch import DeploymentSwitchService, probe_deployment


def build_release_router(
    *,
    operator,
    controller,
    deployments: DeploymentSwitchService,
    current_auth,
    audit=None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/releases/production",
        tags=["production-release"],
    )

    def actor(auth, payload: dict) -> str:
        if auth is not None:
            return str(auth.workspace_id)
        value = str(payload.get("actor") or "").strip()
        if not value:
            raise HTTPException(
                status_code=400,
                detail="actor is required when API authentication is disabled",
            )
        return value

    @router.post("/plan")
    def plan_release(
        payload: dict,
        auth=Depends(current_auth),
    ) -> dict:
        adapters = payload.get(
            "adapters",
            ["flowise", "open_design", "hermes"],
        )
        if not isinstance(adapters, list) or not all(
            isinstance(item, str)
            for item in adapters
        ):
            raise HTTPException(
                status_code=400,
                detail="adapters must be a string array",
            )
        try:
            plan = operator.plan(
                str(payload.get("target_slot") or ""),
                adapters,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="deployment or adapter not found",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc
        return plan.to_dict()

    @router.post("/approve")
    def approve_release(
        payload: dict,
        auth=Depends(current_auth),
    ) -> dict:
        adapters = payload.get(
            "adapters",
            ["flowise", "open_design", "hermes"],
        )
        if not isinstance(adapters, list) or not all(
            isinstance(item, str)
            for item in adapters
        ):
            raise HTTPException(
                status_code=400,
                detail="adapters must be a string array",
            )
        try:
            plan = operator.plan(
                str(payload.get("target_slot") or ""),
                adapters,
            )
            result = operator.approve(
                plan,
                actor=actor(auth, payload),
                decision=str(
                    payload.get("decision") or "approve"
                ),
                reason=str(payload.get("reason") or ""),
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="deployment or adapter not found",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc
        if audit is not None:
            audit.append(
                "production_release.approval",
                actor=result["actor"],
                payload={
                    "approval_id": result["approval_id"],
                    "decision": result["decision"],
                    "target_slot": result["target_slot"],
                    "fingerprint": result["fingerprint"],
                },
            )
        return result

    @router.post("/execute")
    def execute_release(
        payload: dict,
        auth=Depends(current_auth),
    ) -> dict:
        approval_id = str(
            payload.get("approval_id") or ""
        ).strip()
        if not approval_id:
            raise HTTPException(
                status_code=400,
                detail="approval_id is required",
            )
        owner = (
            str(auth.workspace_id)
            if auth is not None
            else str(payload.get("owner") or "local-operator")
        )
        try:
            result = operator.execute(
                approval_id,
                owner=owner,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="release approval not found",
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=423,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc
        if audit is not None:
            audit.append(
                "production_release.executed",
                actor=owner,
                payload={
                    "approval_id": approval_id,
                    "release_id": result.get("release_id"),
                    "status": result.get("status"),
                },
            )
        return result

    @router.get("")
    def list_releases(
        auth=Depends(current_auth),
    ) -> list[dict]:
        return controller.list()

    @router.get("/status")
    def operator_status(
        auth=Depends(current_auth),
    ) -> dict:
        return {
            "lease": operator.lease(),
            "releases": controller.list(),
            "deployment_actions": deployments.actions(
                "openclaw"
            ),
        }

    @router.get("/{release_id}")
    def release_status(
        release_id: str,
        auth=Depends(current_auth),
    ) -> dict:
        try:
            return controller.get(release_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="release not found",
            ) from exc

    @router.post("/actions/reconcile")
    def reconcile_release(
        payload: dict,
        auth=Depends(current_auth),
    ) -> dict:
        smoke_url = str(
            payload.get("smoke_url")
            or controller.smoke_url
            or ""
        ).strip()
        if not smoke_url:
            raise HTTPException(
                status_code=409,
                detail="stable smoke URL is required",
            )
        try:
            observed = probe_deployment(
                smoke_url,
                timeout_seconds=controller.timeout_seconds,
            )
            if not observed.healthy:
                raise ValueError(
                    "reconcile smoke probe is unhealthy"
                )
            if not observed.reported_active_slot:
                raise ValueError(
                    "reconcile smoke response must report active_slot"
                )
            active = deployments.reconcile(
                "openclaw",
                observed_slot=observed.reported_active_slot,
                observed_version=observed.reported_version,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="observed deployment slot not found",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc
        result = DeploymentSwitchService.as_dict(active)
        if audit is not None:
            audit.append(
                "production_release.reconciled",
                actor=(
                    str(auth.workspace_id)
                    if auth is not None
                    else str(
                        payload.get("actor")
                        or "local-operator"
                    )
                ),
                payload={
                    "slot": active.slot,
                    "version": active.version,
                },
            )
        return result

    return router
