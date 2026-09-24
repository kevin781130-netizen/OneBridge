from onebridge.line_progress import map_release_progress, map_task_progress


def test_task_progress_mapping_is_channel_safe():
    value = map_task_progress(
        "failed",
        task_id="task_1",
        artifact_count=2,
        error="token=super-secret provider dump",
    )
    assert value.code == "OB_TASK_FAILED"
    assert value.retryable is True
    assert "super-secret" not in value.message
    assert value.task_id == "task_1"


def test_release_progress_mapping():
    ready = map_release_progress(
        allowed=True,
        task_id="task_1",
    )
    assert ready.code == "OB_RELEASE_READY"

    blocked = map_release_progress(
        allowed=False,
        task_id="task_1",
        reasons=["human_approval_incomplete"],
    )
    assert blocked.code == "OB_RELEASE_BLOCKED"
    assert blocked.retryable is True
