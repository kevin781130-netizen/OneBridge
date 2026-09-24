from __future__ import annotations

from dataclasses import asdict

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from .adapters.factory import build_adapter_registry
from .artifacts import LocalObjectStore, S3ObjectStore
from .audit import HashChainAuditLog
from .auth import AuthContext, AuthenticationError, authenticate_bearer, require_tenant
from .checkpoints import CheckpointStore
from .compatibility_service import CompatibilityService
from .contextforge import ContextForge
from .config import Settings
from .contracts import ApprovalRequest, TaskContract, TextArtifactRevisionRequest
from .db import Database, ProjectRecord, TaskRecord
from .deployment_switch import DeploymentProbeError, DeploymentSwitchService
from .durable_service import DurableOneBridgeService
from .identity import IdentityStore
from .line_messaging import LineMessagingClient, LineMessagingError, LineWebhookController
from .line_progress import map_task_progress
from .preview import load_artifact_preview
from .release_gate import evaluate_release_gate
from .review import compare_artifacts
from .review_portal import render_review_portal
from .sdk import ContextPluginContext, load_context_plugins
from .service import OneBridgeService
from .telemetry import build_telemetry
from .workers.factory import build_worker_queue
from .workers.task_runner import TaskScheduler


def build_service(settings: Settings | None = None) -> OneBridgeService:
    settings = settings or Settings()
    db = Database(settings.database_url)
    db.create_all()
    if settings.storage_backend.lower() in {"s3", "minio"}:
        store = S3ObjectStore(
            settings.s3_bucket,
            endpoint_url=settings.s3_endpoint,
            region=settings.s3_region,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
        )
    else:
        store = LocalObjectStore(settings.storage_root)

    registry = build_adapter_registry(settings, store=store)
    if settings.require_qualified_adapters:
        CompatibilityService(db, registry).require_active_registry()

    context_forge = ContextForge()
    for scope, provider in load_context_plugins(
        settings.context_plugins,
        context=ContextPluginContext(state_root=settings.state_root),
    ).items():
        context_forge.register(scope, provider)

    telemetry = build_telemetry(
        service_name=settings.otel_service_name,
        endpoint=settings.otel_endpoint,
        headers=settings.otel_headers,
    )

    return DurableOneBridgeService(
        db,
        registry,
        store,
        audit=HashChainAuditLog(settings.audit_log),
        checkpoints=CheckpointStore(settings.checkpoint_root),
        telemetry=telemetry,
        context_forge=context_forge,
        output_routes=settings.output_routes,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    service = build_service(settings)
    identities = IdentityStore(settings.identity_db)
    compatibility = CompatibilityService(service.db, service.registry)
    deployments = DeploymentSwitchService(service.db)
    task_scheduler = None
    if settings.task_queue_url:
        task_scheduler = TaskScheduler(
            build_worker_queue(settings.task_queue_url)
        )

    line_values = (
        settings.line_channel_secret,
        settings.line_channel_access_token,
        settings.line_tenant_id,
    )
    if any(line_values) and not all(line_values):
        raise ValueError(
            "LINE integration requires ONEBRIDGE_LINE_CHANNEL_SECRET, "
            "ONEBRIDGE_LINE_CHANNEL_ACCESS_TOKEN and ONEBRIDGE_LINE_TENANT_ID"
        )
    line_controller = None
    if all(line_values):
        line_controller = LineWebhookController(
            service=service,
            client=LineMessagingClient(
                channel_access_token=str(settings.line_channel_access_token),
            ),
            channel_secret=str(settings.line_channel_secret),
            tenant_id=str(settings.line_tenant_id),
            required_outputs=settings.line_required_outputs,
        )

    app = FastAPI(title="OneBridge Control Plane", version="0.1.0")

    def current_auth(
        authorization: str | None = Header(default=None),
    ) -> AuthContext | None:
        if not settings.require_api_key:
            return None
        try:
            return authenticate_bearer(authorization, identities)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    def enforce_task_scope(task_id: str, auth: AuthContext | None) -> None:
        if auth is None:
            return
        with service.db.Session() as session:
            task = session.get(TaskRecord, task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="task not found")
            project = session.get(ProjectRecord, task.project_id)
            if project is None or project.tenant_id != auth.workspace_id:
                raise HTTPException(status_code=404, detail="task not found")

    @app.get("/review/{task_id}", response_class=HTMLResponse)
    def review_portal(task_id: str) -> HTMLResponse:
        return HTMLResponse(render_review_portal(task_id))

    @app.post("/integrations/line/webhook")
    async def line_webhook(
        request: Request,
        x_line_signature: str | None = Header(
            default=None,
            alias="X-Line-Signature",
        ),
    ) -> dict:
        if line_controller is None:
            raise HTTPException(
                status_code=503,
                detail="LINE integration is not configured",
            )
        raw_body = await request.body()
        try:
            results = line_controller.handle(
                raw_body,
                signature=x_line_signature,
            )
        except LineMessagingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "events": results}

    @app.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "version": "0.1.0",
            "adapters": service.registry.names(),
            "auth_required": settings.require_api_key,
            "task_queue_enabled": task_scheduler is not None,
        }

    @app.get("/api/v1/adapters")
    def adapters(auth: AuthContext | None = Depends(current_auth)) -> list[dict]:
        result = []
        for adapter in service.registry.list():
            health = adapter.health()
            result.append({
                "name": adapter.name,
                "version": adapter.version,
                "capabilities": adapter.capabilities(),
                "health": {
                    "status": health.status,
                    "detail": health.detail,
                },
            })
        return result

    @app.get("/api/v1/compatibility")
    def compatibility_matrix(
        auth: AuthContext | None = Depends(current_auth),
    ) -> list[dict]:
        return [
            CompatibilityService.as_dict(item)
            for item in compatibility.list()
        ]

    @app.post("/api/v1/adapters/{adapter_id}/compatibility/candidate")
    def register_adapter_candidate(
        adapter_id: str,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        try:
            status = compatibility.register_current(adapter_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="adapter not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return CompatibilityService.as_dict(status)

    @app.post("/api/v1/adapters/{adapter_id}/qualify")
    def qualify_adapter_endpoint(
        adapter_id: str,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        try:
            result = compatibility.qualify_current(adapter_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="adapter not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return result.to_dict()

    @app.post("/api/v1/adapters/{adapter_id}/compatibility/{version}/promote")
    def promote_adapter(
        adapter_id: str,
        version: str,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        try:
            status = compatibility.promote(adapter_id, version)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="compatibility entry not found",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return CompatibilityService.as_dict(status)

    @app.post("/api/v1/adapters/{adapter_id}/compatibility/{version}/block")
    def block_adapter(
        adapter_id: str,
        version: str,
        payload: dict | None = None,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        notes = str((payload or {}).get("notes") or "")[:2000]
        try:
            status = compatibility.block(
                adapter_id,
                version,
                notes=notes,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="compatibility entry not found",
            ) from exc
        return CompatibilityService.as_dict(status)

    @app.get("/api/v1/deployments/openclaw")
    def openclaw_deployments(
        auth: AuthContext | None = Depends(current_auth),
    ) -> list[dict]:
        return [
            DeploymentSwitchService.as_dict(item)
            for item in deployments.list("openclaw")
        ]

    @app.post("/api/v1/deployments/openclaw/{slot}")
    def register_openclaw_deployment(
        slot: str,
        payload: dict,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        try:
            status = deployments.register(
                "openclaw",
                slot,
                endpoint=str(payload.get("endpoint") or ""),
                version=str(payload.get("version") or ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit = getattr(service, "audit", None)
        if audit is not None:
            audit.append(
                "deployment.candidate_registered",
                actor=(
                    auth.workspace_id
                    if auth is not None
                    else "local-operator"
                ),
                payload={
                    "service": "openclaw",
                    "slot": status.slot,
                    "version": status.version,
                },
            )
        return DeploymentSwitchService.as_dict(status)

    @app.post("/api/v1/deployments/openclaw/{slot}/probe")
    def probe_openclaw_deployment(
        slot: str,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        try:
            status = deployments.probe("openclaw", slot)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="deployment slot not found",
            ) from exc
        except (ValueError, DeploymentProbeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return DeploymentSwitchService.as_dict(status)

    @app.post("/api/v1/deployments/openclaw/{slot}/promote")
    def promote_openclaw_deployment(
        slot: str,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        try:
            status = deployments.promote("openclaw", slot)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="deployment slot not found",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit = getattr(service, "audit", None)
        if audit is not None:
            audit.append(
                "deployment.promoted",
                actor=(
                    auth.workspace_id
                    if auth is not None
                    else "local-operator"
                ),
                payload={
                    "service": "openclaw",
                    "slot": status.slot,
                    "version": status.version,
                },
            )
        return DeploymentSwitchService.as_dict(status)

    @app.post("/api/v1/deployments/openclaw/rollback")
    def rollback_openclaw_deployment(
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        try:
            status = deployments.rollback("openclaw")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit = getattr(service, "audit", None)
        if audit is not None:
            audit.append(
                "deployment.rolled_back",
                actor=(
                    auth.workspace_id
                    if auth is not None
                    else "local-operator"
                ),
                payload={
                    "service": "openclaw",
                    "slot": status.slot,
                    "version": status.version,
                },
            )
        return DeploymentSwitchService.as_dict(status)

    @app.get("/api/v1/audit/verify")
    def verify_audit(auth: AuthContext | None = Depends(current_auth)) -> dict:
        audit = getattr(service, "audit", None)
        if audit is None:
            return {"valid": True, "events": 0, "disabled": True}
        return audit.verify()

    @app.post("/api/v1/tasks")
    def submit(
        contract: TaskContract,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        if auth is not None:
            try:
                require_tenant(auth, contract.context.tenant_id)
            except AuthenticationError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
        try:
            result = service.submit(contract)
            if task_scheduler is not None:
                dispatch = task_scheduler.schedule(contract.task_id)
                result["execution_job_id"] = dispatch.job_id
                result["execution_status"] = dispatch.status
            return result
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/tasks/{task_id}")
    def status(
        task_id: str,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        enforce_task_scope(task_id, auth)
        try:
            return service.status(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @app.get("/api/v1/tasks/{task_id}/execution")
    def execution_status(
        task_id: str,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        enforce_task_scope(task_id, auth)
        if task_scheduler is None:
            raise HTTPException(
                status_code=404,
                detail="durable task queue is not enabled",
            )
        try:
            service.status(task_id)
            job = task_scheduler.status(task_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="execution job not found",
            ) from exc
        return job.to_dict()

    @app.get("/api/v1/tasks/{task_id}/progress")
    def task_progress(
        task_id: str,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        enforce_task_scope(task_id, auth)
        try:
            status_value = service.status(task_id)
            artifact_count = len(service.artifacts(task_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        return map_task_progress(
            status_value["status"],
            task_id=task_id,
            artifact_count=artifact_count,
            error=status_value.get("error"),
        ).to_dict()

    @app.get("/api/v1/tasks/{task_id}/artifacts")
    def artifacts(
        task_id: str,
        auth: AuthContext | None = Depends(current_auth),
    ):
        enforce_task_scope(task_id, auth)
        try:
            service.status(task_id)
            return [item.model_dump() for item in service.artifacts(task_id)]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @app.post("/api/v1/tasks/{task_id}/run")
    def run(
        task_id: str,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        enforce_task_scope(task_id, auth)
        try:
            if task_scheduler is not None:
                service.status(task_id)
                dispatch = task_scheduler.schedule(task_id)
                result = service.status(task_id)
                result["execution_job_id"] = dispatch.job_id
                result["execution_status"] = dispatch.status
                return result
            return service.run(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/tasks/{task_id}/artifacts/{artifact_id}/preview")
    def artifact_preview(
        task_id: str,
        artifact_id: str,
        auth: AuthContext | None = Depends(current_auth),
    ) -> Response:
        enforce_task_scope(task_id, auth)
        artifact = next(
            (
                item
                for item in service.artifacts(task_id)
                if item.artifact_id == artifact_id
            ),
            None,
        )
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        try:
            preview = load_artifact_preview(service.store, artifact)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(
            content=preview.content,
            media_type=preview.media_type,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
            },
        )

    @app.get("/api/v1/tasks/{task_id}/artifacts/{artifact_id}/content")
    def artifact_content(
        task_id: str,
        artifact_id: str,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        enforce_task_scope(task_id, auth)
        try:
            return service.read_text_artifact(task_id, artifact_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/tasks/{task_id}/artifacts/{left_id}/compare/{right_id}")
    def compare_artifact_revisions(
        task_id: str,
        left_id: str,
        right_id: str,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        enforce_task_scope(task_id, auth)
        values = {
            item.artifact_id: item
            for item in service.artifacts(task_id)
        }
        left = values.get(left_id)
        right = values.get(right_id)
        if left is None or right is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        return asdict(compare_artifacts(service.store, left, right))

    @app.post("/api/v1/tasks/{task_id}/artifacts/{artifact_id}/revisions")
    def revise_artifact(
        task_id: str,
        artifact_id: str,
        request: TextArtifactRevisionRequest,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        enforce_task_scope(task_id, auth)
        try:
            revised = service.revise_text_artifact(
                task_id,
                artifact_id,
                request,
            )
            return revised.model_dump()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/tasks/{task_id}/approve")
    def approve(
        task_id: str,
        request: ApprovalRequest,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        enforce_task_scope(task_id, auth)
        try:
            return service.approve(task_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/tasks/{task_id}/retry")
    def retry(
        task_id: str,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        enforce_task_scope(task_id, auth)
        try:
            result = service.retry(task_id)
            if task_scheduler is not None:
                dispatch = task_scheduler.schedule(
                    task_id,
                    restart=True,
                )
                result["execution_job_id"] = dispatch.job_id
                result["execution_status"] = dispatch.status
            return result
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/tasks/{task_id}/line/push-progress")
    def line_push_progress(
        task_id: str,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        enforce_task_scope(task_id, auth)
        if line_controller is None:
            raise HTTPException(
                status_code=503,
                detail="LINE integration is not configured",
            )
        try:
            contract = service.contract(task_id)
            status_value = service.status(task_id)
            artifact_count = len(service.artifacts(task_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        if contract.context.channel != "line" or not contract.context.conversation_id:
            raise HTTPException(
                status_code=409,
                detail="task has no LINE conversation binding",
            )
        progress = map_task_progress(
            status_value["status"],
            task_id=task_id,
            artifact_count=artifact_count,
            error=status_value.get("error"),
        )
        try:
            line_controller.client.push_progress(
                contract.context.conversation_id,
                progress,
            )
        except LineMessagingError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return progress.to_dict()

    @app.get("/api/v1/tasks/{task_id}/release-gate")
    def release_gate(
        task_id: str,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        enforce_task_scope(task_id, auth)
        try:
            status_value = service.status(task_id)
            decision = evaluate_release_gate(
                service.contract(task_id),
                service.artifacts(task_id),
                task_status=status_value["status"],
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        return {
            "allowed": decision.allowed,
            "reasons": list(decision.reasons),
            "selected_artifact_ids": list(decision.selected_artifact_ids),
        }

    @app.post("/api/v1/tasks/{task_id}/release")
    def release(
        task_id: str,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        enforce_task_scope(task_id, auth)
        try:
            return service.release(task_id).model_dump()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/tasks/{task_id}/cancel")
    def cancel(
        task_id: str,
        auth: AuthContext | None = Depends(current_auth),
    ) -> dict:
        enforce_task_scope(task_id, auth)
        try:
            return service.cancel(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    return app


app = create_app()
