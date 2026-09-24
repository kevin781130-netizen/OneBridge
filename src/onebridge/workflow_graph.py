from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    index: int
    name: str
    adapter: str
    output_kind: str
    depends_on: tuple[int, ...] = ()


def step_digest(step: WorkflowStep) -> str:
    payload = json.dumps(asdict(step), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def step_map(steps: Iterable[WorkflowStep]) -> dict[int, WorkflowStep]:
    values = list(steps)
    mapping = {step.index: step for step in values}
    if len(mapping) != len(values):
        raise ValueError("workflow_step_index_duplicate")
    return mapping


def ancestor_indices(steps: Iterable[WorkflowStep], step: WorkflowStep) -> tuple[int, ...]:
    by_index = step_map(steps)
    result: set[int] = set()
    visiting: set[int] = set()

    def visit(index: int) -> None:
        if index in result:
            return
        if index in visiting:
            raise ValueError("workflow_cycle_detected")
        parent = by_index.get(index)
        if parent is None:
            raise ValueError("workflow_dependency_missing")
        visiting.add(index)
        for dependency in parent.depends_on:
            visit(dependency)
        visiting.remove(index)
        result.add(index)

    for dependency in step.depends_on:
        visit(dependency)
    return tuple(sorted(result))


def execution_waves(steps: Iterable[WorkflowStep]) -> list[list[WorkflowStep]]:
    """Dependency waves extracted from Vera's child-task graph scheduler."""

    values = sorted(list(steps), key=lambda item: item.index)
    by_index = step_map(values)
    for step in values:
        for dependency in step.depends_on:
            if dependency not in by_index:
                raise ValueError("workflow_dependency_missing")
        if step.index in step.depends_on:
            raise ValueError("workflow_cycle_detected")

    completed: set[int] = set()
    remaining = {step.index: step for step in values}
    waves: list[list[WorkflowStep]] = []
    while remaining:
        ready = [
            step
            for _, step in sorted(remaining.items())
            if set(step.depends_on) <= completed
        ]
        if not ready:
            raise ValueError("workflow_cycle_detected")
        waves.append(ready)
        for step in ready:
            completed.add(step.index)
            remaining.pop(step.index, None)
    return waves


def default_pipeline(required_outputs: Iterable[str]) -> list[WorkflowStep]:
    requested = set(required_outputs)
    steps: list[WorkflowStep] = []
    previous: int | None = None
    mapping = [
        ("content", "flowise"),
        ("design", "open_design"),
        ("code", "hermes"),
        ("test_report", "hermes"),
    ]
    for kind, adapter in mapping:
        if kind not in requested:
            continue
        index = len(steps)
        dependencies = (previous,) if previous is not None else ()
        steps.append(
            WorkflowStep(
                index=index,
                name=f"produce_{kind}",
                adapter=adapter,
                output_kind=kind,
                depends_on=dependencies,
            )
        )
        previous = index
    unknown = requested - {kind for kind, _ in mapping}
    if unknown:
        raise ValueError(f"unsupported workflow outputs: {sorted(unknown)}")
    return steps
