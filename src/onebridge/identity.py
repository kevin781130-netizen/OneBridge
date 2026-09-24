from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class WorkspaceIdentity:
    id: str
    name: str
    created_at: float
    is_active: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class ApiKeyInfo:
    id: str
    workspace_id: str
    name: str
    prefix: str
    created_at: float
    last_used_at: float | None = None
    revoked_at: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class IdentityStore:
    """Workspace/API-key identity store extracted from CutPilot and renamed for OneBridge."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _init(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workspaces(
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS api_keys(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    prefix TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_used_at REAL,
                    revoked_at REAL,
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(prefix)")
            connection.commit()

    def create_workspace(self, name: str, workspace_id: str | None = None) -> WorkspaceIdentity:
        clean = name.strip()
        if not clean:
            raise ValueError("workspace name is required")
        workspace_id = workspace_id or f"ws_{uuid.uuid4().hex[:16]}"
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO workspaces(id,name,created_at,is_active) VALUES(?,?,?,1)",
                (workspace_id, clean, now),
            )
            connection.commit()
        return WorkspaceIdentity(workspace_id, clean, now, True)

    def get_workspace(self, workspace_id: str) -> WorkspaceIdentity:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id,name,created_at,is_active FROM workspaces WHERE id=?",
                (workspace_id,),
            ).fetchone()
        if row is None:
            raise KeyError(workspace_id)
        return WorkspaceIdentity(
            str(row["id"]), str(row["name"]), float(row["created_at"]), bool(row["is_active"])
        )

    @staticmethod
    def _digest(raw: str, salt: str) -> str:
        return hashlib.sha256((salt + raw).encode("utf-8")).hexdigest()

    def create_api_key(self, workspace_id: str, name: str = "default") -> tuple[ApiKeyInfo, str]:
        self.get_workspace(workspace_id)
        raw = "ob_" + secrets.token_urlsafe(32)
        prefix = raw[:12]
        salt = secrets.token_hex(16)
        digest = self._digest(raw, salt)
        key_id = f"key_{uuid.uuid4().hex[:16]}"
        now = time.time()
        clean_name = name.strip() or "default"
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO api_keys(id,workspace_id,name,prefix,salt,digest,created_at) VALUES(?,?,?,?,?,?,?)",
                (key_id, workspace_id, clean_name, prefix, salt, digest, now),
            )
            connection.commit()
        return ApiKeyInfo(key_id, workspace_id, clean_name, prefix, now), raw

    def authenticate(self, raw_key: str) -> WorkspaceIdentity | None:
        if not raw_key.startswith("ob_"):
            return None
        prefix = raw_key[:12]
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM api_keys WHERE prefix=? AND revoked_at IS NULL", (prefix,)
            ).fetchall()
            for row in rows:
                if not hmac.compare_digest(
                    self._digest(raw_key, str(row["salt"])), str(row["digest"])
                ):
                    continue
                workspace = connection.execute(
                    "SELECT id,name,created_at,is_active FROM workspaces WHERE id=?",
                    (row["workspace_id"],),
                ).fetchone()
                if workspace is None or not bool(workspace["is_active"]):
                    return None
                connection.execute(
                    "UPDATE api_keys SET last_used_at=? WHERE id=?",
                    (time.time(), row["id"]),
                )
                connection.commit()
                return WorkspaceIdentity(
                    str(workspace["id"]),
                    str(workspace["name"]),
                    float(workspace["created_at"]),
                    True,
                )
        return None

    def revoke_api_key(self, key_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE api_keys SET revoked_at=? WHERE id=?", (time.time(), key_id)
            )
            connection.commit()
