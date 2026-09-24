from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Callable

from .adapters.base import OneBridgeAdapter
from .contextforge import ContextProvider


ENTRY_POINT_GROUP = "onebridge.adapters"
CONTEXT_ENTRY_POINT_GROUP = "onebridge.context_providers"


@dataclass(frozen=True, slots=True)
class AdapterPluginContext:
    store: Any
    state_root: Path


@dataclass(frozen=True, slots=True)
class ContextPluginContext:
    state_root: Path


@dataclass(frozen=True, slots=True)
class AdapterPluginInfo:
    name: str
    value: str
    distribution: str | None
    version: str | None


def discover_adapter_plugins() -> list[AdapterPluginInfo]:
    entries = metadata.entry_points()
    selected = (
        entries.select(group=ENTRY_POINT_GROUP)
        if hasattr(entries, "select")
        else entries.get(ENTRY_POINT_GROUP, [])
    )
    result: list[AdapterPluginInfo] = []
    for entry in selected:
        distribution = getattr(entry, "dist", None)
        result.append(
            AdapterPluginInfo(
                name=str(entry.name),
                value=str(entry.value),
                distribution=(
                    str(distribution.metadata.get("Name"))
                    if distribution is not None
                    else None
                ),
                version=(
                    str(distribution.version)
                    if distribution is not None
                    else None
                ),
            )
        )
    return sorted(result, key=lambda item: item.name)


def load_adapter_plugins(
    names: tuple[str, ...] | list[str],
    *,
    context: AdapterPluginContext,
) -> list[OneBridgeAdapter]:
    """Load only explicitly configured Python entry-point adapters.

    Entry-point loading executes package code, so discovery alone never imports
    plugins and the loader refuses names not explicitly requested by the
    operator.
    """

    requested = tuple(dict.fromkeys(str(name).strip() for name in names if str(name).strip()))
    if not requested:
        return []

    entries = metadata.entry_points()
    selected = (
        entries.select(group=ENTRY_POINT_GROUP)
        if hasattr(entries, "select")
        else entries.get(ENTRY_POINT_GROUP, [])
    )
    by_name = {str(entry.name): entry for entry in selected}

    missing = [name for name in requested if name not in by_name]
    if missing:
        raise ValueError(
            "configured adapter plugins not installed: " + ", ".join(missing)
        )

    adapters: list[OneBridgeAdapter] = []
    for name in requested:
        loaded = by_name[name].load()
        candidate = loaded(context) if callable(loaded) and not hasattr(loaded, "execute") else loaded
        for attribute in ("name", "version", "health", "capabilities", "execute", "cancel"):
            if not hasattr(candidate, attribute):
                raise TypeError(
                    f"adapter plugin {name!r} missing required attribute {attribute!r}"
                )
        adapters.append(candidate)
    return adapters


def load_context_plugins(
    names: tuple[str, ...] | list[str],
    *,
    context: ContextPluginContext,
) -> dict[str, ContextProvider]:
    """Load explicitly configured context-provider entry points.

    A context provider plugin must return a mapping of knowledge scope names to
    provider objects implementing provider_id and fetch(scope, contract).
    """

    requested = tuple(
        dict.fromkeys(str(name).strip() for name in names if str(name).strip())
    )
    if not requested:
        return {}

    entries = metadata.entry_points()
    selected = (
        entries.select(group=CONTEXT_ENTRY_POINT_GROUP)
        if hasattr(entries, "select")
        else entries.get(CONTEXT_ENTRY_POINT_GROUP, [])
    )
    by_name = {str(entry.name): entry for entry in selected}
    missing = [name for name in requested if name not in by_name]
    if missing:
        raise ValueError(
            "configured context plugins not installed: " + ", ".join(missing)
        )

    providers: dict[str, ContextProvider] = {}
    for name in requested:
        loaded = by_name[name].load()
        value = loaded(context) if callable(loaded) else loaded
        if not isinstance(value, dict):
            raise TypeError(
                f"context plugin {name!r} must return a scope/provider mapping"
            )
        for scope, provider in value.items():
            scope_name = str(scope or "").strip()
            if not scope_name:
                raise TypeError(f"context plugin {name!r} returned an empty scope")
            if scope_name in providers:
                raise ValueError(
                    f"duplicate context scope from plugins: {scope_name}"
                )
            if not hasattr(provider, "provider_id") or not hasattr(provider, "fetch"):
                raise TypeError(
                    f"context plugin {name!r} provider for {scope_name!r} is invalid"
                )
            providers[scope_name] = provider
    return providers
