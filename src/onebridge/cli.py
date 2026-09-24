from __future__ import annotations

import argparse

from .api import create_app
from .config import Settings
from .db import Database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onebridge")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
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

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
