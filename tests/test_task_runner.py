from pathlib import Path

from onebridge.adapters.mock import default_mock_registry
from onebridge.artifacts import LocalObjectStore
from onebridge.contracts import ApprovalRequest, TaskContract
from onebridge.db import Database
from onebridge.service import OneBridgeService
from onebridge.workers.queue import SQLiteWorkerQueue
from onebridge.workers.task_runner import TaskScheduler, TaskWorker, task_job_id


def build_service(tmp_path: Path) -> OneBridgeService:
    db = Database(f"sqlite:///{tmp_path / 'db.sqlite'}")
    db.create_all()
    return OneBridgeService(
        db,
        default_mock_registry(),
        LocalObjectStore(tmp_path / "objects"),
    )


def contract() -> TaskContract:
    return TaskContract.model_validate({
        "input": {
            "goal": "Build page",
            "required_outputs": ["content", "design"],
        },
        "context": {
            "tenant_id": "tenant",
            "user_id": "user",
        },
        "policy": {"approval": "before_publish"},
    })


def test_scheduler_is_idempotent_and_worker_runs_pipeline(tmp_path: Path):
    service = build_service(tmp_path)
    queue = SQLiteWorkerQueue(tmp_path / "queue.sqlite")
    scheduler = TaskScheduler(queue)
    task = contract()
    service.submit(task)

    first = scheduler.schedule(task.task_id)
    second = scheduler.schedule(task.task_id)

    assert first.created is True
    assert second.created is False
    assert first.job_id == task_job_id(task.task_id)
    assert second.status == "queued"

    worker = TaskWorker(
        service,
        queue,
        worker_id="worker-test",
        workspace_parent=tmp_path / "work",
    )
    execution = worker.run_one()

    assert execution.worked is True
    assert execution.status == "succeeded"
    assert service.status(task.task_id)["status"] == "waiting_approval"
    assert queue.get(first.job_id).result["task_status"] == "waiting_approval"


def test_explicit_retry_requeues_completed_execution_job(tmp_path: Path):
    service = build_service(tmp_path)
    queue = SQLiteWorkerQueue(tmp_path / "queue.sqlite")
    scheduler = TaskScheduler(queue)
    task = contract()
    service.submit(task)
    scheduler.schedule(task.task_id)

    worker = TaskWorker(
        service,
        queue,
        worker_id="worker-test",
        workspace_parent=tmp_path / "work",
    )
    worker.run_one()

    artifacts = service.artifacts(task.task_id)
    service.approve(
        task.task_id,
        ApprovalRequest(
            artifact_ids=[artifacts[0].artifact_id],
            decision="reject",
            actor="reviewer",
        ),
    )
    assert service.status(task.task_id)["status"] == "blocked"

    service.retry(task.task_id)
    dispatch = scheduler.schedule(task.task_id, restart=True)

    assert dispatch.status == "queued"
    assert dispatch.created is False
