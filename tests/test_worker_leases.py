import time
from pathlib import Path

from onebridge.workers.queue import SQLiteWorkerQueue


def test_stale_running_claim_is_requeued(tmp_path: Path):
    queue = SQLiteWorkerQueue(tmp_path / "queue.sqlite")
    job = queue.enqueue(
        task_id="task_1",
        adapter="onebridge_task",
        payload={"task_id": "task_1"},
    )
    claimed = queue.claim(
        worker="worker-a",
        adapter="onebridge_task",
    )
    assert claimed is not None
    assert claimed.status == "running"

    with queue._connect() as connection:
        connection.execute(
            "UPDATE worker_jobs SET updated_at=? WHERE id=?",
            (time.time() - 7200, job.id),
        )

    recovered = queue.requeue_stale(
        adapter="onebridge_task",
        stale_after_seconds=3600,
    )
    assert recovered == 1
    assert queue.get(job.id).status == "queued"


def test_fresh_running_claim_is_not_requeued(tmp_path: Path):
    queue = SQLiteWorkerQueue(tmp_path / "queue.sqlite")
    queue.enqueue(
        task_id="task_1",
        adapter="onebridge_task",
        payload={"task_id": "task_1"},
    )
    claimed = queue.claim(
        worker="worker-a",
        adapter="onebridge_task",
    )
    assert claimed is not None

    recovered = queue.requeue_stale(
        adapter="onebridge_task",
        stale_after_seconds=3600,
    )
    assert recovered == 0
    assert queue.get(claimed.id).status == "running"
