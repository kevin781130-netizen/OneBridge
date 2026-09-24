from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    database_url: str = os.getenv("ONEBRIDGE_DATABASE_URL", "sqlite:///./onebridge.db")
    storage_backend: str = os.getenv("ONEBRIDGE_STORAGE_BACKEND", "local")
    storage_root: Path = Path(os.getenv("ONEBRIDGE_STORAGE_ROOT", ".onebridge/objects"))
    s3_bucket: str = os.getenv("ONEBRIDGE_S3_BUCKET", "onebridge")
    s3_endpoint: str | None = os.getenv("ONEBRIDGE_S3_ENDPOINT")
    s3_region: str | None = os.getenv("ONEBRIDGE_S3_REGION")
    s3_access_key: str | None = os.getenv("ONEBRIDGE_S3_ACCESS_KEY")
    s3_secret_key: str | None = os.getenv("ONEBRIDGE_S3_SECRET_KEY")
