from __future__ import annotations

from pathlib import Path

from .postgres_queue import PostgresWorkerQueue
from .queue import SQLiteWorkerQueue


def is_postgres_location(value: str | Path) -> bool:
    text = str(value)
    return text.startswith("postgresql://") or text.startswith("postgres://")


def build_worker_queue(location: str | Path):
    if is_postgres_location(location):
        return PostgresWorkerQueue(str(location))
    return SQLiteWorkerQueue(location)
