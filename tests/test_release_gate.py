from onebridge.contracts import ArtifactManifest, TaskContract
from onebridge.release_gate import evaluate_release_gate


def contract() -> TaskContract:
    return TaskContract.model_validate({
        "input": {
            "goal": "Build a page",
            "required_outputs": ["content", "design"],
        },
        "context": {"tenant_id": "t1", "user_id": "u1"},
        "policy": {"approval": "before_publish"},
    })


def artifact(kind: str, status: str = "approved") -> ArtifactManifest:
    task = contract()
    return ArtifactManifest(
        artifact_id=f"art_{kind}",
        project_id=task.project_id,
        task_id=task.task_id,
        revision=1,
        kind=kind,
        media_type="application/json",
        sha256="a" * 64,
        uri=f"file:///tmp/{kind}",
        producer_adapter="mock",
        producer_version="1",
        status=status,
    )


def test_release_gate_allows_complete_approved_set():
    task = contract()
    values = [
        ArtifactManifest(
            artifact_id="art_content",
            project_id=task.project_id,
            task_id=task.task_id,
            revision=1,
            kind="content",
            media_type="application/json",
            sha256="a" * 64,
            uri="file:///tmp/content",
            producer_adapter="flowise",
            producer_version="1",
            status="approved",
        ),
        ArtifactManifest(
            artifact_id="art_design",
            project_id=task.project_id,
            task_id=task.task_id,
            revision=1,
            kind="design",
            media_type="text/html",
            sha256="b" * 64,
            uri="file:///tmp/design",
            producer_adapter="open_design",
            producer_version="1",
            status="approved",
        ),
    ]
    decision = evaluate_release_gate(task, values, task_status="succeeded")
    assert decision.allowed
    assert not decision.reasons


def test_release_gate_blocks_missing_or_unapproved():
    task = contract()
    values = [
        ArtifactManifest(
            artifact_id="art_content",
            project_id=task.project_id,
            task_id=task.task_id,
            revision=1,
            kind="content",
            media_type="application/json",
            sha256="a" * 64,
            uri="file:///tmp/content",
            producer_adapter="flowise",
            producer_version="1",
            status="awaiting_review",
        )
    ]
    decision = evaluate_release_gate(
        task,
        values,
        task_status="waiting_approval",
    )
    assert not decision.allowed
    assert any("required_artifact_missing:design" == item for item in decision.reasons)
    assert "human_approval_incomplete" in decision.reasons
