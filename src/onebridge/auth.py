from __future__ import annotations

from dataclasses import dataclass

from .identity import IdentityStore, WorkspaceIdentity


class AuthenticationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AuthContext:
    workspace_id: str
    workspace_name: str


def authenticate_bearer(
    authorization: str | None,
    store: IdentityStore,
) -> AuthContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("bearer_api_key_required")

    raw = authorization.split(" ", 1)[1].strip()
    if not raw:
        raise AuthenticationError("bearer_api_key_required")

    workspace: WorkspaceIdentity | None = store.authenticate(raw)
    if workspace is None:
        raise AuthenticationError("invalid_or_revoked_api_key")

    return AuthContext(
        workspace_id=workspace.id,
        workspace_name=workspace.name,
    )


def require_tenant(context: AuthContext, tenant_id: str) -> None:
    if context.workspace_id != tenant_id:
        raise AuthenticationError("tenant_scope_mismatch")
