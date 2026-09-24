from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from .contracts import ArtifactManifest


_ALLOWED_PREVIEW_TYPES = frozenset({
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "application/pdf",
})


@dataclass(frozen=True, slots=True)
class ArtifactPreview:
    artifact_id: str
    media_type: str
    content: bytes
    size: int


def load_artifact_preview(
    store,
    artifact: ArtifactManifest,
    *,
    max_bytes: int = 10 * 1024 * 1024,
) -> ArtifactPreview:
    media_type = artifact.media_type.lower().split(";", 1)[0].strip()
    if media_type not in _ALLOWED_PREVIEW_TYPES:
        raise ValueError("artifact media type is not previewable")

    with tempfile.TemporaryDirectory(prefix="onebridge-preview-") as temp:
        target = Path(temp) / "preview"
        materialized = store.get_file(artifact.uri, target)
        if materialized.is_symlink() or not materialized.is_file():
            raise ValueError("artifact preview source is invalid")
        size = materialized.stat().st_size
        if size > max_bytes:
            raise ValueError("artifact preview exceeds size limit")
        content = materialized.read_bytes()

    return ArtifactPreview(
        artifact_id=artifact.artifact_id,
        media_type=media_type,
        content=content,
        size=len(content),
    )
