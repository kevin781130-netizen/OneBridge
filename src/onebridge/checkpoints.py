from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .workflow_graph import WorkflowStep, step_digest


SCHEMA_VERSION = 1
MAX_CHECKPOINT_BYTES = 128 * 1024


def _payload_digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class StepCheckpoint:
    step_index: int
    step_digest: str
    input_lineage: tuple[str, ...]
    output_artifact_ids: tuple[str, ...]
    metadata: dict[str, Any]
    payload_digest: str


class CheckpointStore:
    """Digest-validated step checkpoints generalized from Vera's task graph checkpoints."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id: str, step_index: int) -> Path:
        safe_task = "".join(ch for ch in task_id if ch.isalnum() or ch in "._-")
        if safe_task != task_id or not safe_task:
            raise ValueError("invalid task_id")
        directory = (self.root / safe_task).resolve()
        if self.root != directory and self.root not in directory.parents:
            raise ValueError("checkpoint path escapes root")
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"step-{step_index}.json"

    def save(
        self,
        task_id: str,
        *,
        step: WorkflowStep,
        input_lineage: tuple[str, ...],
        output_artifact_ids: tuple[str, ...],
        metadata: dict[str, Any] | None = None,
    ) -> StepCheckpoint:
        body = {
            "schema_version": SCHEMA_VERSION,
            "step_index": step.index,
            "step_digest": step_digest(step),
            "input_lineage": list(input_lineage),
            "output_artifact_ids": list(output_artifact_ids),
            "metadata": metadata or {},
        }
        body["payload_digest"] = _payload_digest(body)
        raw = (json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(raw) > MAX_CHECKPOINT_BYTES:
            raise ValueError("checkpoint_too_large")
        target = self._path(task_id, step.index)
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
        temp.write_bytes(raw)
        try:
            temp.chmod(0o600)
        except OSError:
            pass
        os.replace(temp, target)
        return self.load(task_id, step=step, input_lineage=input_lineage)  # type: ignore[return-value]

    def load(
        self,
        task_id: str,
        *,
        step: WorkflowStep,
        input_lineage: tuple[str, ...],
    ) -> StepCheckpoint | None:
        target = self._path(task_id, step.index)
        if not target.exists() or target.is_symlink():
            return None
        try:
            if target.stat().st_size > MAX_CHECKPOINT_BYTES:
                return None
            payload = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return None
        if payload.get("schema_version") != SCHEMA_VERSION:
            return None
        if payload.get("step_index") != step.index:
            return None
        if payload.get("step_digest") != step_digest(step):
            return None
        if payload.get("input_lineage") != list(input_lineage):
            return None
        digest = payload.pop("payload_digest", None)
        if digest != _payload_digest(payload):
            return None
        outputs = payload.get("output_artifact_ids")
        if not isinstance(outputs, list) or not all(isinstance(item, str) for item in outputs):
            return None
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            return None
        return StepCheckpoint(
            step_index=step.index,
            step_digest=str(payload["step_digest"]),
            input_lineage=tuple(str(x) for x in payload["input_lineage"]),
            output_artifact_ids=tuple(outputs),
            metadata=metadata,
            payload_digest=str(digest),
        )
