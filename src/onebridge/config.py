from __future__ import annotations

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

    flowise_base_url: str | None = os.getenv("ONEBRIDGE_FLOWISE_BASE_URL")
    flowise_chatflow_id: str | None = os.getenv("ONEBRIDGE_FLOWISE_CHATFLOW_ID")
    flowise_api_key: str | None = os.getenv("ONEBRIDGE_FLOWISE_API_KEY")
    flowise_timeout_seconds: float = float(
        os.getenv("ONEBRIDGE_FLOWISE_TIMEOUT_SECONDS", "120")
    )
    flowise_allowed_override_keys: tuple[str, ...] = _env_csv(
        "ONEBRIDGE_FLOWISE_ALLOWED_OVERRIDE_KEYS"
    )

    s3_bucket: str = os.getenv("ONEBRIDGE_S3_BUCKET", "onebridge")
    s3_endpoint: str | None = os.getenv("ONEBRIDGE_S3_ENDPOINT")
    s3_region: str | None = os.getenv("ONEBRIDGE_S3_REGION")
    s3_access_key: str | None = os.getenv("ONEBRIDGE_S3_ACCESS_KEY")
    s3_secret_key: str | None = os.getenv("ONEBRIDGE_S3_SECRET_KEY")
