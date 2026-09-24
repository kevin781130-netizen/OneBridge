from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contracts import ArtifactManifest, TaskContract


@dataclass(frozen=True, slots=True)
class ReleaseGateDecision:
    allowed: bool
    reasons: tuple[str, ...]
    selected_artifact_ids: tuple[str, ...]


def evaluate_release_gate(
    contract: TaskContract,
    artifacts: Iterable[ArtifactManifest],
    *,
    task_status: str,
) -> ReleaseGateDecision:
    """Fail-closed release policy generalized from Vera/Sonicraft gates."""

    values = list(artifacts)
    reasons: list[str] = []

    if task_status != "succeeded":
        reasons.append(f"task_not_succeeded:{task_status}")

    latest: dict[str, ArtifactManifest] = {}
    for artifact in values:
        current = latest.get(artifact.kind)
        if current is None or artifact.revision > current.revision:
            latest[artifact.kind] = artifact

    selected: list[str] = []
    for kind in contract.input.required_outputs:
        artifact = latest.get(kind)
        if artifact is None:
            reasons.append(f"required_artifact_missing:{kind}")
            continue

        selected.append(artifact.artifact_id)
        if artifact.status != "approved":
            reasons.append(
                f"required_artifact_not_approved:{kind}:{artifact.status}"
            )
        if (
            len(artifact.sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in artifact.sha256.lower())
        ):
            reasons.append(f"artifact_sha256_invalid:{artifact.artifact_id}")

    for kind, artifact in latest.items():
        if artifact.status == "rejected":
            reasons.append(f"latest_artifact_rejected:{kind}")

    if contract.policy.approval == "before_publish":
        unapproved = [
            artifact.artifact_id
            for artifact in latest.values()
            if artifact.status != "approved"
        ]
        if unapproved:
            reasons.append("human_approval_incomplete")

    return ReleaseGateDecision(
        allowed=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        selected_artifact_ids=tuple(selected),
    )
