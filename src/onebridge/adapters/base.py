from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol


_ADAPTER_ID = re.compile(r"^[a-z][a-z0-9._-]{1,99}$")


@dataclass(frozen=True, slots=True)
class AdapterHealth:
    status: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class AdapterRequest:
    task_id: str
    project_id: str
    goal: str
    operation: str
    inputs: dict[str, Any] = field(default_factory=dict)
    artifact_inputs: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AdapterOutput:
    kind: str
    media_type: str
    content: bytes
    filename: str


@dataclass(frozen=True, slots=True)
class AdapterResult:
    external_task_id: str
    outputs: list[AdapterOutput]
    metadata: dict[str, Any] = field(default_factory=dict)


class OneBridgeAdapter(Protocol):
    name: str
    version: str

    def health(self) -> AdapterHealth: ...
    def capabilities(self) -> list[str]: ...
    def execute(self, request: AdapterRequest) -> AdapterResult: ...
    def cancel(self, external_task_id: str) -> None: ...


class AdapterRegistry:
    """Validated adapter registry generalized from LyricGuard's provider registry."""

    def __init__(self) -> None:
        self._items: dict[str, OneBridgeAdapter] = {}

    def register(self, adapter: OneBridgeAdapter) -> None:
        name = str(adapter.name or "")
        version = str(adapter.version or "")
        if not _ADAPTER_ID.fullmatch(name):
            raise ValueError(f"invalid adapter name: {name!r}")
        if not version or len(version) > 100:
            raise ValueError(f"invalid adapter version for {name}")
        if name in self._items:
            raise ValueError(f"adapter already registered: {name}")
        capabilities = adapter.capabilities()
        if not capabilities or len(capabilities) != len(set(capabilities)):
            raise ValueError(f"invalid capabilities for adapter: {name}")
        self._items[name] = adapter

    def unregister(self, name: str) -> None:
        self._items.pop(name, None)

    def get(self, name: str) -> OneBridgeAdapter:
        try:
            return self._items[name]
        except KeyError as exc:
            raise KeyError(f"adapter not registered: {name}") from exc

    def list(self) -> tuple[OneBridgeAdapter, ...]:
        return tuple(self._items[name] for name in sorted(self._items))

    def names(self) -> list[str]:
        return [adapter.name for adapter in self.list()]

    def health_all(self) -> dict[str, AdapterHealth]:
        return {adapter.name: adapter.health() for adapter in self.list()}
