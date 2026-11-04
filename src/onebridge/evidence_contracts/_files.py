"""證據檔案的安全讀取（供各 adapter 共用）：拒絕絕對路徑、`..`、反斜線與 symlink 逃逸。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .validator import ContractError


def read_local(root: Path, path: str, *, max_bytes: int | None = None) -> tuple[Path, str, str, int]:
    """Resolve only evidence-root relative paths and reject symlink escapes."""
    raw = Path(path)
    if raw.is_absolute() or not path or "\\" in path or any(p in ("..", ".") for p in raw.parts):
        raise ContractError("absolute/traversal evidence paths are forbidden")
    try:
        resolved = (root / raw).resolve(strict=True)
        relative = resolved.relative_to(root).as_posix()
    except (ValueError, OSError) as exc:
        raise ContractError("evidence file missing or outside approved root") from exc
    if not resolved.is_file() or raw.as_posix() != relative:
        raise ContractError("evidence must be a regular file with canonical relative path")
    if max_bytes is not None and resolved.stat().st_size > max_bytes:
        raise ContractError("evidence JSON exceeds size limit")
    sha = hashlib.sha256()
    n = 0
    with resolved.open("rb") as inp:
        for block in iter(lambda: inp.read(1024 * 1024), b""):
            sha.update(block)
            n += len(block)
            if max_bytes is not None and n > max_bytes:
                raise ContractError("evidence JSON exceeds size limit")
    return resolved, relative, sha.hexdigest(), n
