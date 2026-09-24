from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class QueueJob:
    id: str
    task_id: str
    adapter: str
    status: str
    payload: dict[str, Any]
    created_at: float
    updated_at: float
    worker: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SQLiteWorkerQueue:
    """Small durable worker queue extracted and generalized from CutPilot's queue pattern."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_jobs(
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    adapter TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    worker TEXT,
                    error TEXT,
                    result_json TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_worker_jobs_claim ON worker_jobs(status, adapter, created_at)"
            )

    def enqueue(self, *, task_id: str, adapter: str, payload: dict[str, Any], job_id: str | None = None) -> QueueJob:
        now = time.time()
        jid = job_id or f"job_{uuid.uuid4().hex}"
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO worker_jobs(id,task_id,adapter,status,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (jid, task_id, adapter, "queued", json.dumps(payload, ensure_ascii=False), now, now),
            )
        return self.get(jid)

    def enqueue_idempotent(
        self,
        *,
        task_id: str,
        adapter: str,
        payload: dict[str, Any],
        job_id: str,
    ) -> tuple[QueueJob, bool]:
        now = time.time()
        with self._connect() as conn:
            changed = conn.execute(
                """
                INSERT OR IGNORE INTO worker_jobs(
                    id,task_id,adapter,status,payload_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?)
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
            ).rowcount
        return self.get(job_id), changed == 1

    def get(self, job_id: str) -> QueueJob:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM worker_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._row(row)

    def claim(self, *, worker: str, adapter: str) -> QueueJob | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM worker_jobs WHERE status='queued' AND adapter=? ORDER BY created_at LIMIT 1",
                (adapter,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            now = time.time()
            changed = conn.execute(
                "UPDATE worker_jobs SET status='running',worker=?,updated_at=? WHERE id=? AND status='queued'",
                (worker, now, row["id"]),
            ).rowcount
            conn.execute("COMMIT")
        if not changed:
            return None
        return self.get(row["id"])

    def heartbeat(
        self,
        job_id: str,
        *,
        worker: str,
    ) -> bool:
        with self._connect() as conn:
            changed = conn.execute(
                """
                UPDATE worker_jobs
                SET updated_at=?
                WHERE id=? AND status='running' AND worker=?
                """,
                (time.time(), job_id, worker),
            ).rowcount
        return changed == 1

    def requeue_stale(
        self,
        *,
        adapter: str,
        stale_after_seconds: float,
    ) -> int:
        cutoff = time.time() - max(1.0, float(stale_after_seconds))
        with self._connect() as conn:
            changed = conn.execute(
                """
                UPDATE worker_jobs
                SET status='queued',worker=NULL,error=NULL,result_json=NULL,updated_at=?
                WHERE adapter=? AND status='running' AND updated_at<?
                """,
                (time.time(), adapter, cutoff),
            ).rowcount
        return int(changed)

    def finish(self, job_id: str, result: dict[str, Any]) -> QueueJob:
        with self._connect() as conn:
            conn.execute(
                "UPDATE worker_jobs SET status='succeeded',result_json=?,error=NULL,updated_at=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), time.time(), job_id),
            )
        return self.get(job_id)

    def fail(self, job_id: str, error: str) -> QueueJob:
        with self._connect() as conn:
            conn.execute(
                "UPDATE worker_jobs SET status='failed',error=?,updated_at=? WHERE id=?",
                (str(error)[:8000], time.time(), job_id),
            )
        return self.get(job_id)

    def requeue(self, job_id: str) -> QueueJob:
        with self._connect() as conn:
            conn.execute(
                "UPDATE worker_jobs SET status='queued',worker=NULL,error=NULL,result_json=NULL,updated_at=? WHERE id=?",
                (time.time(), job_id),
            )
        return self.get(job_id)

    @staticmethod
    def _row(row: sqlite3.Row) -> QueueJob:
        return QueueJob(
            id=row["id"],
            task_id=row["task_id"],
            adapter=row["adapter"],
            status=row["status"],
            payload=json.loads(row["payload_json"] or "{}"),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            worker=row["worker"],
            error=row["error"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
        )
