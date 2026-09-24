from pathlib import Path

from onebridge.artifacts import LocalObjectStore
from onebridge.contracts import ArtifactManifest
from onebridge.review import compare_artifacts


def manifest(artifact_id: str, revision: int, uri: str, sha256: str) -> ArtifactManifest:
    return ArtifactManifest(
        artifact_id=artifact_id,
        project_id="prj_1",
        task_id="task_1",
        revision=revision,
        kind="design",
        media_type="text/html",
        sha256=sha256,
        uri=uri,
        producer_adapter="human_review",
        producer_version="1",
        status="awaiting_review",
    )


def test_compare_artifacts_returns_text_diff(tmp_path: Path):
    store = LocalObjectStore(tmp_path / "objects")
    left_stored = store.put_bytes(b"<h1>Old</h1>\n", filename="index.html")
    right_stored = store.put_bytes(b"<h1>New</h1>\n", filename="index.html")

    result = compare_artifacts(
        store,
        manifest("art_old", 1, left_stored.uri, left_stored.sha256),
        manifest("art_new", 2, right_stored.uri, right_stored.sha256),
    )

    assert result.changed
    assert result.revision_delta == 1
    assert result.diff is not None
    assert "-<h1>Old</h1>" in result.diff
    assert "+<h1>New</h1>" in result.diff
