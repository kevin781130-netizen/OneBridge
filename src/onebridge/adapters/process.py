from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from onebridge.redaction import redact_text


@dataclass(frozen=True, slots=True)
class ProcessAdapterResult:
    executable: str
    arguments: tuple[str, ...]
    duration_seconds: float
    exit_code: int
    version: str | None
    stdout_path: Path
    stderr_path: Path

    @property
    def command(self) -> list[str]:
        return [self.executable, *self.arguments]


class ProcessAdapterError(RuntimeError):
    pass


def probe_version(executable: str, timeout_seconds: float = 10.0) -> str | None:
    for flag in ("--version", "--help"):
        try:
            completed = subprocess.run(
                [executable, flag],
                capture_output=True,
                text=True,
                timeout=min(max(timeout_seconds, 0.1), 15.0),
                check=False,
                shell=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        text = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode == 0 and text:
            return redact_text(text.splitlines()[0])[:500]
    return None


def run_adapter_process(
    executable: str,
    arguments: list[str],
    *,
    cwd: Path,
    log_dir: Path,
    timeout_seconds: float,
    env: Mapping[str, str] | None = None,
    max_log_bytes: int = 2 * 1024 * 1024,
) -> ProcessAdapterResult:
    """Run an already-authorized adapter command without a shell.

    Generalized from FlowSonic's external CLI adapters. It persists bounded,
    redacted stdout/stderr and fails on missing binaries, timeout, or non-zero exit.
    """
    workdir = cwd.resolve(strict=True)
    log_dir = log_dir.resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    command = [str(executable), *[str(item) for item in arguments]]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=workdir,
            env=dict(env) if env is not None else os.environ.copy(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=max(0.1, float(timeout_seconds)),
            check=False,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise ProcessAdapterError(f"adapter executable not found: {executable}") from exc
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        _write_logs(log_dir, stdout, stderr, max_log_bytes)
        raise ProcessAdapterError(
            f"adapter timed out after {timeout_seconds:g} seconds"
        ) from exc

    duration = time.monotonic() - started
    stdout_path, stderr_path = _write_logs(
        log_dir,
        completed.stdout or "",
        completed.stderr or "",
        max_log_bytes,
    )
    if completed.returncode != 0:
        raise ProcessAdapterError(
            f"adapter exited with code {completed.returncode}"
        )

    return ProcessAdapterResult(
        executable=str(executable),
        arguments=tuple(str(item) for item in arguments),
        duration_seconds=duration,
        exit_code=completed.returncode,
        version=probe_version(str(executable), timeout_seconds),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )


def _write_logs(
    log_dir: Path,
    stdout: str,
    stderr: str,
    max_bytes: int,
) -> tuple[Path, Path]:
    stdout_path = log_dir / "adapter.stdout.log"
    stderr_path = log_dir / "adapter.stderr.log"

    def bounded(value: str) -> str:
        safe = redact_text(value)
        encoded = safe.encode("utf-8", "replace")
        if len(encoded) <= max_bytes:
            return safe
        return encoded[:max_bytes].decode("utf-8", "replace") + "\n[truncated]\n"

    stdout_path.write_text(bounded(stdout), encoding="utf-8")
    stderr_path.write_text(bounded(stderr), encoding="utf-8")
    return stdout_path, stderr_path
