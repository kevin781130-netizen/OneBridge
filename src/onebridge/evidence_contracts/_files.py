"""證據檔案的安全讀取（供各 adapter 共用）：拒絕絕對路徑、`..`、反斜線與 symlink 逃逸。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

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


def strict_json(raw: bytes, *, invalid: str = "INVALID_JSON") -> Any:
    """嚴格解析不可信 JSON：UTF-8、拒絕重複 key 與 NaN／Infinity。"""
    def unique(pairs: list[tuple[str, object]]) -> dict:
        out: dict = {}
        for key, value in pairs:
            if key in out:
                raise ContractError("DUPLICATE_JSON_KEY")
            out[key] = value
        return out

    def nonfinite(_: str) -> None:
        raise ContractError("NONFINITE_JSON")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(invalid) from exc
