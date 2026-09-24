from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


ArtifactKind = Literal["content", "design", "code", "test_report", "release", "other"]
ApprovalMode = Literal["none", "before_publish"]


class TaskInput(BaseModel):
    goal: str = Field(min_length=1, max_length=20_000)
    required_outputs: list[ArtifactKind] = Field(default_factory=list)

    @field_validator("required_outputs")
    @classmethod
    def unique_outputs(cls, value: list[ArtifactKind]) -> list[ArtifactKind]:
        if len(value) != len(set(value)):
            raise ValueError("required_outputs must be unique")
        return value


class TaskContext(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=200)
    user_id: str = Field(min_length=1, max_length=200)
    channel: str = Field(default="api", min_length=1, max_length=100)
    conversation_id: str | None = Field(default=None, max_length=300)


class TaskPolicy(BaseModel):
    knowledge_scopes: list[str] = Field(default_factory=list)
    network: Literal["disabled", "restricted", "open"] = "restricted"
    approval: ApprovalMode = "before_publish"


class TaskContract(BaseModel):
    contract_version: Literal["1.0"] = "1.0"
    task_id: str = Field(default_factory=lambda: f"task_{uuid4().hex}")
    project_id: str = Field(default_factory=lambda: f"prj_{uuid4().hex}")
    operation: str = Field(default="project.create", min_length=1, max_length=200)
    input: TaskInput
    context: TaskContext
    policy: TaskPolicy = Field(default_factory=TaskPolicy)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactManifest(BaseModel):
    artifact_id: str
    project_id: str
    task_id: str
    revision: int = Field(ge=1)
    kind: ArtifactKind
    media_type: str
    sha256: str = Field(min_length=64, max_length=64)
    uri: str
    producer_adapter: str
    producer_version: str = "0"
    status: Literal[
        "draft",
        "generated",
        "testing",
        "awaiting_review",
        "approved",
        "rejected",
        "released",
        "superseded",
    ] = "generated"
    input_artifacts: list[str] = Field(default_factory=list)


class ApprovalRequest(BaseModel):
    artifact_ids: list[str] = Field(min_length=1)
    decision: Literal["approve", "reject"]
    reason: str = Field(default="", max_length=4000)
    actor: str = Field(min_length=1, max_length=200)
