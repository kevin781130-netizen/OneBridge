import hashlib
import json
from pathlib import Path

import pytest

from onebridge.integrity import IntegrityError, verify_artifact_manifest


def test_manifest_verification(tmp_path: Path):
    data = b"hello"
    (tmp_path / "artifact.bin").write_bytes(data)
    manifest = {
        "schema": 1,
        "files": [{"name": "artifact.bin", "sha256": hashlib.sha256(data).hexdigest()}],
    }
    (tmp_path / "artifact_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = verify_artifact_manifest(tmp_path)
    assert result["verified"] is True


def test_manifest_rejects_hash_mismatch(tmp_path: Path):
    (tmp_path / "artifact.bin").write_bytes(b"hello")
    (tmp_path / "artifact_manifest.json").write_text(
        json.dumps({"schema": 1, "files": [{"name": "artifact.bin", "sha256": "0" * 64}]}),
        encoding="utf-8",
    )
    with pytest.raises(IntegrityError):
        verify_artifact_manifest(tmp_path)
