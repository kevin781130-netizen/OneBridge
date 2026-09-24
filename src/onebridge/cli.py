from __future__ import annotations

import argparse
import json

from .api import build_service, create_app
from .config import Settings
from .db import Database
from .qualification import qualify_adapter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onebridge")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

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
