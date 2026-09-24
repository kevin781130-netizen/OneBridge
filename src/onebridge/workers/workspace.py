from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,160}$")


@dataclass(frozen=True, slots=True)
class WorkspaceArtifact:
    relative_path: str
    path: Path
    sha256: str
    size: int


class WorkerWorkspace:
    """Disposable per-job workspace generalized from CutPilot workers.

    Inputs, outputs and logs are separated. The directory is removed when the
    context exits unless preserve=True is explicitly requested for debugging.
    """

    def __init__(
        self,
        job_id: str,
        *,
        parent: str | Path | None = None,
        preserve: bool = False,
    ) -> None:
        if not _SAFE_ID.fullmatch(str(job_id)):
            raise ValueError("invalid_worker_job_id")
        self.job_id = str(job_id)
        self.parent = Path(parent).expanduser().resolve() if parent else None
        self.preserve = bool(preserve)
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self.root: Path | None = None
        self.inputs: Path | None = None
        self.outputs: Path | None = None
        self.logs: Path | None = None

    def __enter__(self) -> "WorkerWorkspace":
        if self.parent is not None:
            self.parent.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix=f"onebridge-job-{self.job_id}-",
            dir=str(self.parent) if self.parent else None,
            ignore_cleanup_errors=True,
        )
        self.root = Path(self._temporary.name).resolve()
        self.inputs = self.root / "inputs"
        self.outputs = self.root / "outputs"
        self.logs = self.root / "logs"
        for directory in (self.inputs, self.outputs, self.logs):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._temporary is None:
            return
        if self.preserve:
            self._temporary._finalizer.detach()
        else:
            self._temporary.cleanup()

    def _require_open(self) -> tuple[Path, Path, Path, Path]:
        if self.root is None or self.inputs is None or self.outputs is None or self.logs is None:
            raise RuntimeError("worker_workspace_not_open")
        return self.root, self.inputs, self.outputs, self.logs

    def materialize(
        self,
        store,
        uri: str,
        *,
        name: str,
    ) -> Path:
        _, inputs, _, _ = self._require_open()
        clean_name = Path(name).name
        if clean_name != name or not clean_name or clean_name in {".", ".."}:
            raise ValueError("invalid_materialized_filename")
        destination = (inputs / clean_name).resolve()
        if inputs != destination and inputs not in destination.parents:
            raise ValueError("materialized_path_outside_inputs")

        parsed = urlparse(uri)
        if parsed.scheme in {"s3", "file"}:
            return store.get_file(uri, destination)

        source = Path(uri).expanduser().resolve(strict=True)
        if source.is_symlink() or not source.is_file():
            raise ValueError("invalid_local_input")
        shutil.copy2(source, destination)
        return destination

    def collect_outputs(
        self,
        *,
        max_files: int = 500,
        max_total_bytes: int = 512 * 1024 * 1024,
    ) -> list[WorkspaceArtifact]:
        _, _, outputs, _ = self._require_open()
        artifacts: list[WorkspaceArtifact] = []
        total = 0

        for path in sorted(outputs.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(outputs).as_posix()
            if ".." in Path(relative).parts:
                raise ValueError("output_path_escape")
            size = path.stat().st_size
            total += size
            if total > max_total_bytes:
                raise ValueError("worker_output_byte_budget_exceeded")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            artifacts.append(
                WorkspaceArtifact(
                    relative_path=relative,
                    path=path,
                    sha256=digest,
                    size=size,
                )
            )
            if len(artifacts) > max_files:
                raise ValueError("worker_output_file_budget_exceeded")

        return artifacts

    def output_path(self, relative_name: str) -> Path:
        _, _, outputs, _ = self._require_open()
        candidate = (outputs / relative_name).resolve()
        if outputs != candidate and outputs not in candidate.parents:
            raise ValueError("worker_output_path_escape")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate
