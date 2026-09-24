from pathlib import Path

from onebridge.workers.factory import build_worker_queue, is_postgres_location
from onebridge.workers.queue import SQLiteWorkerQueue


def test_postgres_location_detection():
    assert is_postgres_location("postgresql://user:pass@localhost/db")
    assert is_postgres_location("postgres://user:pass@localhost/db")
    assert not is_postgres_location(".onebridge/workers.db")


def test_local_queue_factory_uses_sqlite(tmp_path: Path):
    queue = build_worker_queue(tmp_path / "workers.db")
    assert isinstance(queue, SQLiteWorkerQueue)
