from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DEFAULT_RETAINED_OUTPUT_BYTES = 64_000
DEFAULT_TOTAL_OUTPUT_BYTES = 8 * 1024 * 1024
DEFAULT_WORKSPACE_GROWTH_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_NEW_FILES = 5_000
MAX_SCAN_FILES = 50_000
MONITOR_INTERVAL_SECONDS = 0.25
WORKSPACE_SCAN_INTERVAL_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class SupervisorPolicy:
    retained_output_bytes: int = DEFAULT_RETAINED_OUTPUT_BYTES
    total_output_bytes: int = DEFAULT_TOTAL_OUTPUT_BYTES
    workspace_growth_bytes: int | None = DEFAULT_WORKSPACE_GROWTH_BYTES
    max_new_files: int | None = DEFAULT_MAX_NEW_FILES


@dataclass(frozen=True, slots=True)
class SupervisorResult:
    returncode: int
    output: str
    duration_seconds: float
    termination_reason: str | None
    output_bytes: int
    output_truncated: bool
    workspace_growth_bytes: int | None
    workspace_new_files: int | None


def _workspace_usage(workspace: Path) -> tuple[int, int] | None:
    total = 0
    files = 0
    stack = [workspace]
    try:
        while stack:
            directory = stack.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    if directory == workspace and entry.name == ".git":
                        continue
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            files += 1
                            if files > MAX_SCAN_FILES:
                                return None
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
    except OSError:
        return None
    return total, files


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            process.kill()
        except OSError:
            pass
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    time.sleep(0.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


def run_supervised(
    argv: list[str],
    *,
    cwd: Path,
    workspace_root: Path,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float,
    policy: SupervisorPolicy | None = None,
) -> SupervisorResult:
    policy = policy or SupervisorPolicy()
    root = workspace_root.resolve()
    workdir = cwd.resolve()
    try:
        workdir.relative_to(root)
    except ValueError as exc:
        raise ValueError("supervisor_cwd_outside_workspace") from exc
    if not argv:
        raise ValueError("supervisor_empty_argv")

    baseline = _workspace_usage(root)
    started = time.monotonic()
    retained = bytearray()
    output_total = 0
    lock = threading.Lock()
    done = threading.Event()

    process = subprocess.Popen(
        argv,
        cwd=workdir,
        env=dict(env) if env is not None else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=(os.name != "nt"),
        creationflags=(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if os.name == "nt"
            else 0
        ),
        shell=False,
    )

    def reader() -> None:
        nonlocal output_total
        pipe = process.stdout
        if pipe is None:
            done.set()
            return
        try:
            while True:
                chunk = pipe.read(64 * 1024)
                if not chunk:
                    break
                with lock:
                    output_total += len(chunk)
                    remaining = max(
                        0,
                        policy.retained_output_bytes - len(retained),
                    )
                    if remaining:
                        retained.extend(chunk[:remaining])
        finally:
            done.set()

    thread = threading.Thread(
        target=reader,
        name="onebridge-worker-output-drain",
        daemon=True,
    )
    thread.start()

    termination_reason: str | None = None
    last_workspace_scan = started

    try:
        deadline = started + max(0.1, float(timeout_seconds))
        while process.poll() is None:
            now = time.monotonic()

            if now >= deadline:
                termination_reason = "timeout"
                _kill_tree(process)
                break

            with lock:
                current_output = output_total
            if current_output > policy.total_output_bytes:
                termination_reason = "output_budget_exceeded"
                _kill_tree(process)
                break

            if (
                baseline is not None
                and now - last_workspace_scan >= WORKSPACE_SCAN_INTERVAL_SECONDS
            ):
                last_workspace_scan = now
                current = _workspace_usage(root)
                if current is not None:
                    growth = max(0, current[0] - baseline[0])
                    new_files = max(0, current[1] - baseline[1])
                    if (
                        policy.workspace_growth_bytes is not None
                        and growth > policy.workspace_growth_bytes
                    ):
                        termination_reason = "workspace_growth_budget_exceeded"
                        _kill_tree(process)
                        break
                    if (
                        policy.max_new_files is not None
                        and new_files > policy.max_new_files
                    ):
                        termination_reason = "workspace_file_budget_exceeded"
                        _kill_tree(process)
                        break

            time.sleep(MONITOR_INTERVAL_SECONDS)

        try:
            returncode = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            returncode = process.wait(timeout=2)
    finally:
        done.wait(1.0)
        thread.join(timeout=1.0)
        if process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass

    final_usage = _workspace_usage(root) if baseline is not None else None
    growth_bytes = None
    new_files = None
    if baseline is not None and final_usage is not None:
        growth_bytes = max(0, final_usage[0] - baseline[0])
        new_files = max(0, final_usage[1] - baseline[1])

    with lock:
        raw = bytes(retained)
        total = output_total

    if termination_reason is None and total > policy.total_output_bytes:
        termination_reason = "output_budget_exceeded"
    if (
        termination_reason is None
        and policy.workspace_growth_bytes is not None
        and growth_bytes is not None
        and growth_bytes > policy.workspace_growth_bytes
    ):
        termination_reason = "workspace_growth_budget_exceeded"
    if (
        termination_reason is None
        and policy.max_new_files is not None
        and new_files is not None
        and new_files > policy.max_new_files
    ):
        termination_reason = "workspace_file_budget_exceeded"

    truncated = total > len(raw)
    output = raw.decode("utf-8", "replace")
    if truncated:
        output += "\n[output truncated by OneBridge supervisor]"
    if termination_reason:
        output += f"\n[process terminated: {termination_reason}]"
        returncode = 124 if termination_reason == "timeout" else 125

    return SupervisorResult(
        returncode=returncode,
        output=output,
        duration_seconds=time.monotonic() - started,
        termination_reason=termination_reason,
        output_bytes=total,
        output_truncated=truncated,
        workspace_growth_bytes=growth_bytes,
        workspace_new_files=new_files,
    )
