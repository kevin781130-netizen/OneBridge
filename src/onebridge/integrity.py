from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class IntegrityError(RuntimeError):
    pass


def sha256_file(path: str | Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def verify_artifact_manifest(root: str | Path, manifest_name: str = "artifact_manifest.json") -> dict[str, Any]:
    """Fail-closed release/evidence manifest verification generalized from SONICRAFT."""

    base = Path(root).resolve()
    manifest_path = base / manifest_name
    if not manifest_path.is_file():
        raise IntegrityError("artifact manifest is required")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise IntegrityError(f"invalid artifact manifest: {exc}") from exc

    if int(manifest.get("schema", 0)) != 1:
        raise IntegrityError("unsupported artifact manifest schema")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise IntegrityError("artifact manifest contains no files")

    verified: list[dict[str, Any]] = []
    for entry in files:
        if not isinstance(entry, dict):
            raise IntegrityError("invalid artifact manifest entry")
        name = str(entry.get("name") or "")
        expected = str(entry.get("sha256") or "").lower()
        if not name or Path(name).name != name or "/" in name or "\\" in name:
            raise IntegrityError("invalid artifact filename")
        if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
            raise IntegrityError(f"invalid SHA-256 for {name}")
        path = base / name
        if not path.is_file():
            raise IntegrityError(f"artifact file missing: {name}")
        actual = sha256_file(path)
        if actual != expected:
            raise IntegrityError(f"artifact SHA-256 mismatch: {name}")
        verified.append({"name": name, "sha256": actual, "size": path.stat().st_size})

    return {"verified": True, "schema": 1, "files": verified}
