from __future__ import annotations

from typing import Any


SUCCESS = "SUCCESS"
BLOCKED = "BLOCKED"
PARTIAL = "PARTIAL"
NEEDS_REVIEW = "NEEDS_REVIEW"
USER_EXIT = "USER_EXIT"

ALLOWED_STATUSES = {SUCCESS, BLOCKED, PARTIAL, NEEDS_REVIEW, USER_EXIT}


def tests_passed(test_payload: dict[str, Any] | None) -> bool:
    payload = test_payload or {}
    status = str(payload.get("status") or payload.get("result") or "").lower()
    return status in {"passed", "pass", "success"} or payload.get("exit_code") == 0


def tests_failed(test_payload: dict[str, Any] | None) -> bool:
    payload = test_payload or {}
    status = str(payload.get("status") or payload.get("result") or "").lower()
    return status in {"failed", "fail", "error"} or (
        "exit_code" in payload and payload.get("exit_code") not in (0, None)
    )


def classify_evidence(
    *,
    execution_failed: bool = False,
    unsafe: bool = False,
    artifacts: list[dict[str, Any]] | None = None,
    verification: dict[str, Any] | None = None,
) -> str:
    if execution_failed or unsafe:
        return BLOCKED

    verify = verification or {}
    if verify:
        if tests_passed(verify):
            return SUCCESS
        if tests_failed(verify):
            return BLOCKED
        return NEEDS_REVIEW

    produced = artifacts or []
    if produced:
        approved = [item for item in produced if str(item.get("status")) == "approved"]
        rejected = [item for item in produced if str(item.get("status")) == "rejected"]
        if rejected:
            return BLOCKED
        if len(approved) == len(produced):
            return SUCCESS
        return PARTIAL

    return NEEDS_REVIEW


def next_action(status: str) -> str:
    return {
        SUCCESS: "Deliver or release the approved artifacts.",
        BLOCKED: "Inspect failed verification, policy, or approval evidence.",
        PARTIAL: "Continue the workflow or request review for remaining artifacts.",
        NEEDS_REVIEW: "Inspect artifacts and verification evidence before proceeding.",
        USER_EXIT: "Resume the task from its latest checkpoint.",
    }.get(status, "Inspect OneBridge task status and evidence.")
