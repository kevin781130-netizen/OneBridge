import pytest

from onebridge.contracts import ArtifactManifest
from onebridge.release import ReleaseError, build_release_manifest


def artifact(artifact_id: str, kind: str, status: str = "approved") -> ArtifactManifest:
    return ArtifactManifest(
        artifact_id=artifact_id,
        project_id="prj_1",
        task_id="task_1",
        revision=1,
        kind=kind,
        media_type="application/json",
        sha256="a" * 64,
        uri=f"file:///tmp/{artifact_id}",
        producer_adapter="mock",
        producer_version="1",
        status=status,
    )


def test_release_manifest_requires_approval():
    with pytest.raises(ReleaseError):
        build_release_manifest([artifact("art_1", "content", "awaiting_review")])


def test_release_manifest_is_hash_bound():
    manifest = build_release_manifest([
        artifact("art_1", "content"),
        artifact("art_2", "design"),
    ])
    assert manifest.schema == 1
    assert len(manifest.manifest_sha256) == 64
    assert [item.artifact_id for item in manifest.artifacts] == ["art_1", "art_2"]
