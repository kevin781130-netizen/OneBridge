from pathlib import Path

import pytest

from onebridge.artifacts import LocalObjectStore
from onebridge.workers.workspace import WorkerWorkspace


def test_worker_workspace_materializes_and_collects_outputs(tmp_path: Path):
    store = LocalObjectStore(tmp_path / "objects")
    source = store.put_bytes(b"input", filename="input.txt")

    with WorkerWorkspace("job_1", parent=tmp_path / "work") as workspace:
        materialized = workspace.materialize(
            store,
            source.uri,
            name="input.txt",
        )
        assert materialized.read_bytes() == b"input"

        output = workspace.output_path("nested/result.json")
        output.write_text('{"ok": true}', encoding="utf-8")

        artifacts = workspace.collect_outputs()
        assert len(artifacts) == 1
        assert artifacts[0].relative_path == "nested/result.json"
        assert len(artifacts[0].sha256) == 64

        root = workspace.root
        assert root is not None and root.exists()

    assert root is not None and not root.exists()


def test_workspace_blocks_path_escape(tmp_path: Path):
    with WorkerWorkspace("job_2", parent=tmp_path) as workspace:
        with pytest.raises(ValueError):
            workspace.output_path("../escape.txt")
