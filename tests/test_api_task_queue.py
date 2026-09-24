from pathlib import Path

from fastapi.testclient import TestClient

from onebridge.api import create_app
from onebridge.config import Settings
from onebridge.workers.queue import SQLiteWorkerQueue
from onebridge.workers.task_runner import task_job_id


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        state_root=tmp_path,
        storage_root=tmp_path / "objects",
        checkpoint_root=tmp_path / "checkpoints",
        audit_log=tmp_path / "audit.jsonl",
        identity_db=tmp_path / "identity.db",
        task_queue_url=str(tmp_path / "task-queue.sqlite"),
    )


def test_submit_auto_dispatches_without_inline_execution(tmp_path: Path):
    config = settings(tmp_path)
    client = TestClient(create_app(config))

    created = client.post(
        "/api/v1/tasks",
        json={
            "input": {
                "goal": "Build page",
                "required_outputs": ["content"],
            },
            "context": {
                "tenant_id": "local",
                "user_id": "user",
            },
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "queued"
    assert body["execution_status"] == "queued"

    queue = SQLiteWorkerQueue(config.task_queue_url)
    job = queue.get(task_job_id(body["task_id"]))
    assert job.status == "queued"
    assert job.task_id == body["task_id"]

    execution = client.get(
        f"/api/v1/tasks/{body['task_id']}/execution"
    )
    assert execution.status_code == 200
    assert execution.json()["status"] == "queued"


def test_run_endpoint_only_ensures_dispatch_when_queue_enabled(tmp_path: Path):
    config = settings(tmp_path)
    client = TestClient(create_app(config))

    created = client.post(
        "/api/v1/tasks",
        json={
            "input": {
                "goal": "Build page",
                "required_outputs": ["content"],
            },
            "context": {
                "tenant_id": "local",
                "user_id": "user",
            },
        },
    ).json()

    response = client.post(
        f"/api/v1/tasks/{created['task_id']}/run"
    )
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["execution_status"] == "queued"
