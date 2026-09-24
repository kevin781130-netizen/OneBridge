from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .redaction import redact_text, redact_value


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    ts: float
    event_type: str
    actor: str
    task_id: str | None
    artifact_id: str | None
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str


class HashChainAuditLog:
    """Append-only, secret-redacted, tamper-evident audit log."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _last_hash(self) -> str:
        if not self.path.exists():
            return "0" * 64
        last = ""
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last = line
        if not last:
            return "0" * 64
        try:
            return str(json.loads(last)["event_hash"])
        except Exception as exc:
            raise RuntimeError("audit_log_corrupt") from exc

    def append(
        self,
        event_type: str,
        *,
        actor: str,
        task_id: str | None = None,
        artifact_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AuditEvent:
        previous_hash = self._last_hash()
        safe_payload = redact_value(payload or {})
        body = {
            "event_id": f"evt_{uuid.uuid4().hex}",
            "ts": time.time(),
            "event_type": redact_text(str(event_type)),
            "actor": redact_text(str(actor)),
            "task_id": redact_text(task_id) if task_id is not None else None,
            "artifact_id": redact_text(artifact_id) if artifact_id is not None else None,
            "payload": safe_payload if isinstance(safe_payload, dict) else {},
            "previous_hash": previous_hash,
        }
        event_hash = hashlib.sha256(
            previous_hash.encode("ascii") + _canonical(body)
        ).hexdigest()
        event = AuditEvent(**body, event_hash=event_hash)
        raw = (
            json.dumps(
                asdict(event),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        fd = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            os.write(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        return event

    def verify(self) -> dict[str, Any]:
        previous_hash = "0" * 64
        count = 0
        if not self.path.exists():
            return {"valid": True, "events": 0, "head": previous_hash}
        with self.path.open("r", encoding="utf-8") as handle:
            for count, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                event_hash = payload.pop("event_hash", None)
                if payload.get("previous_hash") != previous_hash:
                    return {
                        "valid": False,
                        "events": count,
                        "reason": "previous_hash_mismatch",
                    }
                expected = hashlib.sha256(
                    previous_hash.encode("ascii") + _canonical(payload)
                ).hexdigest()
                if event_hash != expected:
                    return {
                        "valid": False,
                        "events": count,
                        "reason": "event_hash_mismatch",
                    }
                previous_hash = expected
        return {"valid": True, "events": count, "head": previous_hash}
