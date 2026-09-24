from pathlib import Path

from onebridge.adapters.base import (
    AdapterHealth,
    AdapterOutput,
    AdapterRegistry,
    AdapterRequest,
    AdapterResult,
)
from onebridge.artifacts import LocalObjectStore
from onebridge.contextforge import ContextDocument, ContextForge, StaticContextProvider
from onebridge.contracts import TaskContract
from onebridge.db import Database
from onebridge.service import OneBridgeService


class CaptureAdapter:
    name = "custom_content"
    version = "1.0.0"

    def __init__(self):
        self.requests = []

    def health(self):
        return AdapterHealth("healthy", "ready")

    def capabilities(self):
        return ["content"]

    def execute(self, request: AdapterRequest):
        self.requests.append(request)
        return AdapterResult(
            external_task_id="external-1",
            outputs=[
                AdapterOutput(
                    kind="content",
                    media_type="application/json",
                    content=b'{"ok": true}',
                    filename="content.json",
                )
            ],
        )

    def cancel(self, external_task_id: str):
        return None


def test_service_routes_to_custom_adapter_with_bounded_context(tmp_path: Path):
    db = Database(f"sqlite:///{tmp_path / 'db.sqlite'}")
    db.create_all()
    adapter = CaptureAdapter()
    registry = AdapterRegistry()
    registry.register(adapter)

    forge = ContextForge(budget_bytes=4096)
    forge.register(
        "brand",
        StaticContextProvider(
            "brand-provider",
            {
                "brand": [
                    ContextDocument(
                        scope="brand",
                        source_id="guide",
                        content="Use concise product language.",
                        priority=1.0,
                    )
                ]
            },
        ),
    )
    service = OneBridgeService(
        db,
        registry,
        LocalObjectStore(tmp_path / "objects"),
        context_forge=forge,
        output_routes={"content": "custom_content"},
    )
    contract = TaskContract.model_validate({
        "input": {"goal": "Write page", "required_outputs": ["content"]},
        "context": {"tenant_id": "tenant", "user_id": "user"},
        "policy": {
            "knowledge_scopes": ["brand"],
            "approval": "none",
        },
    })

    service.submit(contract)
    result = service.run(contract.task_id)

    assert result["status"] == "succeeded"
    assert len(adapter.requests) == 1
    bundle = adapter.requests[0].inputs["context_bundle"]
    assert bundle["requested_scopes"] == ["brand"]
    assert bundle["items"][0]["source_id"] == "guide"
    assert bundle["items"][0]["content"] == "Use concise product language."
