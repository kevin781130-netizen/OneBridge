from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException

from .adapters.factory import build_adapter_registry
from .artifacts import LocalObjectStore, S3ObjectStore
from .audit import HashChainAuditLog
from .auth import AuthContext, AuthenticationError, authenticate_bearer, require_tenant
from .checkpoints import CheckpointStore
from .config import Settings
from .contracts import ApprovalRequest, TaskContract
from .db import Database, ProjectRecord, TaskRecord
from .durable_service import DurableOneBridgeService
from .identity import IdentityStore
from .service import OneBridgeService


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

    return DurableOneBridgeService(
        db,
        build_adapter_registry(settings),
        store,
        audit=HashChainAuditLog(settings.audit_log),
        checkpoints=CheckpointStore(settings.checkpoint_root),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    service = build_service(settings)
    identities = IdentityStore(settings.identity_db)
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

    @app.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "version": "0.1.0",
            "adapters": service.registry.names(),
            "auth_required": settings.require_api_key,
        }

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
            return service.submit(contract)
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
            return service.run(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except (ValueError, RuntimeError) as exc:
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
            return service.retry(task_id)
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
