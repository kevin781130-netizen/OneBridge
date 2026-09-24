from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ChannelProgress:
    code: str
    phase: str
    message: str
    terminal: bool
    retryable: bool
    task_id: str | None = None
    artifact_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_STATUS = {
    "queued": ("OB_TASK_QUEUED", "queued", "任務已接收，等待處理。", False, False),
    "running": ("OB_TASK_RUNNING", "running", "OneBridge 正在處理任務。", False, False),
    "waiting_approval": (
        "OB_TASK_REVIEW",
        "review",
        "產出已完成，等待人工審核。",
        False,
        False,
    ),
    "blocked": (
        "OB_TASK_BLOCKED",
        "blocked",
        "任務需要人工處理後才能繼續。",
        False,
        True,
    ),
    "failed": (
        "OB_TASK_FAILED",
        "failed",
        "任務執行失敗，可檢查錯誤後重試。",
        True,
        True,
    ),
    "succeeded": (
        "OB_TASK_SUCCEEDED",
        "completed",
        "任務已完成。",
        True,
        False,
    ),
    "cancelled": (
        "OB_TASK_CANCELLED",
        "cancelled",
        "任務已取消。",
        True,
        False,
    ),
}


def map_task_progress(
    status: str,
    *,
    task_id: str | None = None,
    artifact_count: int | None = None,
    error: str | None = None,
) -> ChannelProgress:
    code, phase, message, terminal, retryable = _STATUS.get(
        str(status),
        (
            "OB_TASK_UNKNOWN",
            "unknown",
            "任務狀態已更新，請重新查詢 OneBridge 狀態。",
            False,
            False,
        ),
    )
    if status == "failed" and error:
        # Do not forward arbitrary provider output or secrets to the channel.
        message = "任務執行失敗，可查看 OneBridge 稽核紀錄後重試。"
    return ChannelProgress(
        code=code,
        phase=phase,
        message=message,
        terminal=terminal,
        retryable=retryable,
        task_id=task_id,
        artifact_count=artifact_count,
    )


def map_release_progress(
    *,
    allowed: bool,
    task_id: str,
    reasons: list[str] | tuple[str, ...] = (),
) -> ChannelProgress:
    if allowed:
        return ChannelProgress(
            code="OB_RELEASE_READY",
            phase="release",
            message="審核已完成，可以建立發布版本。",
            terminal=False,
            retryable=False,
            task_id=task_id,
        )
    return ChannelProgress(
        code="OB_RELEASE_BLOCKED",
        phase="review",
        message="發布條件尚未完成，請先處理審核或缺少的產出。",
        terminal=False,
        retryable=True,
        task_id=task_id,
    )
