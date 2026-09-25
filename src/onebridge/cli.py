from __future__ import annotations

import argparse
import json
import os
import socket
import time

from .api import build_service, create_app
from .compatibility_service import CompatibilityService
from .config import Settings
from .db import Database
from .line_messaging import LineMessagingClient, LineTaskProgressNotifier
from .qualification import qualify_adapter
from .release_pipeline import AdapterReleasePipeline
from .workers.factory import build_worker_queue
from .workers.task_runner import TaskWorker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onebridge")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("migrate-db")
    sub.add_parser("schema-status")
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

    release_adapters = sub.add_parser("release-adapters")
    release_adapters.add_argument(
        "--adapters",
        default="flowise,open_design,hermes",
        help="Comma-separated adapter ids to qualify as one release set.",
    )
    release_adapters.add_argument(
        "--qualify-only",
        action="store_true",
        help="Store qualification evidence without promoting the release set.",
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
        status = Database(settings.database_url).migrate()
        print(json.dumps(status.to_dict(), indent=2, sort_keys=True))
        return 0

    if args.command == "migrate-db":
        settings = Settings()
        status = Database(settings.database_url).migrate()
        print(json.dumps(status.to_dict(), indent=2, sort_keys=True))
        return 0

    if args.command == "schema-status":
        settings = Settings()
        status = Database(settings.database_url).schema_status()
        print(json.dumps(status.to_dict(), indent=2, sort_keys=True))
        return 0 if status.up_to_date else 8

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

    if args.command == "release-adapters":
        settings = Settings()
        # Release qualification is the operation that establishes the active
        # evidence, so it must be allowed to start before the strict startup
        # gate is enabled for normal API/worker processes.
        settings.require_qualified_adapters = False
        service = build_service(settings)
        compatibility = CompatibilityService(
            service.db,
            service.registry,
        )
        adapter_ids = [
            item.strip()
            for item in str(args.adapters).split(",")
            if item.strip()
        ]
        result = AdapterReleasePipeline(compatibility).run(
            adapter_ids,
            promote=not args.qualify_only,
        )
        audit = getattr(service, "audit", None)
        if audit is not None:
            audit.append(
                "adapter_release.completed",
                actor="onebridge-cli",
                payload={
                    "passed": result.passed,
                    "promoted": result.promoted,
                    "adapters": [
                        {
                            "adapter_id": item.adapter_id,
                            "version": item.version,
                            "qualification_passed": item.qualification_passed,
                            "promoted": item.promoted,
                        }
                        for item in result.items
                    ],
                },
            )
        print(json.dumps(
            result.to_dict(),
            indent=2,
            sort_keys=True,
        ))
        return 0 if result.passed else 4

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
