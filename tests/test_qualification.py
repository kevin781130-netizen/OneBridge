from onebridge.adapters.base import (
    AdapterHealth,
    AdapterOutput,
    AdapterRequest,
    AdapterResult,
)
from onebridge.qualification import qualify_adapter


class GoodAdapter:
    name = "demo"
    version = "1.2.3"

    def health(self):
        return AdapterHealth("healthy", "ready")

    def capabilities(self):
        return ["content"]

    def execute(self, request: AdapterRequest):
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


class BadAdapter(GoodAdapter):
    def execute(self, request: AdapterRequest):
        return AdapterResult(
            external_task_id="external-1",
            outputs=[],
        )


def test_qualification_passes_valid_adapter():
    result = qualify_adapter(GoodAdapter())
    assert result.passed
    assert result.expected_output_kinds == ("content",)
    assert result.observed_output_kinds == ("content",)


def test_qualification_fails_missing_required_output():
    result = qualify_adapter(BadAdapter())
    assert not result.passed
    assert any("adapter returned no outputs" in item for item in result.errors)
