from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .adapters.mock import default_mock_registry
from .artifacts import LocalObjectStore, S3ObjectStore
from .audit import HashChainAuditLog
from .checkpoints import CheckpointStore
from .config import Settings
from .contracts import ApprovalRequest, TaskContract
from .db import Database
from .durable_service import DurableOneBridgeService
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
        default_mock_registry(),
        store,
        audit=HashChainAuditLog(settings.audit_log),
        checkpoints=CheckpointStore(settings.checkpoint_root),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    service = build_service(settings)
    app = FastAPI(title="OneBridge Control Plane", version="0.1.0")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "version": "0.1.0", "adapters": service.registry.names()}

    @app.get("/api/v1/audit/verify")
    def verify_audit() -> dict:
        audit = getattr(service, "audit", None)
        if audit is None:
            return {"valid": True, "events": 0, "disabled": True}
        return audit.verify()

    @app.post("/api/v1/tasks")
    def submit(contract: TaskContract) -> dict:
        try:
            return service.submit(contract)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/tasks/{task_id}")
    def status(task_id: str) -> dict:
        try:
            return service.status(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @app.get("/api/v1/tasks/{task_id}/artifacts")
    def artifacts(task_id: str):
        try:
            service.status(task_id)
            return [item.model_dump() for item in service.artifacts(task_id)]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @app.post("/api/v1/tasks/{task_id}/run")
    def run(task_id: str) -> dict:
        try:
            return service.run(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/tasks/{task_id}/approve")
    def approve(task_id: str, request: ApprovalRequest) -> dict:
        try:
            return service.approve(task_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/tasks/{task_id}/retry")
    def retry(task_id: str) -> dict:
        try:
            return service.retry(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/tasks/{task_id}/cancel")
    def cancel(task_id: str) -> dict:
        try:
            return service.cancel(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    return app


app = create_app()
