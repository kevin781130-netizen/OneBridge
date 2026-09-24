from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


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
    def __init__(self) -> None:
        self._items: dict[str, OneBridgeAdapter] = {}

    def register(self, adapter: OneBridgeAdapter) -> None:
        self._items[adapter.name] = adapter

    def get(self, name: str) -> OneBridgeAdapter:
        try:
            return self._items[name]
        except KeyError as exc:
            raise KeyError(f"adapter not registered: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._items)
