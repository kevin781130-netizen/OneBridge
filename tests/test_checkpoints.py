import json
from pathlib import Path

from onebridge.checkpoints import CheckpointStore
from onebridge.workflow_graph import WorkflowStep


def test_checkpoint_validates_step_and_lineage(tmp_path: Path):
    store = CheckpointStore(tmp_path / "checkpoints")
    step = WorkflowStep(0, "content", "flowise", "content")
    saved = store.save(
        "task_1",
        step=step,
        input_lineage=("root",),
        output_artifact_ids=("art_1",),
        metadata={"ok": True},
    )
    assert saved.output_artifact_ids == ("art_1",)
    assert store.load("task_1", step=step, input_lineage=("root",)) is not None
    assert store.load("task_1", step=step, input_lineage=("different",)) is None


def test_checkpoint_tampering_is_rejected(tmp_path: Path):
    store = CheckpointStore(tmp_path / "checkpoints")
    step = WorkflowStep(0, "content", "flowise", "content")
    store.save("task_1", step=step, input_lineage=(), output_artifact_ids=("art_1",))
    target = tmp_path / "checkpoints" / "task_1" / "step-0.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["output_artifact_ids"] = ["art_tampered"]
    target.write_text(json.dumps(payload), encoding="utf-8")
    assert store.load("task_1", step=step, input_lineage=()) is None
