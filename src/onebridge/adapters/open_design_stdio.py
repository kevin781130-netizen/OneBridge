from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from pathlib import PurePosixPath
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5

from onebridge.artifacts import LocalObjectStore, S3ObjectStore
from onebridge.mcp_stdio import McpStdioClient

from .base import AdapterHealth, AdapterOutput, AdapterRequest, AdapterResult
from .open_design import MAX_ARTIFACT_BYTES, OpenDesignAdapterError, OpenDesignMCPAdapter
from .validation import validate_adapter_outputs


MAX_OD_PROMPT_BYTES = 128 * 1024
MAX_OD_FILES = 200
MAX_OD_SOURCE_BYTES = 18 * 1024 * 1024


def _result_payload(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("isError") is True:
        raise OpenDesignAdapterError("open_design_stdio_tool_failed")
    structured = response.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for item in response.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                payload = json.loads(item.get("text", ""))
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                return payload
    raise OpenDesignAdapterError("open_design_stdio_invalid_tool_payload")


def _safe_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 240 or "\\" in value:
        raise OpenDesignAdapterError("open_design_stdio_unsafe_path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts) or ":" in parts[0]:
        raise OpenDesignAdapterError("open_design_stdio_unsafe_path")
    if PurePosixPath(value).is_absolute():
        raise OpenDesignAdapterError("open_design_stdio_unsafe_path")
    return value


def _outputs_from_bundle(response: dict[str, Any]) -> tuple[list[AdapterOutput], dict[str, Any]]:
    bundle = _result_payload(response)
    if bundle.get("truncated") is True or bundle.get("skippedFileCount", 0) != 0:
        raise OpenDesignAdapterError("open_design_stdio_incomplete_bundle")
    files = bundle.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_OD_FILES:
        raise OpenDesignAdapterError("open_design_stdio_invalid_bundle")
    entry = _safe_relative_path(bundle.get("entryFile"))
    seen: set[str] = set()
    extracted: list[tuple[str, str, bytes]] = []
    source_bytes = 0
    for item in files:
        if not isinstance(item, dict):
            raise OpenDesignAdapterError("open_design_stdio_invalid_file")
        path = _safe_relative_path(item.get("name"))
        if path in seen:
            raise OpenDesignAdapterError("open_design_stdio_duplicate_path")
        seen.add(path)
        if item.get("binary") is True or not isinstance(item.get("content"), str):
            raise OpenDesignAdapterError("open_design_stdio_binary_file_unsupported")
        mime = str(item.get("mime") or "")
        if "/" not in mime or "\n" in mime or "\r" in mime:
            raise OpenDesignAdapterError("open_design_stdio_invalid_mime")
        data = item["content"].encode("utf-8")
        if not data:
            raise OpenDesignAdapterError("open_design_stdio_empty_file")
        source_bytes += len(data)
        if source_bytes > MAX_OD_SOURCE_BYTES:
            raise OpenDesignAdapterError("open_design_stdio_source_too_large")
        extracted.append((path, mime, data))
    by_path = {path: (mime, data) for path, mime, data in extracted}
    if entry not in by_path:
        raise OpenDesignAdapterError("open_design_stdio_missing_entry")
    mime, content = by_path[entry]
    filename = PurePosixPath(entry).name
    outputs = [AdapterOutput(kind="design", media_type=mime, content=content, filename=filename)]
    if len(extracted) > 1:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, _mime, data in extracted:
                info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
        archive_data = buf.getvalue()
        archive_name = "open_design_sources.zip"
        if filename == archive_name:
            raise OpenDesignAdapterError("open_design_stdio_archive_name_collision")
        outputs.append(AdapterOutput(
            kind="design", media_type="application/zip",
            content=archive_data, filename=archive_name,
        ))
    report = validate_adapter_outputs(
        outputs, expected_kind="design", max_total_bytes=MAX_ARTIFACT_BYTES,
    )
    if not report.valid:
        raise OpenDesignAdapterError(
            "open_design_stdio_invalid_output:" + ";".join(report.errors)
        )
    return outputs, {"entry_file": entry, "file_count": len(extracted)}


class OpenDesignStdioAdapter(OpenDesignMCPAdapter):
    """Native od mcp adapter; opt-in, leaving the legacy HTTP adapter untouched."""

    name = "open_design"

    def __init__(
        self,
        *,
        client_factory: Callable[[], McpStdioClient],
        store: LocalObjectStore | S3ObjectStore,
        timeout_seconds: float = 120.0,
        poll_seconds: float = 2.0,
    ) -> None:
        self.client_factory = client_factory
        self.store = store
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.poll_seconds = max(0.001, float(poll_seconds))
        self.version = "od-stdio-v1"

    def health(self) -> AdapterHealth:
        return AdapterHealth("healthy", "configured_stdio_not_live_qualified")

    def capabilities(self) -> list[str]:
        return ["design"]

    def _prompt(self, request: AdapterRequest) -> str:
        context = {
            "operation": request.operation,
            "inputs": request.inputs,
            "upstream_artifacts": self._artifact_context(request),
        }
        prompt = request.goal + "\n\nOneBridge task context (JSON):\n" + json.dumps(
            context, ensure_ascii=False, sort_keys=True,
        )
        if len(prompt.encode("utf-8")) > MAX_OD_PROMPT_BYTES:
            raise OpenDesignAdapterError("open_design_stdio_prompt_too_large")
        return prompt

    @staticmethod
    def _project_id(task_id: str) -> str:
        return "ob-" + hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:24]

    def execute(self, request: AdapterRequest) -> AdapterResult:
        deadline = time.monotonic() + self.timeout_seconds
        project_id = self._project_id(request.task_id)
        request_id = str(uuid5(NAMESPACE_URL, "onebridge/open-design/" + request.task_id))
        prompt = self._prompt(request)
        client = self.client_factory()
        run_id: str | None = None
        try:
            projects = _result_payload(client.call_tool("list_projects", {}))
            existing = projects.get("projects")
            if not isinstance(existing, list):
                raise OpenDesignAdapterError("open_design_stdio_invalid_project_list")
            if not any(
                isinstance(project, dict) and project.get("id") == project_id
                for project in existing
            ):
                created = _result_payload(client.call_tool("create_project", {
                    "name": "OneBridge " + request.task_id[:72],
                    "id": project_id,
                }))
                # Do not retry a failed or ambiguous mutation.
                if created.get("id") not in {None, project_id}:
                    raise OpenDesignAdapterError("open_design_stdio_project_mismatch")

            started = _result_payload(client.call_tool("start_run", {
                "project": project_id,
                "prompt": prompt,
                "requestId": request_id,
            }))
            run_id = started.get("runId") or started.get("id")
            if not isinstance(run_id, str) or not run_id:
                raise OpenDesignAdapterError("open_design_stdio_missing_run_id")
            while True:
                if time.monotonic() >= deadline:
                    raise OpenDesignAdapterError("open_design_stdio_run_timeout")
                state = _result_payload(client.call_tool("get_run", {"runId": run_id}))
                status = state.get("status")
                if status == "succeeded":
                    break
                if status in {"failed", "canceled", "cancelled", "blocked"}:
                    raise OpenDesignAdapterError("open_design_stdio_run_" + str(status))
                if status not in {"queued", "running"}:
                    raise OpenDesignAdapterError("open_design_stdio_unknown_run_status")
                time.sleep(min(self.poll_seconds, max(0, deadline - time.monotonic())))
            artifact_args: dict[str, Any] = {
                "project": project_id,
                "include": "all",
                "maxBytes": MAX_OD_SOURCE_BYTES,
            }
            if isinstance(state.get("entryFile"), str) and state["entryFile"]:
                artifact_args["entry"] = state["entryFile"]
            outputs, details = _outputs_from_bundle(client.call_tool(
                "get_artifact", artifact_args,
            ))
            return AdapterResult(
                external_task_id=run_id,
                outputs=outputs,
                metadata={"upstream": "open-design", "transport": "stdio", **details},
            )
        except OpenDesignAdapterError as exc:
            if run_id and str(exc) == "open_design_stdio_run_timeout":
                try:
                    client.call_tool("cancel_run", {"runId": run_id})
                except Exception:
                    pass
            raise
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    def cancel(self, external_task_id: str) -> None:
        if not external_task_id:
            raise ValueError("open_design_stdio_run_id_required")
        client = self.client_factory()
        try:
            _result_payload(client.call_tool("cancel_run", {
                "runId": external_task_id,
            }))
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
