from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from onebridge.artifacts import LocalObjectStore, S3ObjectStore
from onebridge.mcp_http import McpHttpClient

from .base import AdapterHealth, AdapterOutput, AdapterRequest, AdapterResult
from .validation import validate_adapter_outputs


MAX_CONTEXT_BYTES = 1_000_000
MAX_ARTIFACT_BYTES = 20 * 1024 * 1024


class OpenDesignAdapterError(RuntimeError):
    pass


def _is_text_media(media_type: str) -> bool:
    value = str(media_type or "").lower().split(";", 1)[0].strip()
    return (
        value.startswith("text/")
        or value in {"application/json", "application/xml"}
        or value.endswith("+json")
    )


class OpenDesignMCPAdapter:
    name = "open_design"

    def __init__(
        self,
        *,
        client: McpHttpClient,
        store: LocalObjectStore | S3ObjectStore,
        tool_name: str = "render_design",
        version: str = "mcp-v1",
    ) -> None:
        self.client = client
        self.store = store
        self.tool_name = str(tool_name or "").strip()
        self.version = version
        if not self.tool_name:
            raise ValueError("open_design_tool_required")

    def health(self) -> AdapterHealth:
        return AdapterHealth("healthy", "configured_mcp")

    def capabilities(self) -> list[str]:
        return ["design"]

    def _artifact_context(
        self,
        request: AdapterRequest,
    ) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="onebridge-open-design-") as temp:
            root = Path(temp)
            for index, artifact in enumerate(request.artifact_inputs[:32]):
                if not isinstance(artifact, dict):
                    continue
                item = {
                    "artifact_id": str(artifact.get("artifact_id") or "")[:300],
                    "kind": str(artifact.get("kind") or "")[:100],
                    "media_type": str(artifact.get("media_type") or "")[:200],
                    "sha256": str(artifact.get("sha256") or "")[:64],
                    "uri": str(artifact.get("uri") or "")[:2000],
                }
                uri = str(artifact.get("uri") or "")
                media_type = str(artifact.get("media_type") or "")
                if uri and _is_text_media(media_type):
                    destination = root / f"artifact-{index}"
                    try:
                        materialized = self.store.get_file(uri, destination)
                        if materialized.stat().st_size <= MAX_CONTEXT_BYTES:
                            item["text"] = materialized.read_text(
                                encoding="utf-8"
                            )
                    except (OSError, ValueError, UnicodeDecodeError):
                        pass
                values.append(item)
        return values

    @staticmethod
    def _structured_artifacts(result: dict[str, Any]) -> list[dict[str, Any]]:
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            artifacts = structured.get("artifacts")
            if isinstance(artifacts, list):
                return [item for item in artifacts if isinstance(item, dict)]

        for item in result.get("content", []):
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("text")
            if not isinstance(text, str):
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get("artifacts"), list):
                return [
                    value
                    for value in parsed["artifacts"]
                    if isinstance(value, dict)
                ]
        return []

    @staticmethod
    def _fallback_text_output(result: dict[str, Any]) -> AdapterOutput | None:
        for item in result.get("content", []):
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            lowered = text.lstrip().lower()
            is_html = lowered.startswith("<!doctype html") or "<html" in lowered[:500]
            return AdapterOutput(
                kind="design",
                media_type="text/html" if is_html else "text/plain",
                content=text.encode("utf-8"),
                filename="index.html" if is_html else "design.txt",
            )
        return None

    @staticmethod
    def _decode_artifact(item: dict[str, Any]) -> AdapterOutput:
        kind = str(item.get("kind") or "design")
        if kind != "design":
            raise OpenDesignAdapterError(
                f"open_design_unexpected_artifact_kind:{kind}"
            )
        filename = str(item.get("filename") or "design.html")
        media_type = str(item.get("media_type") or "text/html")

        if isinstance(item.get("text"), str):
            content = item["text"].encode("utf-8")
        elif isinstance(item.get("content_base64"), str):
            try:
                content = base64.b64decode(
                    item["content_base64"],
                    validate=True,
                )
            except ValueError as exc:
                raise OpenDesignAdapterError(
                    "open_design_invalid_base64_artifact"
                ) from exc
        else:
            raise OpenDesignAdapterError(
                "open_design_artifact_has_no_content"
            )

        if len(content) > MAX_ARTIFACT_BYTES:
            raise OpenDesignAdapterError(
                "open_design_artifact_too_large"
            )
        return AdapterOutput(
            kind="design",
            media_type=media_type,
            content=content,
            filename=filename,
        )

    def execute(self, request: AdapterRequest) -> AdapterResult:
        arguments = {
            "task_id": request.task_id,
            "project_id": request.project_id,
            "goal": request.goal,
            "operation": request.operation,
            "inputs": request.inputs,
            "artifacts": self._artifact_context(request),
        }
        result = self.client.call_tool(
            self.tool_name,
            arguments,
        )

        outputs = [
            self._decode_artifact(item)
            for item in self._structured_artifacts(result)
        ]
        if not outputs:
            fallback = self._fallback_text_output(result)
            if fallback is not None:
                outputs = [fallback]
        if not outputs:
            raise OpenDesignAdapterError(
                "open_design_returned_no_design_artifact"
            )

        report = validate_adapter_outputs(
            outputs,
            expected_kind="design",
            max_total_bytes=MAX_ARTIFACT_BYTES,
        )
        if not report.valid:
            raise OpenDesignAdapterError(
                "open_design_output_validation_failed:"
                + ";".join(report.errors)
            )

        return AdapterResult(
            external_task_id=f"open_design_{uuid4().hex}",
            outputs=outputs,
            metadata={
                "tool": self.tool_name,
                "mcp_session": self.client.session_id,
            },
        )

    def cancel(self, external_task_id: str) -> None:
        return None
