from __future__ import annotations

import hashlib
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from onebridge.service import OneBridgeService

from .executor import WorkerExecution, WorkerExecutor
from .queue import QueueJob


TASK_QUEUE_ADAPTER = "onebridge_task"


class TaskQueueProtocol(Protocol):
    def enqueue_idempotent(
        self,
        *,
        task_id: str,
        adapter: str,
        payload: dict[str, Any],
        job_id: str,
    ) -> tuple[QueueJob, bool]: ...

    def get(self, job_id: str) -> QueueJob: ...

    def requeue(self, job_id: str) -> QueueJob: ...


def task_job_id(task_id: str) -> str:
    digest = hashlib.sha256(str(task_id).encode("utf-8")).hexdigest()[:32]
    return f"taskrun_{digest}"


@dataclass(frozen=True, slots=True)
class TaskDispatch:
    job_id: str
    task_id: str
    status: str
    created: bool


class TaskScheduler:
    """Idempotently maps one OneBridge task to one durable queue job."""

    def __init__(self, queue: TaskQueueProtocol) -> None:
        self.queue = queue

    def schedule(
        self,
        task_id: str,
        *,
        restart: bool = False,
    ) -> TaskDispatch:
        job_id = task_job_id(task_id)
        job, created = self.queue.enqueue_idempotent(
            task_id=task_id,
            adapter=TASK_QUEUE_ADAPTER,
            payload={"task_id": task_id},
            job_id=job_id,
        )
        if (
            restart
            and not created
            and job.status in {"failed", "succeeded"}
        ):
            job = self.queue.requeue(job_id)
        return TaskDispatch(
            job_id=job.id,
            task_id=job.task_id,
            status=job.status,
            created=created,
        )

    def status(self, task_id: str) -> QueueJob:
        return self.queue.get(task_job_id(task_id))


class TaskWorker:
    """Background worker that executes the durable OneBridge service pipeline."""

    def __init__(
        self,
        service: OneBridgeService,
        queue,
        *,
        worker_id: str | None = None,
        workspace_parent: str | Path | None = None,
        preserve_failed_workspace: bool = False,
    ) -> None:
        self.service = service
        self.executor = WorkerExecutor(
            queue,
            adapter=TASK_QUEUE_ADAPTER,
            worker_id=worker_id or socket.gethostname(),
            workspace_parent=workspace_parent,
            preserve_failed_workspace=preserve_failed_workspace,
        )

    def _handle(self, job: QueueJob, workspace) -> dict[str, Any]:
        task_id = str(job.payload.get("task_id") or job.task_id)
        if task_id != job.task_id:
            raise ValueError("queued task_id does not match job task_id")
        result = self.service.run(task_id)
        return {
            "task_id": task_id,
            "task_status": result["status"],
            "attempt": result["attempt"],
        }

    def run_one(self) -> WorkerExecution:
        return self.executor.run_one(self._handle)

    def run_forever(self, *, poll_seconds: float = 2.0) -> None:
        delay = max(0.2, float(poll_seconds))
        while True:
            result = self.run_one()
            if not result.worked:
                time.sleep(delay)
