from pathlib import Path

from onebridge.workers.queue import SQLiteWorkerQueue


def test_worker_queue_claim_finish_requeue(tmp_path: Path):
    queue = SQLiteWorkerQueue(tmp_path / "workers.db")
    job = queue.enqueue(task_id="task_1", adapter="hermes", payload={"x": 1})
    assert job.status == "queued"
    claimed = queue.claim(worker="worker-a", adapter="hermes")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "running"
    done = queue.finish(job.id, {"ok": True})
    assert done.status == "succeeded"
    assert done.result == {"ok": True}
    queued = queue.requeue(job.id)
    assert queued.status == "queued"
