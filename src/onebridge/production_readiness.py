from __future__ import annotations

from dataclasses import asdict, dataclass

from .deployment_switch import validate_probe_url


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    name: str
    passed: bool
    detail: str
    required: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProductionReadinessReport:
    checks: tuple[ReadinessCheck, ...]

    @property
    def ready(self) -> bool:
        return all(
            check.passed or not check.required
            for check in self.checks
        )

    @property
    def failures(self) -> tuple[str, ...]:
        return tuple(
            check.name
            for check in self.checks
            if check.required and not check.passed
        )

    def to_dict(self) -> dict:
        return {
            "ready": self.ready,
            "failures": list(self.failures),
            "checks": [
                check.to_dict()
                for check in self.checks
            ],
        }


def evaluate_production_readiness(
    settings,
    registry=None,
) -> ProductionReadinessReport:
    checks: list[ReadinessCheck] = []

    def add(
        name: str,
        passed: bool,
        detail: str,
        *,
        required: bool = True,
    ) -> None:
        checks.append(
            ReadinessCheck(
                name=name,
                passed=bool(passed),
                detail=detail,
                required=required,
            )
        )

    add(
        "database.postgresql",
        str(settings.database_url).lower().startswith(
            ("postgresql://", "postgresql+")
        ),
        "production control-plane database must use PostgreSQL",
    )
    add(
        "storage.remote",
        str(settings.storage_backend).lower()
        in {"s3", "minio"},
        "production artifact storage must use S3/MinIO",
    )
    add(
        "api.authentication",
        bool(settings.require_api_key),
        "production HTTP controls require API-key authentication",
    )
    add(
        "release.controller",
        bool(settings.release_controller_required),
        "direct production promotion must be disabled",
    )
    add(
        "release.two_person",
        bool(settings.release_two_person_required),
        "production releases require requester/approver separation",
    )

    admin_count = len(
        tuple(settings.release_admin_workspaces)
    )
    add(
        "release.admins",
        admin_count >= 2,
        "at least two release-admin workspaces are required",
    )

    actuator_complete = bool(
        settings.openclaw_actuator_url
        and settings.openclaw_actuator_secret
    )
    add(
        "openclaw.actuator",
        actuator_complete,
        "external OpenClaw switching hook must be fully configured",
    )

    smoke_url = str(
        settings.openclaw_smoke_url or ""
    ).strip()
    smoke_valid = False
    if smoke_url:
        try:
            validate_probe_url(smoke_url)
            smoke_valid = True
        except ValueError:
            smoke_valid = False
    add(
        "openclaw.smoke_path",
        smoke_valid,
        "stable OpenClaw smoke URL must pass probe URL policy",
    )

    add(
        "worker.queue",
        bool(settings.task_queue_url),
        "durable task queue must be configured",
    )
    add(
        "flowise.real_adapter",
        bool(
            settings.flowise_base_url
            and settings.flowise_chatflow_id
        ),
        "Flowise base URL and chatflow id must be configured",
    )
    add(
        "open_design.real_adapter",
        bool(settings.open_design_mcp_url),
        "Open Design MCP URL must be configured",
    )
    add(
        "hermes.real_adapter",
        bool(settings.hermes_executable),
        "Hermes executable must be configured",
    )
    add(
        "hermes.strong_sandbox",
        bool(settings.hermes_require_strong_sandbox),
        "Hermes strong sandbox must remain enabled",
    )
    add(
        "telemetry.export",
        bool(settings.otel_endpoint),
        "production telemetry export endpoint must be configured",
    )

    if registry is not None:
        for adapter_id in (
            "flowise",
            "open_design",
            "hermes",
        ):
            try:
                adapter = registry.get(adapter_id)
            except KeyError:
                add(
                    f"adapter.{adapter_id}",
                    False,
                    f"{adapter_id} adapter is not registered",
                )
                continue
            version = str(adapter.version)
            add(
                f"adapter.{adapter_id}",
                not version.startswith("mock-"),
                (
                    f"{adapter_id} must resolve to a real adapter "
                    "rather than a mock implementation"
                ),
            )

    return ProductionReadinessReport(tuple(checks))
