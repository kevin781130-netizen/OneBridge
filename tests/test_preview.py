from pathlib import Path

import pytest

from onebridge.artifacts import LocalObjectStore
from onebridge.contracts import ArtifactManifest
from onebridge.preview import load_artifact_preview


def artifact(uri: str, media_type: str, sha256: str) -> ArtifactManifest:
    return ArtifactManifest(
        artifact_id="art_1",
        project_id="prj_1",
        task_id="task_1",
        revision=1,
        kind="design",
        media_type=media_type,
        sha256=sha256,
        uri=uri,
        producer_adapter="open_design",
        producer_version="1",
        status="awaiting_review",
    )


def test_png_preview_is_loaded(tmp_path: Path):
    store = LocalObjectStore(tmp_path / "objects")
    stored = store.put_bytes(b"\x89PNG\r\n\x1a\npreview", filename="preview.png")
    preview = load_artifact_preview(
        store,
        artifact(stored.uri, "image/png", stored.sha256),
    )
    assert preview.media_type == "image/png"
    assert preview.content.startswith(b"\x89PNG")
    assert preview.size == len(preview.content)


def test_svg_preview_is_blocked(tmp_path: Path):
    store = LocalObjectStore(tmp_path / "objects")
    stored = store.put_bytes(b"<svg><script/></svg>", filename="preview.svg")
    with pytest.raises(ValueError):
        load_artifact_preview(
            store,
            artifact(stored.uri, "image/svg+xml", stored.sha256),
        )
