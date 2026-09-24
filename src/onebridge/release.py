from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .contracts import ArtifactManifest


class ReleaseError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseArtifact:
    artifact_id: str
    kind: str
    revision: int
    sha256: str
    uri: str
    producer_adapter: str
    producer_version: str


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    schema: int
    project_id: str
    task_id: str
    created_at: str
    artifacts: tuple[ReleaseArtifact, ...]
    manifest_sha256: str


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_release_manifest(
    artifacts: Iterable[ArtifactManifest],
    *,
    require_approved: bool = True,
) -> ReleaseManifest:
    values = list(artifacts)
    if not values:
        raise ReleaseError("release requires at least one artifact")
    project_ids = {item.project_id for item in values}
    task_ids = {item.task_id for item in values}
    if len(project_ids) != 1 or len(task_ids) != 1:
        raise ReleaseError("release artifacts must belong to one project and task")
    if require_approved:
        not_approved = [item.artifact_id for item in values if item.status != "approved"]
        if not_approved:
            raise ReleaseError(f"release blocked by unapproved artifacts: {not_approved}")

    release_artifacts = tuple(
        ReleaseArtifact(
            artifact_id=item.artifact_id,
            kind=item.kind,
            revision=item.revision,
            sha256=item.sha256,
            uri=item.uri,
            producer_adapter=item.producer_adapter,
            producer_version=item.producer_version,
        )
        for item in sorted(values, key=lambda x: (x.kind, x.revision, x.artifact_id))
    )
    body = {
        "schema": 1,
        "project_id": values[0].project_id,
        "task_id": values[0].task_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": [asdict(item) for item in release_artifacts],
    }
    digest = hashlib.sha256(_canonical(body)).hexdigest()
    return ReleaseManifest(
        schema=1,
        project_id=body["project_id"],
        task_id=body["task_id"],
        created_at=body["created_at"],
        artifacts=release_artifacts,
        manifest_sha256=digest,
    )


def write_release_manifest(manifest: ReleaseManifest, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(manifest)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target
