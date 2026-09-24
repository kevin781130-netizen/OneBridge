from pathlib import Path

from onebridge.adapters.base import (
    AdapterHealth,
    AdapterOutput,
    AdapterRequest,
    AdapterResult,
    AdapterRegistry,
)
from onebridge.compatibility_service import CompatibilityService
from onebridge.db import Database
from onebridge.release_pipeline import AdapterReleasePipeline


class GoodAdapter:
    def __init__(self, name: str, version: str, kind: str):
        self.name = name
        self.version = version
        self.kind = kind

    def health(self):
        return AdapterHealth("healthy", "ready")

    def capabilities(self):
        return [self.kind]

    def execute(self, request: AdapterRequest):
        media_type = (
            "application/json"
            if self.kind in {"content", "test_report"}
            else "text/plain"
        )
        filename = self.kind + (
            ".json"
            if media_type == "application/json"
            else ".txt"
        )
        return AdapterResult(
            external_task_id="external",
            outputs=[
                AdapterOutput(
                    kind=self.kind,
                    media_type=media_type,
                    content=b'{"ok": true}'
                    if media_type == "application/json"
                    else b"ok",
                    filename=filename,
                )
            ],
        )

    def cancel(self, external_task_id: str):
        return None


class BadAdapter(GoodAdapter):
    def execute(self, request: AdapterRequest):
        return AdapterResult(
            external_task_id="external",
            outputs=[],
        )


def database(tmp_path: Path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 'release.db'}")
    db.create_all()
    return db


def test_release_pipeline_promotes_full_set_only_after_all_pass(tmp_path: Path):
    registry = AdapterRegistry()
    registry.register(GoodAdapter("flowise", "1.0", "content"))
    registry.register(GoodAdapter("open_design", "2.0", "design"))
    compatibility = CompatibilityService(database(tmp_path), registry)

    result = AdapterReleasePipeline(compatibility).run(
        ["flowise", "open_design"]
    )

    assert result.passed is True
    assert result.promoted is True
    assert all(item.promoted for item in result.items)
    assert compatibility.get("flowise", "1.0").state == "active"
    assert compatibility.get("open_design", "2.0").state == "active"


def test_release_pipeline_does_not_partially_promote(tmp_path: Path):
    registry = AdapterRegistry()
    registry.register(GoodAdapter("flowise", "1.0", "content"))
    registry.register(BadAdapter("open_design", "2.0", "design"))
    compatibility = CompatibilityService(database(tmp_path), registry)

    result = AdapterReleasePipeline(compatibility).run(
        ["flowise", "open_design"]
    )

    assert result.passed is False
    assert result.promoted is False
    assert compatibility.get("flowise", "1.0").state == "candidate"
    assert compatibility.get("open_design", "2.0").state == "blocked"
