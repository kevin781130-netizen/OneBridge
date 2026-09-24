from __future__ import annotations

import json
from uuid import uuid4

from .base import AdapterHealth, AdapterOutput, AdapterRequest, AdapterResult


class MockAdapter:
    def __init__(self, name: str, capabilities: list[str]) -> None:
        self.name = name
        self.version = "mock-1"
        self._capabilities = capabilities

    def health(self) -> AdapterHealth:
        return AdapterHealth("healthy", "mock adapter")

    def capabilities(self) -> list[str]:
        return list(self._capabilities)

    def execute(self, request: AdapterRequest) -> AdapterResult:
        outputs: list[AdapterOutput] = []
        for kind in self._capabilities:
            if kind == "content":
                payload = {
                    "goal": request.goal,
                    "headline": f"Draft for: {request.goal}",
                    "sections": ["Overview", "Benefits", "Call to action"],
                }
                outputs.append(AdapterOutput(kind, "application/json", json.dumps(payload, ensure_ascii=False, indent=2).encode(), "content_spec.json"))
            elif kind == "design":
                html = f"<!doctype html><html><body><main><h1>{request.goal}</h1><p>Mock OneBridge design artifact.</p></main></body></html>"
                outputs.append(AdapterOutput(kind, "text/html", html.encode(), "index.html"))
            elif kind == "code":
                code = f'"""Generated mock implementation for {request.goal}."""\n\ndef main():\n    return "ok"\n'
                outputs.append(AdapterOutput(kind, "text/x-python", code.encode(), "main.py"))
            elif kind == "test_report":
                report = {"status": "passed", "tests": 1, "failures": 0}
                outputs.append(AdapterOutput(kind, "application/json", json.dumps(report, indent=2).encode(), "test_report.json"))
        return AdapterResult(external_task_id=f"mock_{uuid4().hex}", outputs=outputs)

    def cancel(self, external_task_id: str) -> None:
        return None


def default_mock_registry():
    from .base import AdapterRegistry

    registry = AdapterRegistry()
    registry.register(MockAdapter("flowise", ["content"]))
    registry.register(MockAdapter("open_design", ["design"]))
    registry.register(MockAdapter("hermes", ["code", "test_report"]))
    return registry
