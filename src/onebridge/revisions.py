from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True, slots=True)
class Revision:
    id: int
    label: str
    digest: str
    payload: dict[str, Any]
    created_at: str
    parent_revision: int | None


class RevisionWorkspace:
    """Generic revision/rollback primitive extracted from FlowCraft's workflow workspace."""

    def __init__(self, payload: dict[str, Any], workspace_id: str | None = None) -> None:
        self.id = workspace_id or f"rev_{uuid4().hex}"
        self._current = deepcopy(payload)
        self._revisions: list[Revision] = []
        self.snapshot("initial")

    @property
    def current(self) -> dict[str, Any]:
        return deepcopy(self._current)

    @property
    def revision_id(self) -> int:
        return self._revisions[-1].id

    def replace(self, payload: dict[str, Any], *, label: str = "update") -> Revision:
        self._current = deepcopy(payload)
        return self.snapshot(label)

    def snapshot(self, label: str) -> Revision:
        parent = self._revisions[-1].id if self._revisions else None
        data = deepcopy(self._current)
        revision = Revision(
            id=len(self._revisions),
            label=label,
            digest=hashlib.sha256(_canonical(data)).hexdigest(),
            payload=data,
            created_at=datetime.now(timezone.utc).isoformat(),
            parent_revision=parent,
        )
        self._revisions.append(revision)
        return revision

    def revisions(self) -> list[Revision]:
        return list(self._revisions)

    def get(self, revision_id: int) -> Revision:
        if revision_id < 0 or revision_id >= len(self._revisions):
            raise KeyError(revision_id)
        return self._revisions[revision_id]

    def rollback(self, revision_id: int) -> Revision:
        target = self.get(revision_id)
        self._current = deepcopy(target.payload)
        return self.snapshot(f"rollback:{revision_id}")
