from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    uri: str
    sha256: str
    size: int


class LocalObjectStore:
    """Content-addressed immutable object store, adapted from the owner's CutPilot storage pattern."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, content: bytes, *, filename: str) -> StoredArtifact:
        digest = hashlib.sha256(content).hexdigest()
        clean_name = Path(filename).name or "artifact.bin"
        dest = (self.root / digest[:2] / digest / clean_name).resolve()
        if self.root != dest and self.root not in dest.parents:
            raise ValueError("artifact path escapes storage root")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            if hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
                raise RuntimeError("immutable artifact hash collision")
        else:
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_bytes(content)
            tmp.replace(dest)
        return StoredArtifact(dest.as_uri(), digest, len(content))

    def get_file(self, uri: str, destination: str | Path) -> Path:
        source = Path(urlparse(uri).path) if uri.startswith("file://") else Path(uri)
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return target


class S3ObjectStore:
    def __init__(self, bucket: str, *, endpoint_url: str | None = None, region: str | None = None,
                 access_key: str | None = None, secret_key: str | None = None) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        config = Config(s3={"addressing_style": "path"}) if endpoint_url else None
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=region or None,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            config=config,
        )

    def put_bytes(self, content: bytes, *, filename: str) -> StoredArtifact:
        digest = hashlib.sha256(content).hexdigest()
        key = f"sha256/{digest[:2]}/{digest}/{Path(filename).name or 'artifact.bin'}"
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=content)
        return StoredArtifact(f"s3://{self.bucket}/{key}", digest, len(content))

    def get_file(self, uri: str, destination: str | Path) -> Path:
        parsed = urlparse(uri)
        if parsed.scheme != "s3":
            raise ValueError("expected s3 uri")
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(parsed.netloc, parsed.path.lstrip("/"), str(target))
        return target
