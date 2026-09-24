from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from .adapters.capabilities import AdapterCapabilities, AdapterHealthReport


@dataclass(frozen=True, slots=True)
class CompatibilityEntry:
    adapter_id: str
    version: str
    state: str  # active | candidate | blocked
    contract_version: str = "1.0"
    notes: str = ""


class CompatibilityMatrix:
    """Control-plane compatibility registry with explicit candidate promotion.

    This keeps upstream version churn outside OneBridge Core. A candidate may be
    probed without replacing the active entry, matching the blue/green boundary
    described in the OneBridge plan.
    """

    def __init__(self, entries: Iterable[CompatibilityEntry] = ()) -> None:
        self._entries: dict[tuple[str, str], CompatibilityEntry] = {
            (entry.adapter_id, entry.version): entry for entry in entries
        }

    def add(self, entry: CompatibilityEntry) -> None:
        if entry.state not in {"active", "candidate", "blocked"}:
            raise ValueError("invalid compatibility state")
        self._entries[(entry.adapter_id, entry.version)] = entry

    def list(self, adapter_id: str | None = None) -> list[CompatibilityEntry]:
        values = list(self._entries.values())
        if adapter_id is not None:
            values = [entry for entry in values if entry.adapter_id == adapter_id]
        return sorted(values, key=lambda item: (item.adapter_id, item.version))

    def active(self, adapter_id: str) -> CompatibilityEntry | None:
        active = [entry for entry in self._entries.values() if entry.adapter_id == adapter_id and entry.state == "active"]
        if len(active) > 1:
            raise RuntimeError(f"multiple active versions for adapter {adapter_id}")
        return active[0] if active else None

    def promote(self, adapter_id: str, version: str, *, health: AdapterHealthReport) -> CompatibilityEntry:
        key = (adapter_id, version)
        candidate = self._entries.get(key)
        if candidate is None:
            raise KeyError(key)
        if candidate.state != "candidate":
            raise ValueError("only candidate versions can be promoted")
        if health.adapter_id != adapter_id or health.version != version:
            raise ValueError("health report does not match candidate")
        if health.status != "healthy":
            raise ValueError("candidate must be healthy before promotion")

        for other_key, entry in list(self._entries.items()):
            if entry.adapter_id == adapter_id and entry.state == "active":
                self._entries[other_key] = CompatibilityEntry(
                    adapter_id=entry.adapter_id,
                    version=entry.version,
                    state="candidate",
                    contract_version=entry.contract_version,
                    notes="previous active version",
                )
        promoted = CompatibilityEntry(
            adapter_id=candidate.adapter_id,
            version=candidate.version,
            state="active",
            contract_version=candidate.contract_version,
            notes=candidate.notes,
        )
        self._entries[key] = promoted
        return promoted

    def as_dict(self) -> list[dict]:
        return [asdict(entry) for entry in self.list()]


def validate_capabilities(entry: CompatibilityEntry, capabilities: AdapterCapabilities) -> list[str]:
    errors: list[str] = []
    if capabilities.adapter_id != entry.adapter_id:
        errors.append("adapter_id_mismatch")
    if capabilities.version != entry.version:
        errors.append("adapter_version_mismatch")
    if entry.contract_version != "1.0":
        errors.append("unsupported_contract_version")
    if not capabilities.operations:
        errors.append("no_operations")
    if not capabilities.output_kinds:
        errors.append("no_output_kinds")
    return errors
