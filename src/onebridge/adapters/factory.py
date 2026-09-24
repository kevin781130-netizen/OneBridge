from __future__ import annotations

import urllib.parse

from onebridge.config import Settings
from onebridge.network_policy import EgressPolicy, EgressRule

from .base import AdapterRegistry
from .flowise import FlowisePredictionAdapter
from .mock import MockAdapter


def build_adapter_registry(settings: Settings) -> AdapterRegistry:
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
        parsed = urllib.parse.urlsplit(base_url)
        egress_policy = None
        if parsed.scheme == "https":
            if not parsed.hostname:
                raise ValueError("Flowise base URL has no hostname")
            egress_policy = EgressPolicy([
                EgressRule(
                    rule_id="flowise-configured",
                    host=parsed.hostname,
                    path_prefix="/api/v1",
                    methods=("POST",),
                )
            ])

        registry.register(
            FlowisePredictionAdapter(
                base_url=base_url,
                chatflow_id=str(settings.flowise_chatflow_id),
                api_key=settings.flowise_api_key,
                timeout_seconds=settings.flowise_timeout_seconds,
                allowed_override_keys=settings.flowise_allowed_override_keys,
                egress_policy=egress_policy,
            )
        )
    else:
        registry.register(MockAdapter("flowise", ["content"]))

    registry.register(MockAdapter("open_design", ["design"]))
    registry.register(MockAdapter("hermes", ["code", "test_report"]))
    return registry
