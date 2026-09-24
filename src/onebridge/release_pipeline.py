from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .compatibility_service import CompatibilityService
from .qualification import AdapterQualification


@dataclass(frozen=True, slots=True)
class AdapterReleaseItem:
    adapter_id: str
    version: str
    qualification_passed: bool
    qualification_errors: tuple[str, ...]
    promoted: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AdapterReleaseResult:
    passed: bool
    promoted: bool
    items: tuple[AdapterReleaseItem, ...]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "promoted": self.promoted,
            "items": [item.to_dict() for item in self.items],
            "errors": list(self.errors),
        }


class AdapterReleasePipeline:
    """Qualify a release set first, then promote the full set atomically."""

    def __init__(self, compatibility: CompatibilityService) -> None:
        self.compatibility = compatibility

    def run(
        self,
        adapter_ids: list[str] | tuple[str, ...],
        *,
        promote: bool = True,
    ) -> AdapterReleaseResult:
        requested = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in adapter_ids
                if str(value).strip()
            )
        )
        if not requested:
            raise ValueError("adapter release set cannot be empty")

        preflight: list[tuple[str, str]] = []
        preflight_errors: list[str] = []
        for adapter_id in requested:
            try:
                adapter = self.compatibility.registry.get(adapter_id)
            except KeyError:
                preflight_errors.append(
                    f"{adapter_id}:adapter_not_registered"
                )
                continue
            if str(adapter.version).startswith("mock-"):
                preflight_errors.append(
                    f"{adapter_id}:mock_adapter_not_allowed"
                )
                continue
            preflight.append((adapter.name, adapter.version))

        if preflight_errors:
            return AdapterReleaseResult(
                passed=False,
                promoted=False,
                items=(),
                errors=tuple(preflight_errors),
            )

        qualifications: list[AdapterQualification] = []
        pipeline_errors: list[str] = []
        for adapter_id, _ in preflight:
            try:
                result = self.compatibility.qualify_current(adapter_id)
            except Exception as exc:
                pipeline_errors.append(
                    f"{adapter_id}:qualification_error:"
                    f"{type(exc).__name__}:{str(exc)[:500]}"
                )
                continue
            qualifications.append(result)

        by_adapter = {
            result.adapter_id: result
            for result in qualifications
        }
        all_passed = (
            not pipeline_errors
            and len(qualifications) == len(preflight)
            and all(result.passed for result in qualifications)
        )

        promoted_versions: set[tuple[str, str]] = set()
        if all_passed and promote:
            try:
                promoted = self.compatibility.promote_many(preflight)
            except Exception as exc:
                pipeline_errors.append(
                    "promotion_error:"
                    f"{type(exc).__name__}:{str(exc)[:500]}"
                )
                all_passed = False
            else:
                promoted_versions = {
                    (item.adapter_id, item.version)
                    for item in promoted
                }

        items: list[AdapterReleaseItem] = []
        for adapter_id, version in preflight:
            qualification = by_adapter.get(adapter_id)
            items.append(
                AdapterReleaseItem(
                    adapter_id=adapter_id,
                    version=version,
                    qualification_passed=(
                        qualification.passed
                        if qualification is not None
                        else False
                    ),
                    qualification_errors=(
                        qualification.errors
                        if qualification is not None
                        else ("qualification_missing",)
                    ),
                    promoted=(
                        (adapter_id, version) in promoted_versions
                    ),
                )
            )

        return AdapterReleaseResult(
            passed=all_passed,
            promoted=(
                bool(promote)
                and all_passed
                and len(promoted_versions) == len(preflight)
            ),
            items=tuple(items),
            errors=tuple(pipeline_errors),
        )
