"""Small durable append-only evidence ledger (not an authorization service).

SQLite provides atomic, idempotent project-event commits. This module cannot
issue publication rights or authenticate engine/human identities. A trusted
service must be placed in front of the ledger before accepting external users.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from .validator import ContractError, validate_bundle, _canonical

GENESIS = "0" * 64


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class EvidenceLedger:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.conn = sqlite3.connect(self.path, isolation_level=None, timeout=15)
        self.conn.execute("PRAGMA busy_timeout=15000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS evidence_events (
                position INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                record_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                prior_chain_sha256 TEXT NOT NULL,
                chain_sha256 TEXT NOT NULL,
                UNIQUE(tenant_id, event_id),
                UNIQUE(tenant_id, record_id)
            );
            CREATE INDEX IF NOT EXISTS ix_evidence_project
              ON evidence_events(tenant_id, project_id, position);
        """)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "EvidenceLedger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _load_verified(self, tenant: str, project: str) -> tuple[list[dict[str, Any]], str]:
        rows = self.conn.execute("""
          SELECT payload, payload_sha256, prior_chain_sha256, chain_sha256
          FROM evidence_events WHERE tenant_id=? AND project_id=? ORDER BY position
        """, (tenant, project)).fetchall()
        records: list[dict[str, Any]] = []
        previous = GENESIS
        for raw, expected, prior, actual in rows:
            if _digest(raw) != expected or prior != previous or actual != _digest(previous + expected):
                raise ContractError("stored evidence integrity chain mismatch")
            try:
                record = json.loads(raw)
            except (json.JSONDecodeError, TypeError) as exc:
                raise ContractError("stored evidence is not valid JSON") from exc
            if record.get("tenant_id") != tenant or record.get("project_id") != project or _canonical(record) != raw:
                raise ContractError("stored evidence identity/canonicalization mismatch")
            records.append(record)
            previous = actual
        return records, previous

    def append(self, records: list[dict[str, Any]], *, evidence_root: str | Path | None = None) -> dict[str, Any]:
        """Commit entire validated batch or none. No unauthenticated approvals.

        The caller chooses an evidence_root; artifact *and* report bytes must be
        present for new evidence. The ledger cannot authenticate the producer.
        """
        if not isinstance(records, list) or not records or not isinstance(records[0], dict):
            raise ContractError("records must be a nonempty list of objects")
        if len(records) > 10000:
            raise ContractError("batch exceeds 10000 records")
        tenant, project = records[0].get("tenant_id"), records[0].get("project_id")
        if not isinstance(tenant, str) or not tenant or not isinstance(project, str) or not project:
            raise ContractError("tenant_id and project_id required")
        if any(not isinstance(r, dict) or r.get("tenant_id") != tenant or r.get("project_id") != project for r in records):
            raise ContractError("cross-tenant or cross-project batch forbidden")
        # HumanReview is never accepted by this unauthenticated module. Even
        # user-supplied reviewer IDs/flags cannot bypass this limit.
        if any(r.get("record_type") == "HumanReview" for r in records):
            raise ContractError("HumanReview ingestion requires a separate authenticated service")
        if any(r.get("record_type") in ("ArtifactRecord", "EvaluationRecord") for r in records) and evidence_root is None:
            raise ContractError("artifact/evaluation ingestion requires verified evidence bytes")
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            historical, previous = self._load_verified(tenant, project)
            # Existing and new evidence share the same lineage validation.
            result = validate_bundle(historical + records,
                                     evidence_root=Path(evidence_root) if evidence_root is not None else None)
            historical_events = {r["event_id"]: _canonical(r) for r in historical}
            historical_ids = {r["record_id"] for r in historical}
            pending: dict[str, dict[str, Any]] = {}
            for item in records:
                raw = _canonical(item)
                event = item["event_id"]
                if event in historical_events:
                    if historical_events[event] != raw:
                        raise ContractError("event_id replay conflict")
                    continue
                if item["record_id"] in historical_ids:
                    raise ContractError("record_id reused")
                pending[event] = item
            for item in pending.values():
                raw = _canonical(item)
                payload_digest = _digest(raw)
                chain_digest = _digest(previous + payload_digest)
                try:
                    self.conn.execute("""
                      INSERT INTO evidence_events
                        (tenant_id, project_id, event_id, record_id, payload, payload_sha256,
                         prior_chain_sha256, chain_sha256)
                      VALUES(?,?,?,?,?,?,?,?)
                    """, (tenant, project, item["event_id"], item["record_id"],
                          raw, payload_digest, previous, chain_digest))
                except sqlite3.IntegrityError as exc:
                    raise ContractError("global tenant event/record ID collision") from exc
                previous = chain_digest
            self.conn.execute("COMMIT")
            return {
                "ok": True, "tenant_id": tenant, "project_id": project,
                "inserted": len(pending), "replayed": len(records) - len(pending),
                "total_project_records": result["record_count"],
                "chain_head": previous, "release_approved": False,
                "warning": "chain is tamper-evident only against uncoordinated changes; not cryptographic attestation",
            }
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def read(self, tenant: str, project: str) -> list[dict[str, Any]]:
        records, _ = self._load_verified(tenant, project)
        return records
