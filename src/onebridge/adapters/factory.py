from __future__ import annotations

import urllib.parse

from onebridge.artifacts import LocalObjectStore, S3ObjectStore
from onebridge.config import Settings
from onebridge.mcp_http import McpHttpClient
from onebridge.network_policy import EgressPolicy, EgressRule

from .base import AdapterRegistry
from .flowise import FlowisePredictionAdapter
from .hermes import HermesCommandAdapter
from .mock import MockAdapter
from .open_design import OpenDesignMCPAdapter


ObjectStore = LocalObjectStore | S3ObjectStore


def _https_policy(
    url: str,
    *,
    rule_id: str,
    methods: tuple[str, ...] = ("POST",),
    path_prefix: str | None = None,
) -> EgressPolicy | None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        return None
    if not parsed.hostname:
        raise ValueError(f"{rule_id} URL has no hostname")
    return EgressPolicy([
        EgressRule(
            rule_id=rule_id,
            host=parsed.hostname,
            path_prefix=path_prefix or parsed.path or "/",
            methods=methods,
        )
    ])


def build_adapter_registry(
    settings: Settings,
    *,
    store: ObjectStore | None = None,
) -> AdapterRegistry:
    registry = AdapterRegistry()

    has_flowise_url = bool(settings.flowise_base_url)
    has_flowise_id = bool(settings.flowise_chatflow_id)
    if has_flowise_url != has_flowise_id:
        raise ValueError(
            "Flowise requires both ONEBRIDGE_FLOWISE_BASE_URL "
            "and ONEBRIDGE_FLOWISE_CHATFLOW_ID"
        )

    if has_flowise_url and has_flowise_id:
        base_url = str(settings.flowise_base_url)
        parsed_flowise = urllib.parse.urlsplit(base_url)
        base_path = parsed_flowise.path.rstrip("/")
        flowise_path = (
            base_path
            if base_path.endswith("/api/v1")
            else f"{base_path}/api/v1"
        )
        registry.register(
            FlowisePredictionAdapter(
                base_url=base_url,
                chatflow_id=str(settings.flowise_chatflow_id),
                api_key=settings.flowise_api_key,
                timeout_seconds=settings.flowise_timeout_seconds,
                allowed_override_keys=settings.flowise_allowed_override_keys,
                egress_policy=_https_policy(
                    base_url,
                    rule_id="flowise-configured",
                    path_prefix=flowise_path or "/api/v1",
                ),
            )
        )
    else:
        registry.register(MockAdapter("flowise", ["content"]))

    if settings.open_design_mcp_url:
        if store is None:
            raise ValueError(
                "Open Design adapter requires an object store"
            )
        open_design_url = str(settings.open_design_mcp_url)
        registry.register(
            OpenDesignMCPAdapter(
                client=McpHttpClient(
                    url=open_design_url,
                    timeout_seconds=settings.open_design_timeout_seconds,
                    egress_policy=_https_policy(
                        open_design_url,
                        rule_id="open-design-configured",
                    ),
                ),
                store=store,
                tool_name=settings.open_design_tool,
            )
        )
    else:
        registry.register(MockAdapter("open_design", ["design"]))

    if settings.hermes_executable:
        if store is None:
            raise ValueError("Hermes adapter requires an object store")
        registry.register(
            HermesCommandAdapter(
                executable=settings.hermes_executable,
                arguments=settings.hermes_args,
                store=store,
                timeout_seconds=settings.hermes_timeout_seconds,
                require_strong_sandbox=settings.hermes_require_strong_sandbox,
                workspace_parent=settings.state_root / "workers",
            )
        )
    else:
        registry.register(
            MockAdapter("hermes", ["code", "test_report"])
        )

    return registry
