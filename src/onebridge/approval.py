from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    decision: str
    approved: bool
    reason_code: str
    message: str


def decide_approval(*, approve: bool = False, reject: bool = False) -> ApprovalDecision:
    """Fail-closed approval gate generalized from the owner's Vera approval gate."""
    if approve and reject:
        return ApprovalDecision("needs_changes", False, "conflicting_approval_flags", "Conflicting approval flags; no release authority granted.")
    if approve:
        return ApprovalDecision("approved", True, "operator_approved", "Artifact approved by operator.")
    if reject:
        return ApprovalDecision("rejected", False, "operator_rejected", "Artifact rejected by operator.")
    return ApprovalDecision("approval_required", False, "approval_missing", "Explicit approval is required.")
