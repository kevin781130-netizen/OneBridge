from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .contracts import ApprovalRequest, TaskContract
from .openclaw_shim import OpenClawOneBridgeShim


class SubmitToolInput(BaseModel):
    goal: str = Field(min_length=1, max_length=20_000)
    required_outputs: list[str] = Field(default_factory=list)
    tenant_id: str = Field(min_length=1, max_length=200)
    user_id: str = Field(min_length=1, max_length=200)
    channel: str = Field(default="openclaw", min_length=1, max_length=100)
    conversation_id: str | None = Field(default=None, max_length=300)
    operation: str = Field(default="project.create", min_length=1, max_length=200)
    approval: str = Field(default="before_publish")
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskIdInput(BaseModel):
    task_id: str = Field(min_length=1, max_length=200)


class ApproveToolInput(TaskIdInput):
    artifact_ids: list[str] = Field(min_length=1)
    decision: str
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(default="", max_length=4000)


OPENCLAW_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "onebridge_submit",
        "description": (
            "Submit a production task to OneBridge. OneBridge owns workflow state, "
            "artifacts, approvals and execution history."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["goal", "required_outputs", "tenant_id", "user_id"],
            "properties": {
                "goal": {"type": "string"},
                "required_outputs": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["content", "design", "code", "test_report"],
                    },
                },
                "tenant_id": {"type": "string"},
                "user_id": {"type": "string"},
                "channel": {"type": "string", "default": "openclaw"},
                "conversation_id": {"type": ["string", "null"]},
                "operation": {"type": "string", "default": "project.create"},
                "approval": {
                    "type": "string",
                    "enum": ["none", "before_publish"],
                    "default": "before_publish",
                },
                "metadata": {"type": "object"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "onebridge_status",
        "description": "Read the current OneBridge task state.",
        "inputSchema": {
            "type": "object",
            "required": ["task_id"],
            "properties": {"task_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "onebridge_artifacts",
        "description": "List immutable artifacts produced for a OneBridge task.",
        "inputSchema": {
            "type": "object",
            "required": ["task_id"],
            "properties": {"task_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "onebridge_approve",
        "description": "Approve or reject selected OneBridge task artifacts.",
        "inputSchema": {
            "type": "object",
            "required": ["task_id", "artifact_ids", "decision", "actor"],
            "properties": {
                "task_id": {"type": "string"},
                "artifact_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "decision": {
                    "type": "string",
                    "enum": ["approve", "reject"],
                },
                "actor": {"type": "string"},
                "reason": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "onebridge_retry",
        "description": "Requeue a failed or blocked OneBridge task.",
        "inputSchema": {
            "type": "object",
            "required": ["task_id"],
            "properties": {"task_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "onebridge_cancel",
        "description": "Cancel a OneBridge task that has not completed.",
        "inputSchema": {
            "type": "object",
            "required": ["task_id"],
            "properties": {"task_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
)


class OpenClawToolDispatcher:
    """Native tool dispatcher around the stateless OneBridge shim."""

    def __init__(self, shim: OpenClawOneBridgeShim) -> None:
        self.shim = shim

    def definitions(self) -> list[dict[str, Any]]:
        return [dict(item) for item in OPENCLAW_TOOLS]

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> Any:
        if name == "onebridge_submit":
            value = SubmitToolInput.model_validate(arguments)
            contract = TaskContract.model_validate({
                "operation": value.operation,
                "input": {
                    "goal": value.goal,
                    "required_outputs": value.required_outputs,
                },
                "context": {
                    "tenant_id": value.tenant_id,
                    "user_id": value.user_id,
                    "channel": value.channel,
                    "conversation_id": value.conversation_id,
                },
                "policy": {"approval": value.approval},
                "metadata": value.metadata,
            })
            return self.shim.submit(contract)

        if name == "onebridge_status":
            value = TaskIdInput.model_validate(arguments)
            return self.shim.status(value.task_id)

        if name == "onebridge_artifacts":
            value = TaskIdInput.model_validate(arguments)
            return self.shim.artifacts(value.task_id)

        if name == "onebridge_approve":
            value = ApproveToolInput.model_validate(arguments)
            request = ApprovalRequest.model_validate({
                "artifact_ids": value.artifact_ids,
                "decision": value.decision,
                "actor": value.actor,
                "reason": value.reason,
            })
            return self.shim.approve(value.task_id, request)

        if name == "onebridge_retry":
            value = TaskIdInput.model_validate(arguments)
            return self.shim.retry(value.task_id)

        if name == "onebridge_cancel":
            value = TaskIdInput.model_validate(arguments)
            return self.shim.cancel(value.task_id)

        raise KeyError(f"unknown OpenClaw tool: {name}")
