from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    """Stable capability contract generalized from LyricGuard provider contracts."""

    adapter_id: str
    display_name: str
    version: str
    operations: frozenset[str]
    output_kinds: frozenset[str]
    network_access: bool
    requires_review: bool
    supports_cancel: bool = False
    supports_resume: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["operations"] = sorted(self.operations)
        payload["output_kinds"] = sorted(self.output_kinds)
        return payload


@dataclass(frozen=True, slots=True)
class AdapterHealthReport:
    adapter_id: str
    version: str
    status: str
    checked_at: str
    latency_ms: int
    warnings: tuple[str, ...] = ()
    safe_details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def now(
        cls,
        *,
        adapter_id: str,
        version: str,
        status: str,
        latency_ms: int,
        warnings: tuple[str, ...] = (),
        safe_details: dict[str, Any] | None = None,
    ) -> "AdapterHealthReport":
        return cls(
            adapter_id=adapter_id,
            version=version,
            status=status,
            checked_at=datetime.now(timezone.utc).isoformat(),
            latency_ms=max(0, int(latency_ms)),
            warnings=warnings,
            safe_details=safe_details or {},
        )
