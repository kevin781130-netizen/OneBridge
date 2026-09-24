from __future__ import annotations

import difflib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .contracts import ArtifactManifest


_TEXT_MEDIA = {
    "application/json",
    "application/xml",
    "application/javascript",
}


@dataclass(frozen=True, slots=True)
class ArtifactComparison:
    left_artifact_id: str
    right_artifact_id: str
    changed: bool
    same_kind: bool
    revision_delta: int
    sha256_changed: bool
    diff: str | None
    diff_truncated: bool


def _is_text(media_type: str) -> bool:
    value = media_type.lower().split(";", 1)[0].strip()
    return value.startswith("text/") or value in _TEXT_MEDIA or value.endswith("+json")


def compare_artifacts(
    store,
    left: ArtifactManifest,
    right: ArtifactManifest,
    *,
    max_bytes: int = 1_000_000,
    max_diff_chars: int = 100_000,
) -> ArtifactComparison:
    """Compare two artifact revisions with a bounded text diff when possible."""

    diff: str | None = None
    truncated = False

    if _is_text(left.media_type) and _is_text(right.media_type):
        with tempfile.TemporaryDirectory(prefix="onebridge-review-") as temp:
            root = Path(temp)
            left_path = store.get_file(left.uri, root / "left")
            right_path = store.get_file(right.uri, root / "right")

            if left_path.stat().st_size <= max_bytes and right_path.stat().st_size <= max_bytes:
                try:
                    left_text = left_path.read_text(encoding="utf-8")
                    right_text = right_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    diff = None
                else:
                    rendered = "".join(
                        difflib.unified_diff(
                            left_text.splitlines(keepends=True),
                            right_text.splitlines(keepends=True),
                            fromfile=f"{left.artifact_id}@r{left.revision}",
                            tofile=f"{right.artifact_id}@r{right.revision}",
                            n=3,
                        )
                    )
                    if len(rendered) > max_diff_chars:
                        diff = rendered[:max_diff_chars] + "\n[diff truncated]\n"
                        truncated = True
                    else:
                        diff = rendered

    return ArtifactComparison(
        left_artifact_id=left.artifact_id,
        right_artifact_id=right.artifact_id,
        changed=left.sha256 != right.sha256,
        same_kind=left.kind == right.kind,
        revision_delta=right.revision - left.revision,
        sha256_changed=left.sha256 != right.sha256,
        diff=diff,
        diff_truncated=truncated,
    )
