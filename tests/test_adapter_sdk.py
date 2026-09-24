from pathlib import Path

import pytest

from onebridge.adapters.base import (
    AdapterHealth,
    AdapterOutput,
    AdapterRequest,
    AdapterResult,
)
from onebridge.sdk import AdapterPluginContext, load_adapter_plugins


class DemoAdapter:
    name = "demo_plugin"
    version = "1.0.0"

    def health(self):
        return AdapterHealth("healthy", "ready")

    def capabilities(self):
        return ["other"]

    def execute(self, request: AdapterRequest):
        return AdapterResult(
            external_task_id="ext",
            outputs=[
                AdapterOutput(
                    kind="other",
                    media_type="text/plain",
                    content=b"ok",
                    filename="result.txt",
                )
            ],
        )

    def cancel(self, external_task_id: str):
        return None


class Entry:
    name = "demo"
    value = "demo:factory"

    def load(self):
        return lambda context: DemoAdapter()


class Entries(list):
    def select(self, **kwargs):
        assert kwargs["group"] == "onebridge.adapters"
        return self


def test_only_explicit_plugin_is_loaded(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "onebridge.sdk.metadata.entry_points",
        lambda: Entries([Entry()]),
    )
    values = load_adapter_plugins(
        ("demo",),
        context=AdapterPluginContext(
            store=object(),
            state_root=tmp_path,
        ),
    )
    assert [item.name for item in values] == ["demo_plugin"]


def test_missing_configured_plugin_fails_closed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "onebridge.sdk.metadata.entry_points",
        lambda: Entries([]),
    )
    with pytest.raises(ValueError):
        load_adapter_plugins(
            ("missing",),
            context=AdapterPluginContext(
                store=object(),
                state_root=tmp_path,
            ),
        )
