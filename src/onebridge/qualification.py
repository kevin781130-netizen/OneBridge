from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

from .adapters.base import AdapterRequest, OneBridgeAdapter
from .adapters.capabilities import AdapterHealthReport
from .adapters.validation import validate_adapter_outputs


@dataclass(frozen=True, slots=True)
class AdapterQualification:
    adapter_id: str
    version: str
    passed: bool
    health: AdapterHealthReport
    expected_output_kinds: tuple[str, ...]
    observed_output_kinds: tuple[str, ...]
    errors: tuple[str, ...]
    duration_ms: int
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["health"] = asdict(self.health)
        return payload


def qualification_request(adapter_id: str) -> AdapterRequest:
    goals = {
        "flowise": (
            "OneBridge qualification fixture: return a minimal structured "
            "content specification for a hello-world product page."
        ),
        "open_design": (
            "OneBridge qualification fixture: create a minimal accessible "
            "hello-world HTML design artifact."
        ),
        "hermes": (
            "OneBridge qualification fixture: create a tiny hello-world "
            "implementation and a machine-readable passing test report."
        ),
    }
    return AdapterRequest(
        task_id="task_adapter_qualification",
        project_id="prj_adapter_qualification",
        goal=goals.get(
            adapter_id,
            "OneBridge qualification fixture: produce a minimal valid artifact.",
        ),
        operation="adapter.qualify",
        inputs={"qualification": True},
        artifact_inputs=[],
    )


def qualify_adapter(
    adapter: OneBridgeAdapter,
    *,
    request: AdapterRequest | None = None,
) -> AdapterQualification:
    expected = tuple(adapter.capabilities())
    started = time.monotonic()
    errors: list[str] = []
    observed: tuple[str, ...] = ()
    metadata: dict[str, Any] = {}

    health_started = time.monotonic()
    health = adapter.health()
    health_latency_ms = int((time.monotonic() - health_started) * 1000)
    health_report = AdapterHealthReport.now(
        adapter_id=adapter.name,
        version=adapter.version,
        status=health.status,
        latency_ms=health_latency_ms,
        warnings=(health.detail,) if health.detail else (),
        safe_details={"capability_count": len(expected)},
    )
    if health.status != "healthy":
        errors.append(f"adapter_health:{health.status}:{health.detail}")

    if not errors:
        try:
            result = adapter.execute(
                request or qualification_request(adapter.name)
            )
        except Exception as exc:
            errors.append(
                f"adapter_execute:{type(exc).__name__}:{str(exc)[:500]}"
            )
        else:
            observed = tuple(
                dict.fromkeys(output.kind for output in result.outputs)
            )
            metadata = {
                "external_task_id_present": bool(result.external_task_id),
                "output_count": len(result.outputs),
            }
            for kind in expected:
                report = validate_adapter_outputs(
                    result.outputs,
                    expected_kind=kind,
                )
                errors.extend(
                    f"{kind}:{message}"
                    for message in report.errors
                )

    duration_ms = int((time.monotonic() - started) * 1000)
    return AdapterQualification(
        adapter_id=adapter.name,
        version=adapter.version,
        passed=not errors,
        health=health_report,
        expected_output_kinds=expected,
        observed_output_kinds=observed,
        errors=tuple(errors),
        duration_ms=duration_ms,
        metadata=metadata,
    )
