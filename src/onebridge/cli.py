from __future__ import annotations

import argparse
import json
import os
import socket
import time

from .api import build_service, create_app
from .config import Settings
from .db import Database
from .line_messaging import LineMessagingClient, LineTaskProgressNotifier
from .qualification import qualify_adapter
from .workers.factory import build_worker_queue
from .workers.task_runner import TaskWorker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onebridge")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--poll", type=float, default=None)
    worker.add_argument(
        "--name",
        default=f"{socket.gethostname()}-{os.getpid()}",
    )

    qualify = sub.add_parser("qualify")
    qualify.add_argument(
        "--adapter",
        required=True,
        choices=["flowise", "open_design", "hermes"],
    )

    args = parser.parse_args(argv)

    if args.command == "init-db":
        settings = Settings()
        Database(settings.database_url).create_all()
        print("OneBridge database initialized")
        return 0

    if args.command == "serve":
        import uvicorn

        uvicorn.run(create_app(), host=args.host, port=args.port)
        return 0

    if args.command == "worker":
        settings = Settings()
        if not settings.task_queue_url:
            print(
                "ONEBRIDGE_TASK_QUEUE_URL is required for background worker",
            )
            return 5
        service = build_service(settings)
        queue = build_worker_queue(settings.task_queue_url)
        task_worker = TaskWorker(
            service,
            queue,
            worker_id=args.name,
            workspace_parent=settings.state_root / "task-worker",
            preserve_failed_workspace=settings.worker_preserve_failed_workspace,
            stale_after_seconds=settings.worker_stale_after_seconds,
        )
        notifier = None
        if settings.line_channel_access_token:
            notifier = LineTaskProgressNotifier(
                service=service,
                client=LineMessagingClient(
                    channel_access_token=settings.line_channel_access_token,
                ),
            )

        def notify_execution(job_id: str | None) -> None:
            if notifier is None or not job_id:
                return
            try:
                job = queue.get(job_id)
            except KeyError:
                return
            notifier(job.task_id)

        if args.once:
            result = task_worker.run_one()
            if result.worked:
                notify_execution(result.job_id)
            print(json.dumps({
                "worked": result.worked,
                "job_id": result.job_id,
                "status": result.status,
                "error": result.error,
            }, indent=2, sort_keys=True))
            return 0 if result.worked and result.status == "succeeded" else 2

        delay = max(
            0.2,
            float(
                args.poll
                if args.poll is not None
                else settings.worker_poll_seconds
            ),
        )
        while True:
            result = task_worker.run_one()
            if result.worked:
                notify_execution(result.job_id)
            else:
                time.sleep(delay)

    if args.command == "qualify":
        service = build_service(Settings())
        adapter = service.registry.get(args.adapter)
        if str(adapter.version).startswith("mock-"):
            print(json.dumps({
                "adapter_id": adapter.name,
                "version": adapter.version,
                "passed": False,
                "errors": [
                    "adapter_is_mock; configure the real adapter before qualification"
                ],
            }, indent=2, sort_keys=True))
            return 3
        result = qualify_adapter(adapter)
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0 if result.passed else 4

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
