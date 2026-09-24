from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from .contracts import TaskContract


@dataclass(frozen=True, slots=True)
class ContextDocument:
    scope: str
    source_id: str
    content: str
    media_type: str = "text/plain"
    priority: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContextItem:
    scope: str
    source_id: str
    content: str
    media_type: str
    sha256: str
    size_bytes: int
    priority: float
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ContextBundle:
    items: tuple[ContextItem, ...]
    requested_scopes: tuple[str, ...]
    missing_scopes: tuple[str, ...]
    used_bytes: int
    budget_bytes: int
    truncated: bool
    skipped: tuple[dict[str, Any], ...]

    def to_adapter_input(self) -> dict[str, Any]:
        return {
            "requested_scopes": list(self.requested_scopes),
            "missing_scopes": list(self.missing_scopes),
            "used_bytes": self.used_bytes,
            "budget_bytes": self.budget_bytes,
            "truncated": self.truncated,
            "items": [asdict(item) for item in self.items],
            "skipped": list(self.skipped),
        }


class ContextProvider(Protocol):
    provider_id: str

    def fetch(
        self,
        scope: str,
        contract: TaskContract,
    ) -> list[ContextDocument]: ...


class ContextForge:
    """Bounded, explicit-scope context assembler generalized from Vera.

    Context is selected only from TaskPolicy.knowledge_scopes. Providers are
    registered explicitly, documents are de-duplicated by SHA-256, and budget
    skips are visible rather than silently expanding the provider prompt.
    """

    def __init__(
        self,
        *,
        budget_bytes: int = 64 * 1024,
        max_items: int = 24,
    ) -> None:
        self.budget_bytes = max(1024, int(budget_bytes))
        self.max_items = max(1, int(max_items))
        self._providers: dict[str, ContextProvider] = {}

    def register(self, scope: str, provider: ContextProvider) -> None:
        scope = str(scope or "").strip()
        if not scope:
            raise ValueError("context_scope_required")
        if scope in self._providers:
            raise ValueError(f"context provider already registered: {scope}")
        self._providers[scope] = provider

    def assemble(self, contract: TaskContract) -> ContextBundle:
        requested = tuple(dict.fromkeys(contract.policy.knowledge_scopes))
        missing = tuple(
            scope for scope in requested if scope not in self._providers
        )
        candidates: list[ContextDocument] = []
        skipped: list[dict[str, Any]] = []

        for scope in requested:
            provider = self._providers.get(scope)
            if provider is None:
                skipped.append({
                    "scope": scope,
                    "reason": "provider_not_registered",
                })
                continue
            try:
                values = provider.fetch(scope, contract)
            except Exception as exc:
                skipped.append({
                    "scope": scope,
                    "provider": getattr(provider, "provider_id", "unknown"),
                    "reason": f"provider_error:{type(exc).__name__}",
                })
                continue
            for value in values[: self.max_items * 2]:
                if value.scope != scope:
                    skipped.append({
                        "scope": scope,
                        "source_id": value.source_id,
                        "reason": "provider_scope_mismatch",
                    })
                    continue
                candidates.append(value)

        candidates.sort(
            key=lambda item: (
                -float(item.priority),
                item.scope,
                item.source_id,
            )
        )

        selected: list[ContextItem] = []
        seen_hashes: set[str] = set()
        used = 0
        for document in candidates:
            if len(selected) >= self.max_items:
                skipped.append({
                    "scope": document.scope,
                    "source_id": document.source_id,
                    "reason": "max_items_exceeded",
                })
                continue
            raw = document.content.encode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            if digest in seen_hashes:
                skipped.append({
                    "scope": document.scope,
                    "source_id": document.source_id,
                    "reason": "duplicate_content",
                })
                continue
            if used + len(raw) > self.budget_bytes:
                skipped.append({
                    "scope": document.scope,
                    "source_id": document.source_id,
                    "reason": "context_budget_exceeded",
                    "size_bytes": len(raw),
                })
                continue
            seen_hashes.add(digest)
            used += len(raw)
            selected.append(
                ContextItem(
                    scope=document.scope,
                    source_id=document.source_id,
                    content=document.content,
                    media_type=document.media_type,
                    sha256=digest,
                    size_bytes=len(raw),
                    priority=float(document.priority),
                    metadata=dict(document.metadata),
                )
            )

        return ContextBundle(
            items=tuple(selected),
            requested_scopes=requested,
            missing_scopes=missing,
            used_bytes=used,
            budget_bytes=self.budget_bytes,
            truncated=bool(skipped),
            skipped=tuple(skipped),
        )


class StaticContextProvider:
    """Small in-process provider useful for tests and controlled deployments."""

    def __init__(
        self,
        provider_id: str,
        documents: dict[str, list[ContextDocument]],
    ) -> None:
        self.provider_id = provider_id
        self.documents = documents

    def fetch(
        self,
        scope: str,
        contract: TaskContract,
    ) -> list[ContextDocument]:
        return list(self.documents.get(scope, ()))
