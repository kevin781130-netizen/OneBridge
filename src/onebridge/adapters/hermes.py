from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable

from onebridge.artifacts import LocalObjectStore, S3ObjectStore
from onebridge.redaction import redact_text
from onebridge.workers.sandbox import (
    SANDBOX_WORKSPACE,
    SandboxedCommand,
    prepare_sandboxed_command,
    probe_strong_sandbox,
)
from onebridge.workers.supervisor import (
    SupervisorPolicy,
    SupervisorResult,
    run_supervised,
)
from onebridge.workers.workspace import WorkerWorkspace

from .base import AdapterHealth, AdapterOutput, AdapterRequest, AdapterResult
from .validation import validate_adapter_outputs


MAX_RESULT_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 50 * 1024 * 1024


class HermesAdapterError(RuntimeError):
    pass


SandboxBuilder = Callable[[str | Path, str | Path, list[str]], SandboxedCommand]
Runner = Callable[..., SupervisorResult]


def _safe_env() -> dict[str, str]:
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "COMSPEC",
        "PATHEXT",
        "VIRTUAL_ENV",
    }
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in allowed
    }
    env.update({
        "CI": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "PIP_NO_INDEX": "1",
        "NPM_CONFIG_OFFLINE": "true",
        "CARGO_NET_OFFLINE": "true",
        "GOPROXY": "off",
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    })
    return env


class HermesCommandAdapter:
    name = "hermes"

    def __init__(
        self,
        *,
        executable: str,
        arguments: tuple[str, ...],
        store: LocalObjectStore | S3ObjectStore,
        timeout_seconds: float = 300.0,
        require_strong_sandbox: bool = True,
        workspace_parent: str | Path | None = None,
        sandbox_builder: SandboxBuilder = prepare_sandboxed_command,
        runner: Runner = run_supervised,
        version: str = "command-v1",
    ) -> None:
        self.executable = str(executable or "").strip()
        self.arguments = tuple(str(item) for item in arguments)
        self.store = store
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 1800.0))
        self.require_strong_sandbox = bool(require_strong_sandbox)
        self.workspace_parent = workspace_parent
        self.sandbox_builder = sandbox_builder
        self.runner = runner
        self.version = version

        if not self.executable:
            raise ValueError("hermes_executable_required")
        allowed_placeholders = {"{request}", "{output}", "{workspace}"}
        for item in self.arguments:
            for marker in ("{request}", "{output}", "{workspace}"):
                if marker in item:
                    break
            if "{" in item or "}" in item:
                if not any(marker in item for marker in allowed_placeholders):
                    raise ValueError("hermes_argument_placeholder_invalid")

    def health(self) -> AdapterHealth:
        executable = shutil.which(self.executable)
        if executable is None and not Path(self.executable).is_file():
            return AdapterHealth("unhealthy", "executable_not_found")
        if self.require_strong_sandbox:
            probe = probe_strong_sandbox()
            if not probe.available:
                return AdapterHealth("unhealthy", probe.reason)
        return AdapterHealth("healthy", "configured_command")

    def capabilities(self) -> list[str]:
        return ["code", "test_report"]

    def _materialize_inputs(
        self,
        request: AdapterRequest,
        workspace: WorkerWorkspace,
        *,
        sandboxed: bool,
    ) -> list[dict[str, Any]]:
        if workspace.inputs is None or workspace.root is None:
            raise RuntimeError("hermes_workspace_not_open")

        values: list[dict[str, Any]] = []
        for index, artifact in enumerate(request.artifact_inputs[:64]):
            if not isinstance(artifact, dict):
                continue
            uri = str(artifact.get("uri") or "")
            if not uri:
                continue
            kind = str(artifact.get("kind") or "artifact")[:80]
            suffix = Path(uri.split("?", 1)[0]).suffix[:20]
            filename = f"{index:02d}-{kind}{suffix or '.bin'}"
            local = workspace.materialize(
                self.store,
                uri,
                name=filename,
            )
            relative = local.relative_to(workspace.root).as_posix()
            exposed = (
                f"{SANDBOX_WORKSPACE}/{relative}"
                if sandboxed
                else str(local)
            )
            values.append({
                "artifact_id": str(artifact.get("artifact_id") or "")[:300],
                "kind": kind,
                "media_type": str(artifact.get("media_type") or "")[:200],
                "sha256": str(artifact.get("sha256") or "")[:64],
                "path": exposed,
            })
        return values

    @staticmethod
    def _render_args(
        executable: str,
        arguments: tuple[str, ...],
        *,
        request_path: str,
        output_path: str,
        workspace_path: str,
    ) -> list[str]:
        replacements = {
            "{request}": request_path,
            "{output}": output_path,
            "{workspace}": workspace_path,
        }
        rendered = [executable]
        for item in arguments:
            value = item
            for marker, replacement in replacements.items():
                value = value.replace(marker, replacement)
            rendered.append(value)
        return rendered

    @staticmethod
    def _load_outputs(workspace: WorkerWorkspace) -> list[AdapterOutput]:
        if workspace.outputs is None:
            raise RuntimeError("hermes_workspace_not_open")
        manifest_path = workspace.outputs / "result.json"
        if not manifest_path.is_file():
            raise HermesAdapterError("hermes_result_manifest_missing")
        if manifest_path.stat().st_size > MAX_RESULT_BYTES:
            raise HermesAdapterError("hermes_result_manifest_too_large")

        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HermesAdapterError("hermes_result_manifest_invalid") from exc

        items = payload.get("outputs") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            raise HermesAdapterError("hermes_result_outputs_missing")

        outputs: list[AdapterOutput] = []
        total = 0
        for item in items[:32]:
            if not isinstance(item, dict):
                raise HermesAdapterError("hermes_result_output_invalid")
            kind = str(item.get("kind") or "")
            if kind not in {"code", "test_report"}:
                raise HermesAdapterError(
                    f"hermes_result_kind_invalid:{kind}"
                )
            relative = str(item.get("filename") or "")
            candidate = (workspace.outputs / relative).resolve()
            outputs_root = workspace.outputs.resolve()
            if (
                not relative
                or candidate == manifest_path.resolve()
                or (
                    candidate != outputs_root
                    and outputs_root not in candidate.parents
                )
                or not candidate.is_file()
                or candidate.is_symlink()
            ):
                raise HermesAdapterError("hermes_result_path_invalid")
            content = candidate.read_bytes()
            total += len(content)
            if total > MAX_OUTPUT_BYTES:
                raise HermesAdapterError("hermes_outputs_too_large")
            outputs.append(
                AdapterOutput(
                    kind=kind,
                    media_type=str(
                        item.get("media_type")
                        or (
                            "application/json"
                            if kind == "test_report"
                            else "text/plain"
                        )
                    ),
                    content=content,
                    filename=Path(relative).name,
                )
            )
        return outputs

    def execute(self, request: AdapterRequest) -> AdapterResult:
        executable = shutil.which(self.executable) or self.executable
        sandboxed = False
        backend = "none"

        if self.require_strong_sandbox:
            probe = probe_strong_sandbox()
            if not probe.available or not probe.backend:
                raise HermesAdapterError(
                    f"hermes_strong_sandbox_unavailable:{probe.reason}"
                )
            sandboxed = True
            backend = probe.backend

        with WorkerWorkspace(
            request.task_id,
            parent=self.workspace_parent,
        ) as workspace:
            assert workspace.root is not None
            assert workspace.outputs is not None

            artifacts = self._materialize_inputs(
                request,
                workspace,
                sandboxed=sandboxed and backend == "bubblewrap",
            )
            request_path = workspace.root / "request.json"
            request_payload = {
                "schema_version": 1,
                "task_id": request.task_id,
                "project_id": request.project_id,
                "goal": request.goal,
                "operation": request.operation,
                "inputs": request.inputs,
                "artifacts": artifacts,
                "required_outputs": ["code", "test_report"],
            }
            request_path.write_text(
                json.dumps(
                    request_payload,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            if sandboxed and backend == "bubblewrap":
                exposed_request = f"{SANDBOX_WORKSPACE}/request.json"
                exposed_output = f"{SANDBOX_WORKSPACE}/outputs"
                exposed_workspace = SANDBOX_WORKSPACE
            else:
                exposed_request = str(request_path)
                exposed_output = str(workspace.outputs)
                exposed_workspace = str(workspace.root)

            argv = self._render_args(
                executable,
                self.arguments,
                request_path=exposed_request,
                output_path=exposed_output,
                workspace_path=exposed_workspace,
            )
            if sandboxed:
                prepared = self.sandbox_builder(
                    workspace.root,
                    workspace.root,
                    argv,
                )
                run_argv = prepared.argv
                backend = prepared.backend
            else:
                run_argv = argv

            result = self.runner(
                run_argv,
                cwd=workspace.root,
                workspace_root=workspace.root,
                env=_safe_env(),
                timeout_seconds=self.timeout_seconds,
                policy=SupervisorPolicy(
                    workspace_growth_bytes=512 * 1024 * 1024,
                    max_new_files=5_000,
                ),
            )
            if result.returncode != 0:
                raise HermesAdapterError(
                    "hermes_process_failed:"
                    + redact_text(result.output)[-2000:]
                )

            outputs = self._load_outputs(workspace)
            for required_kind in ("code", "test_report"):
                report = validate_adapter_outputs(
                    outputs,
                    expected_kind=required_kind,
                    max_total_bytes=MAX_OUTPUT_BYTES,
                )
                if not report.valid:
                    raise HermesAdapterError(
                        "hermes_output_validation_failed:"
                        + ";".join(report.errors)
                    )

            return AdapterResult(
                external_task_id=request.task_id,
                outputs=outputs,
                metadata={
                    "sandbox_backend": backend,
                    "duration_seconds": result.duration_seconds,
                    "output_bytes": result.output_bytes,
                    "output_truncated": result.output_truncated,
                },
            )

    def cancel(self, external_task_id: str) -> None:
        return None
