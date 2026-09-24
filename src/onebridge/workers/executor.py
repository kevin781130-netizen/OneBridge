from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from onebridge.redaction import redact_text

from .queue import QueueJob
from .workspace import WorkerWorkspace


class WorkerQueueProtocol(Protocol):
    def claim(self, *, worker: str, adapter: str) -> QueueJob | None: ...
    def finish(self, job_id: str, result: dict[str, Any]) -> QueueJob: ...
    def fail(self, job_id: str, error: str) -> QueueJob: ...


WorkerHandler = Callable[[QueueJob, WorkerWorkspace], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class WorkerExecution:
    worked: bool
    job_id: str | None
    status: str
    error: str | None = None


class WorkerExecutor:
    """Claim -> isolated workspace -> execute -> finish/fail worker primitive.

    Generalized from CutPilot's worker loop. Queue persistence and job-specific
    execution stay separate so Hermes or any future adapter can plug in without
    owning OneBridge task state.
    """

    def __init__(
        self,
        queue: WorkerQueueProtocol,
        *,
        adapter: str,
        worker_id: str | None = None,
        workspace_parent: str | Path | None = None,
        preserve_failed_workspace: bool = False,
    ) -> None:
        if not adapter:
            raise ValueError("worker_adapter_required")
        self.queue = queue
        self.adapter = adapter
        self.worker_id = worker_id or socket.gethostname()
        self.workspace_parent = workspace_parent
        self.preserve_failed_workspace = preserve_failed_workspace

    def run_one(self, handler: WorkerHandler) -> WorkerExecution:
        job = self.queue.claim(
            worker=self.worker_id,
            adapter=self.adapter,
        )
        if job is None:
            return WorkerExecution(False, None, "idle")

        workspace: WorkerWorkspace | None = None
        try:
            workspace = WorkerWorkspace(
                job.id,
                parent=self.workspace_parent,
                preserve=False,
            )
            with workspace:
                try:
                    result = handler(job, workspace)
                    if not isinstance(result, dict):
                        raise TypeError("worker_handler_result_must_be_object")
                    finished = self.queue.finish(job.id, result)
                except Exception:
                    if self.preserve_failed_workspace:
                        workspace.preserve = True
                    raise
            return WorkerExecution(True, job.id, finished.status)
        except Exception as exc:
            safe_error = redact_text(
                f"{type(exc).__name__}: {exc}"
            )[:8000]
            failed = self.queue.fail(job.id, safe_error)
            return WorkerExecution(
                True,
                job.id,
                failed.status,
                error=safe_error,
            )
