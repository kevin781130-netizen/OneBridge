from __future__ import annotations

import platform
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


SANDBOX_WORKSPACE = "/workspace"


@dataclass(frozen=True, slots=True)
class SandboxProbe:
    available: bool
    backend: str | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SandboxedCommand:
    argv: list[str]
    backend: str


def _which(name: str) -> str | None:
    return shutil.which(name)


@lru_cache(maxsize=1)
def probe_strong_sandbox() -> SandboxProbe:
    """Fail-closed strong sandbox detection generalized from Vera's agent sandbox."""
    system = platform.system().lower()
    if system == "linux":
        if _which("bwrap"):
            return SandboxProbe(True, "bubblewrap")
        return SandboxProbe(False, reason="bubblewrap_not_installed")
    if system == "darwin":
        if _which("sandbox-exec"):
            return SandboxProbe(True, "seatbelt")
        return SandboxProbe(False, reason="seatbelt_unavailable")
    if system == "windows":
        return SandboxProbe(False, reason="windows_strong_sandbox_requires_worker_backend")
    return SandboxProbe(False, reason="unsupported_sandbox_platform")


def _sandbox_cwd(workspace: Path, cwd: Path) -> str:
    relative = cwd.relative_to(workspace).as_posix()
    return SANDBOX_WORKSPACE if relative == "." else f"{SANDBOX_WORKSPACE}/{relative}"


def _bubblewrap(workspace: Path, cwd: Path, argv: list[str]) -> SandboxedCommand:
    executable = _which("bwrap")
    if not executable:
        raise RuntimeError("bubblewrap_not_installed")
    command = [
        executable,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
    ]
    for root in ("/usr", "/bin", "/lib", "/lib64", "/usr/local", "/etc"):
        if Path(root).exists():
            command.extend(["--ro-bind", root, root])
    command.extend([
        "--bind", str(workspace), SANDBOX_WORKSPACE,
        "--ro-bind-try", str(workspace / ".git"), f"{SANDBOX_WORKSPACE}/.git",
        "--chdir", _sandbox_cwd(workspace, cwd),
        "--setenv", "HOME", SANDBOX_WORKSPACE,
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "NO_PROXY", "*",
        "--",
        *argv,
    ])
    return SandboxedCommand(command, "bubblewrap")


def _seatbelt_profile(workspace: Path) -> str:
    escaped = str(workspace).replace("\\", "\\\\").replace('"', '\\"')
    git_escaped = str(workspace / ".git").replace("\\", "\\\\").replace('"', '\\"')
    return (
        '(version 1)\n'
        '(deny default)\n'
        '(allow process*)\n'
        '(allow sysctl-read)\n'
        '(allow mach-lookup)\n'
        '(allow file-read* (subpath "/System") (subpath "/usr") (subpath "/Library") '
        f'(subpath "/private/etc") (subpath "/dev") (subpath "{escaped}"))\n'
        f'(allow file-write* (subpath "{escaped}") (subpath "/private/tmp"))\n'
        f'(deny file-write* (subpath "{git_escaped}"))\n'
        '(deny network*)\n'
    )


def prepare_sandboxed_command(workspace: str | Path, cwd: str | Path, argv: list[str]) -> SandboxedCommand:
    root = Path(workspace).resolve(strict=True)
    workdir = Path(cwd).resolve(strict=True)
    try:
        workdir.relative_to(root)
    except ValueError as exc:
        raise ValueError("worker_cwd_outside_workspace") from exc
    probe = probe_strong_sandbox()
    if not probe.available or not probe.backend:
        raise RuntimeError(probe.reason or "sandbox_unavailable")
    if probe.backend == "bubblewrap":
        return _bubblewrap(root, workdir, argv)
    if probe.backend == "seatbelt":
        executable = _which("sandbox-exec")
        if not executable:
            raise RuntimeError("seatbelt_unavailable")
        return SandboxedCommand([executable, "-p", _seatbelt_profile(root), *argv], "seatbelt")
    raise RuntimeError("sandbox_unavailable")
