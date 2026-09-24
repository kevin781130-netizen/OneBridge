from pathlib import Path

from onebridge.workers.executor import WorkerExecutor
from onebridge.workers.queue import SQLiteWorkerQueue


def test_worker_executor_claims_runs_and_finishes(tmp_path: Path):
    queue = SQLiteWorkerQueue(tmp_path / "workers.db")
    job = queue.enqueue(
        task_id="task_1",
        adapter="hermes",
        payload={"goal": "build"},
    )

    executor = WorkerExecutor(
        queue,
        adapter="hermes",
        worker_id="worker-a",
        workspace_parent=tmp_path / "work",
    )

    def handler(claimed, workspace):
        assert claimed.id == job.id
        assert workspace.outputs is not None
        output = workspace.output_path("result.txt")
        output.write_text("ok", encoding="utf-8")
        return {"ok": True}

    result = executor.run_one(handler)
    assert result.worked
    assert result.status == "succeeded"
    assert queue.get(job.id).result == {"ok": True}


def test_worker_executor_fails_job_on_exception(tmp_path: Path):
    queue = SQLiteWorkerQueue(tmp_path / "workers.db")
    job = queue.enqueue(
        task_id="task_1",
        adapter="hermes",
        payload={},
    )
    executor = WorkerExecutor(
        queue,
        adapter="hermes",
        workspace_parent=tmp_path / "work",
    )

    def handler(claimed, workspace):
        raise RuntimeError("token=super-secret")

    result = executor.run_one(handler)
    assert result.status == "failed"
    assert "super-secret" not in (result.error or "")
    assert queue.get(job.id).status == "failed"
