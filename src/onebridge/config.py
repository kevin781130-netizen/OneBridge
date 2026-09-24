from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    return tuple(
        item.strip()
        for item in raw.split(",")
        if item.strip()
    )


def _env_json_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be a JSON string array") from exc
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be a JSON string array")
    return tuple(value)


@dataclass(slots=True)
class Settings:
    database_url: str = os.getenv("ONEBRIDGE_DATABASE_URL", "sqlite:///./onebridge.db")
    state_root: Path = Path(os.getenv("ONEBRIDGE_STATE_ROOT", ".onebridge"))
    storage_backend: str = os.getenv("ONEBRIDGE_STORAGE_BACKEND", "local")
    storage_root: Path = Path(os.getenv("ONEBRIDGE_STORAGE_ROOT", ".onebridge/objects"))
    checkpoint_root: Path = Path(os.getenv("ONEBRIDGE_CHECKPOINT_ROOT", ".onebridge/checkpoints"))
    audit_log: Path = Path(os.getenv("ONEBRIDGE_AUDIT_LOG", ".onebridge/audit.jsonl"))
    identity_db: Path = Path(os.getenv("ONEBRIDGE_IDENTITY_DB", ".onebridge/identity.db"))
    require_api_key: bool = _env_bool("ONEBRIDGE_REQUIRE_API_KEY", False)
    require_qualified_adapters: bool = _env_bool(
        "ONEBRIDGE_REQUIRE_QUALIFIED_ADAPTERS",
        False,
    )

    flowise_base_url: str | None = os.getenv("ONEBRIDGE_FLOWISE_BASE_URL")
    flowise_chatflow_id: str | None = os.getenv("ONEBRIDGE_FLOWISE_CHATFLOW_ID")
    flowise_api_key: str | None = os.getenv("ONEBRIDGE_FLOWISE_API_KEY")
    flowise_timeout_seconds: float = float(
        os.getenv("ONEBRIDGE_FLOWISE_TIMEOUT_SECONDS", "120")
    )
    flowise_allowed_override_keys: tuple[str, ...] = _env_csv(
        "ONEBRIDGE_FLOWISE_ALLOWED_OVERRIDE_KEYS"
    )

    open_design_mcp_url: str | None = os.getenv("ONEBRIDGE_OPEN_DESIGN_MCP_URL")
    open_design_tool: str = os.getenv(
        "ONEBRIDGE_OPEN_DESIGN_TOOL",
        "render_design",
    )
    open_design_timeout_seconds: float = float(
        os.getenv("ONEBRIDGE_OPEN_DESIGN_TIMEOUT_SECONDS", "120")
    )

    hermes_executable: str | None = os.getenv("ONEBRIDGE_HERMES_EXECUTABLE")
    hermes_args: tuple[str, ...] = _env_json_list(
        "ONEBRIDGE_HERMES_ARGS_JSON",
        ("--request", "{request}", "--output", "{output}"),
    )
    hermes_timeout_seconds: float = float(
        os.getenv("ONEBRIDGE_HERMES_TIMEOUT_SECONDS", "300")
    )
    hermes_require_strong_sandbox: bool = _env_bool(
        "ONEBRIDGE_HERMES_REQUIRE_STRONG_SANDBOX",
        True,
    )

    otel_service_name: str = os.getenv(
        "ONEBRIDGE_OTEL_SERVICE_NAME",
        "onebridge",
    )
    otel_endpoint: str | None = os.getenv("ONEBRIDGE_OTEL_ENDPOINT")
    otel_headers: str | None = os.getenv("ONEBRIDGE_OTEL_HEADERS")

    line_channel_secret: str | None = os.getenv("ONEBRIDGE_LINE_CHANNEL_SECRET")
    line_channel_access_token: str | None = os.getenv("ONEBRIDGE_LINE_CHANNEL_ACCESS_TOKEN")
    line_tenant_id: str | None = os.getenv("ONEBRIDGE_LINE_TENANT_ID")
    line_required_outputs: tuple[str, ...] = _env_csv(
        "ONEBRIDGE_LINE_REQUIRED_OUTPUTS"
    ) or ("content", "design", "code", "test_report")
    adapter_plugins: tuple[str, ...] = _env_csv(
        "ONEBRIDGE_ADAPTER_PLUGINS"
    )

    s3_bucket: str = os.getenv("ONEBRIDGE_S3_BUCKET", "onebridge")
    s3_endpoint: str | None = os.getenv("ONEBRIDGE_S3_ENDPOINT")
    s3_region: str | None = os.getenv("ONEBRIDGE_S3_REGION")
    s3_access_key: str | None = os.getenv("ONEBRIDGE_S3_ACCESS_KEY")
    s3_secret_key: str | None = os.getenv("ONEBRIDGE_S3_SECRET_KEY")
