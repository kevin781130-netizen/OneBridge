from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any


SCHEMA_VERSION = 1
CONTRACT = "onebridge.task.result"
MAX_RESULT_BYTES = 65_536
MAX_ARTIFACTS = 200
_WINDOWS_ABS = re.compile(r"^[A-Za-z]:/")


def safe_relative_path(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip().replace("\\", "/")[:512]
    if not text or text.startswith("/") or _WINDOWS_ABS.match(text):
        return ""
    candidate = PurePosixPath(text)
    if candidate.is_absolute() or ".." in candidate.parts:
        return ""
    return candidate.as_posix()


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_task_result(
    *,
    task_id: str,
    project_id: str,
    state: str,
    artifacts: list[dict[str, Any]],
    error_code: str | None = None,
    verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    public_artifacts: list[dict[str, Any]] = []
    for item in artifacts[:MAX_ARTIFACTS]:
        public_artifacts.append({
            "artifact_id": str(item.get("artifact_id", ""))[:120],
            "kind": str(item.get("kind", ""))[:40],
            "revision": max(0, int(item.get("revision", 0) or 0)),
            "sha256": str(item.get("sha256", ""))[:64],
            "status": str(item.get("status", ""))[:40],
            "producer_adapter": str(item.get("producer_adapter", ""))[:100],
        })

    payload = {
        "schema_version": SCHEMA_VERSION,
        "contract": CONTRACT,
        "task_id": str(task_id)[:120],
        "project_id": str(project_id)[:120],
        "state": str(state)[:40],
        "error_code": str(error_code)[:120] if error_code else None,
        "artifacts": public_artifacts,
        "verification": verification if isinstance(verification, dict) else None,
    }
    raw = _canonical(payload)
    if len(raw) > MAX_RESULT_BYTES:
        digest = hashlib.sha256(raw).hexdigest()
        payload["artifacts"] = public_artifacts[:20]
        payload["verification"] = None
        payload["truncated"] = True
        payload["full_result_sha256"] = digest
    else:
        payload["truncated"] = False
    return payload
