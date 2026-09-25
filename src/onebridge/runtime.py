from __future__ import annotations

from .adapters.factory import build_adapter_registry
from .artifacts import LocalObjectStore, S3ObjectStore
from .audit import HashChainAuditLog
from .checkpoints import CheckpointStore
from .compatibility_service import CompatibilityService
from .config import Settings
from .contextforge import ContextForge
from .db import Database
from .durable_service import DurableOneBridgeService
from .sdk import ContextPluginContext, load_context_plugins
from .telemetry import build_telemetry


def build_runtime_service(settings: Settings):
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
