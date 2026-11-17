from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Sequence


MAX_MCP_LINE_BYTES = 24 * 1024 * 1024
# Do not forward OneBridge/provider keys to the separately installed OD process.
PASSTHROUGH_ENV = (
    "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR",
    "APPDATA", "LOCALAPPDATA", "TMP", "TEMP", "TMPDIR",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
    "LANG", "LC_ALL", "OD_DATA_DIR",
)


class McpStdioError(RuntimeError):
    pass


class McpStdioClient:
    """One bounded MCP stdio session; never starts a shell or OD generation itself."""

    session_id: str | None = None

    def __init__(
        self,
        *,
        command: Sequence[str],
        timeout_seconds: float = 120.0,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.command = tuple(command)
        if not self.command or any(
            not isinstance(arg, str) or not arg or "\x00" in arg
            for arg in self.command
        ):
            raise ValueError("open_design_stdio_invalid_command")
        if Path(self.command[0]).suffix.lower() in {".bat", ".cmd", ".ps1"}:
            raise ValueError("open_design_stdio_shell_launcher_forbidden")
        if "mcp" not in self.command[1:]:
            raise ValueError("open_design_stdio_requires_mcp_subcommand")
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 3600.0))
        self._environment = dict(environment) if environment is not None else {
            key: value for key, value in os.environ.items()
            if key in PASSTHROUGH_ENV
        }
        self._process: subprocess.Popen[bytes] | None = None
        self._messages: queue.Queue[bytes | Exception | None] = queue.Queue(maxsize=4)
        self._reader: threading.Thread | None = None
        self._deadline: float | None = None
        self._next_id = 1
        self._initialized = False

    def __enter__(self) -> McpStdioClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _remaining(self) -> float:
        if self._deadline is None:
            self._deadline = time.monotonic() + self.timeout_seconds
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise McpStdioError("open_design_stdio_timeout")
        return left

    def _start(self) -> None:
        if self._process is not None:
            return
        try:
            self._process = subprocess.Popen(
                list(self.command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                env=self._environment,
                start_new_session=(os.name != "nt"),
            )
        except OSError as exc:
            raise McpStdioError("open_design_stdio_launch_failed") from exc
        assert self._process.stdout is not None
        stdout = self._process.stdout

        def read_stdout() -> None:
            try:
                while True:
                    line = stdout.readline(MAX_MCP_LINE_BYTES + 2)
                    if not line:
                        self._messages.put(None)
                        return
                    if len(line) > MAX_MCP_LINE_BYTES:
                        self._messages.put(McpStdioError("open_design_stdio_response_too_large"))
                        return
                    if line.strip():
                        self._messages.put(line)
            except (OSError, ValueError) as exc:
                self._messages.put(McpStdioError("open_design_stdio_read_failed"))

        self._reader = threading.Thread(target=read_stdout, daemon=True)
        self._reader.start()

    def _send(self, payload: dict[str, Any]) -> None:
        self._remaining()
        self._start()
        assert self._process is not None
        assert self._process.stdin is not None
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
        if len(body) > MAX_MCP_LINE_BYTES:
            raise McpStdioError("open_design_stdio_request_too_large")
        errors: list[BaseException] = []

        def write_stdin() -> None:
            try:
                assert self._process is not None
                assert self._process.stdin is not None
                self._process.stdin.write(body)
                self._process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                errors.append(exc)

        writer = threading.Thread(target=write_stdin, daemon=True)
        writer.start()
        writer.join(timeout=self._remaining())
        if writer.is_alive():
            self.close()
            raise McpStdioError("open_design_stdio_write_timeout")
        if errors:
            raise McpStdioError("open_design_stdio_write_failed") from errors[0]

    def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        request_id = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            try:
                item = self._messages.get(timeout=self._remaining())
            except queue.Empty as exc:
                raise McpStdioError("open_design_stdio_timeout") from exc
            if item is None:
                raise McpStdioError("open_design_stdio_closed")
            if isinstance(item, Exception):
                raise item
            try:
                payload = json.loads(item)
            except (ValueError, UnicodeDecodeError) as exc:
                raise McpStdioError("open_design_stdio_invalid_json") from exc
            if not isinstance(payload, dict):
                raise McpStdioError("open_design_stdio_invalid_json")
            if "id" not in payload and "method" in payload:
                continue  # MCP notification, not the request's response.
            if payload.get("id") != request_id:
                raise McpStdioError("open_design_stdio_response_id_mismatch")
            if payload.get("error") is not None:
                raise McpStdioError("open_design_stdio_rpc_error")
            return payload.get("result")

    def initialize(self) -> None:
        if self._initialized:
            return
        result = self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "onebridge", "version": "0.1.0"},
        })
        if not isinstance(result, dict) or not result.get("protocolVersion"):
            raise McpStdioError("open_design_stdio_invalid_initialize")
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        self._initialized = True

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            raise McpStdioError("open_design_stdio_invalid_tool_result")
        if result.get("isError") is True:
            raise McpStdioError("open_design_stdio_tool_failed")
        return result

    def close(self) -> None:
        proc = self._process
        if proc is None:
            return
        self._process = None
        try:
            if proc.poll() is None:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGTERM)
                else:
                    proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
                proc.wait(timeout=2)
        except (OSError, ProcessLookupError):
            pass
        finally:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except (OSError, ValueError):
                    pass
            if proc.stdout is not None:
                try:
                    proc.stdout.close()
                except (OSError, ValueError):
                    pass
