from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .queue import QueueJob


class PostgresWorkerQueue:
    """Distributed worker queue using PostgreSQL FOR UPDATE SKIP LOCKED."""

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL worker queue requires the 'postgres' extra"
            ) from exc
        self.psycopg = psycopg
        self.dsn = dsn
        self._init()

    def _connect(self):
        return self.psycopg.connect(self.dsn)

    def _init(self) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS onebridge_worker_jobs(
                        id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        adapter TEXT NOT NULL,
                        status TEXT NOT NULL,
                        payload_json JSONB NOT NULL,
                        created_at DOUBLE PRECISION NOT NULL,
                        updated_at DOUBLE PRECISION NOT NULL,
                        worker TEXT,
                        error TEXT,
                        result_json JSONB
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_onebridge_worker_claim
                    ON onebridge_worker_jobs(status, adapter, created_at)
                    """
                )
            connection.commit()

    @staticmethod
    def _json_value(value: Any, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return value
        return json.loads(value)

    @classmethod
    def _row(cls, row) -> QueueJob:
        return QueueJob(
            id=str(row[0]),
            task_id=str(row[1]),
            adapter=str(row[2]),
            status=str(row[3]),
            payload=cls._json_value(row[4], {}),
            created_at=float(row[5]),
            updated_at=float(row[6]),
            worker=str(row[7]) if row[7] is not None else None,
            error=str(row[8]) if row[8] is not None else None,
            result=cls._json_value(row[9], None),
        )

    def enqueue(
        self,
        *,
        task_id: str,
        adapter: str,
        payload: dict[str, Any],
        job_id: str | None = None,
    ) -> QueueJob:
        now = time.time()
        job_id = job_id or f"job_{uuid.uuid4().hex}"
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO onebridge_worker_jobs(
                        id, task_id, adapter, status, payload_json,
                        created_at, updated_at
                    ) VALUES(%s,%s,%s,%s,%s::jsonb,%s,%s)
                    """,
                    (
                        job_id,
                        task_id,
                        adapter,
                        "queued",
                        json.dumps(payload, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
            connection.commit()
        return self.get(job_id)

    def enqueue_idempotent(
        self,
        *,
        task_id: str,
        adapter: str,
        payload: dict[str, Any],
        job_id: str,
    ) -> tuple[QueueJob, bool]:
        now = time.time()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO onebridge_worker_jobs(
                        id, task_id, adapter, status, payload_json,
                        created_at, updated_at
                    ) VALUES(%s,%s,%s,%s,%s::jsonb,%s,%s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        job_id,
                        task_id,
                        adapter,
                        "queued",
                        json.dumps(payload, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                created = cursor.rowcount == 1
            connection.commit()
        return self.get(job_id), created

    def get(self, job_id: str) -> QueueJob:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id,task_id,adapter,status,payload_json,
                           created_at,updated_at,worker,error,result_json
                    FROM onebridge_worker_jobs
                    WHERE id=%s
                    """,
                    (job_id,),
                )
                row = cursor.fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._row(row)

    def claim(self, *, worker: str, adapter: str) -> QueueJob | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id
                    FROM onebridge_worker_jobs
                    WHERE status='queued' AND adapter=%s
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """,
                    (adapter,),
                )
                row = cursor.fetchone()
                if row is None:
                    connection.commit()
                    return None
                job_id = str(row[0])
                cursor.execute(
                    """
                    UPDATE onebridge_worker_jobs
                    SET status='running', worker=%s, updated_at=%s
                    WHERE id=%s
                    """,
                    (worker, time.time(), job_id),
                )
            connection.commit()
        return self.get(job_id)

    def finish(self, job_id: str, result: dict[str, Any]) -> QueueJob:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE onebridge_worker_jobs
                    SET status='succeeded',
                        result_json=%s::jsonb,
                        error=NULL,
                        updated_at=%s
                    WHERE id=%s
                    """,
                    (json.dumps(result, ensure_ascii=False), time.time(), job_id),
                )
            connection.commit()
        return self.get(job_id)

    def fail(self, job_id: str, error: str) -> QueueJob:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE onebridge_worker_jobs
                    SET status='failed', error=%s, updated_at=%s
                    WHERE id=%s
                    """,
                    (str(error)[:8000], time.time(), job_id),
                )
            connection.commit()
        return self.get(job_id)

    def requeue(self, job_id: str) -> QueueJob:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE onebridge_worker_jobs
                    SET status='queued',
                        worker=NULL,
                        error=NULL,
                        result_json=NULL,
                        updated_at=%s
                    WHERE id=%s
                    """,
                    (time.time(), job_id),
                )
            connection.commit()
        return self.get(job_id)
